"""Continuous, self-directed training for the clearance tagger.

Each round:

1. Draw fresh synthetic transmissions at the current level, more of whatever the champion
   currently gets wrong (`focus`), plus a replay of examples it recently failed and a slice
   of easier levels so it doesn't forget them.
2. Train a candidate from the champion's weights.
3. Score the candidate on a fixed validation set for the level. It replaces the champion only
   if it scores higher there and doesn't lose ground on the real-radio dev set.
4. Probe the champion with new examples to find its weak spots. Those set the next round's
   focus and replay.
5. Level up once the champion gets LEVEL_PASS of the level's validation set exactly right.

The champion is **optimal** when it has passed level 3 and, on the held-out half of the
hand-labeled real transmissions, reaches GOLD_BAR item F1 and beats the rule-based parser.
Training carries on after that on level 4, a stress level with every kind of noise at once,
under the same promotion rule.

The ATCO2 blind test (`gold.load_blind`) is scored for every new champion and reported next
to the rest, but nothing here reads it to decide anything: not promotion, focus, levels or
optimality.

All state lives in one directory (status.json, log.jsonl, champion.pt), so a run can be
stopped and resumed at any round.
"""

from __future__ import annotations

import json
import random
import time
from datetime import UTC, datetime
from pathlib import Path

import torch

from readback import rules
from readback.tagger.decode import TAGS, tidy
from readback.tagger.evaluate import same, score
from readback.tagger.gold import blind_info, load_blind, load_gold
from readback.tagger.model import Tagger
from readback.tagger.synth import MAX_LEVEL, TELEPHONY, VERSION, dataset

LEVEL_PASS = 0.97
GOLD_BAR = 0.90
TRAIN_PER_ROUND = 8000
EASIER_SHARE = 0.2
REPLAY_MAX = 2500
VAL_N = 2000
PROBE_N = 2000
EPOCHS = 2
BATCH = 64
LR = 2e-3
MIN_LR = 1.25e-4
DEV_SLACK = 0.02  # a candidate may not lose more than this much real-radio dev F1
FOCUS_MAX = 4.0
TAG_IX = {t: i for i, t in enumerate(TAGS)}


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slim(m: dict) -> dict:
    """Headline numbers of a score, rounded for the log."""
    keep = ("n", "exact", "item_precision", "item_recall", "item_f1", "callsign")
    out = {k: round(m[k], 4) if isinstance(m[k], float) else m[k] for k in keep}
    out["kind_f1"] = {k: round(v, 3) for k, v in m["kind_f1"].items()}
    return out


class Trainer:
    def __init__(self, state_dir: Path, seed: int = 0):
        self.dir = state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.status_path = self.dir / "status.json"
        self.log_path = self.dir / "log.jsonl"
        self.champion_path = self.dir / "champion.pt"
        self.rng = random.Random(seed)
        self.replay: list = []
        gold = load_gold()
        self.gold = {s: [g for g in gold if g.split == s] for s in ("dev", "test")}
        self.blind = load_blind()
        self._val: dict[int, list] = {}
        if self.status_path.exists() and self.champion_path.exists():
            self.status = json.loads(self.status_path.read_text(encoding="utf-8"))
            self.champion = Tagger.load(self.champion_path)
            if self.status.get("generator_version") != VERSION:
                self._rescore(f"generator changed to version {VERSION}")
            elif self.blind and "blind" not in self.status["champion"]:
                self._rescore("blind test added")
        else:
            self._start()

    # -- setup --------------------------------------------------------------------------
    def _start(self) -> None:
        sentences = [e.tokens for lvl in (1, 2, 3) for e in dataset(lvl, 8000, f"vocab:{lvl}")]
        self.champion = Tagger.build(sentences)
        self.status = {
            "started": now(),
            "generator_version": VERSION,
            "round": 0,
            "level": 1,
            "levels_passed": [],
            "optimal": False,
            "optimal_since": None,
            "lr": LR,
            "stagnant": 0,
            "focus": {},
            "examples_seen": 0,
            "train_seconds": 0.0,
            "champion": {"round": 0},
            "settings": {
                "level_pass": LEVEL_PASS,
                "gold_bar": GOLD_BAR,
                "train_per_round": TRAIN_PER_ROUND,
                "epochs": EPOCHS,
            },  # fmt: skip
        }
        self.status["baseline"] = self._baseline()
        self.status["champion"].update(self._evaluate(self.champion), **self._blind(self.champion))
        self._save_champion()
        self._write_status()

    def _baseline(self) -> dict:
        """The rule-based parser on the same tests, as the bar to beat."""
        from readback.datasets import load_transcripts
        from readback.metrics import discover_designators
        from readback.tagger.gold import CORPUS

        out = {}
        if CORPUS.exists():
            designators = discover_designators(t.text for t in load_transcripts(CORPUS, "uwb"))
            for split, rows in self.gold.items():
                preds = [tidy(rules.parse(g.text, designators)) for g in rows]
                out[f"gold_{split}"] = _slim(score(preds, [g.clearance for g in rows]))
        if self.blind:
            from readback.tagger.gold import BLIND_CORPUS

            designators = discover_designators(
                t.text for t in load_transcripts(BLIND_CORPUS, "atco2-1h")
            )
            preds = [tidy(rules.parse(g.text, designators)) for g in self.blind]
            out["blind"] = _slim(score(preds, [g.clearance for g in self.blind]))
        designators = {w for w in TELEPHONY if " " not in w}
        for lvl in range(1, MAX_LEVEL + 1):
            val = self.val(lvl)
            preds = [tidy(rules.parse(" ".join(e.tokens), designators)) for e in val]
            out[f"val_{lvl}"] = _slim(score(preds, [e.clearance for e in val]))
        return {"rules": out}

    def val(self, level: int) -> list:
        if level not in self._val:
            self._val[level] = dataset(level, VAL_N, f"val:{level}:{VERSION}")
        return self._val[level]

    # -- evaluation ---------------------------------------------------------------------
    def _score(self, tagger: Tagger, examples: list, features: bool = False) -> dict:
        preds = tagger.parse_tokens([e.tokens for e in examples])
        golds = [e.clearance for e in examples]
        return score(preds, golds, [e.features for e in examples] if features else None)

    def _evaluate(self, tagger: Tagger) -> dict:
        out = {"val": _slim(self._score(tagger, self.val(self.status["level"])))}
        for split, rows in self.gold.items():
            if rows:
                out[f"gold_{split}"] = _slim(self._score(tagger, rows))
        return out

    def _blind(self, tagger: Tagger) -> dict:
        """The blind score, for reporting only."""
        return {"blind": _slim(self._score(tagger, self.blind))} if self.blind else {}

    def _rescore(self, why: str) -> None:
        self.status["generator_version"] = VERSION
        self.status["champion"].update(self._evaluate(self.champion), **self._blind(self.champion))
        self.status["baseline"] = self._baseline()
        self._log({"event": "rescore", "why": why, "champion": self.status["champion"]})
        self._write_status()

    # -- training -----------------------------------------------------------------------
    def _train(self, tagger: Tagger, examples: list, lr: float) -> float:
        net = tagger.net
        net.train()
        opt = torch.optim.Adam(net.parameters(), lr=lr)
        losses = []
        for _ in range(EPOCHS):
            self.rng.shuffle(examples)
            for k in range(0, len(examples), BATCH):
                batch = examples[k : k + BATCH]
                words, chars, shapes = tagger.encode([e.tokens for e in batch], 0.1, self.rng)
                target = torch.full(words.shape, -100, dtype=torch.long)
                for i, e in enumerate(batch):
                    target[i, : len(e.tags)] = torch.tensor([TAG_IX[t] for t in e.tags])
                logits = net(words, chars, shapes)
                loss = torch.nn.functional.cross_entropy(
                    logits.reshape(-1, len(TAGS)), target.reshape(-1), ignore_index=-100
                )
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                opt.step()
                losses.append(loss.item())
        return sum(losses[-50:]) / max(1, len(losses[-50:]))

    def _copy(self, tagger: Tagger) -> Tagger:
        twin = Tagger(tagger.words, tagger.chars, tagger.config)
        twin.net.load_state_dict(tagger.net.state_dict())
        return twin

    def _focus(self, feature_error: dict[str, float]) -> dict[str, float]:
        """More practice where the champion fails more than average. Only knobs the generator
        can turn (item kinds and noise types) get a weight."""
        knobs = {f: e for f, e in feature_error.items() if f.startswith(("kind:", "noise:"))}
        if not knobs:
            return {}
        mean = sum(knobs.values()) / len(knobs)
        if mean == 0:
            return {}
        return {f: round(min(FOCUS_MAX, max(1.0, e / mean)), 2) for f, e in knobs.items()
                if e > mean}  # fmt: skip

    def round(self) -> dict:
        st = self.status
        t0 = time.time()
        st["round"] += 1
        rnd, level = st["round"], st["level"]

        data = dataset(level, TRAIN_PER_ROUND, f"train:{rnd}", st["focus"])
        if level > 1:
            n_easy = int(TRAIN_PER_ROUND * EASIER_SHARE)
            data += dataset(self.rng.randint(1, level - 1), n_easy, f"easy:{rnd}")
        data += self.replay
        candidate = self._copy(self.champion)
        loss = self._train(candidate, data, st["lr"])
        train_s = time.time() - t0

        cand = self._evaluate(candidate)
        champ = st["champion"]
        dev_ok = "gold_dev" not in cand or (
            cand["gold_dev"]["item_f1"] >= champ.get("gold_dev", {}).get("item_f1", 0) - DEV_SLACK
        )
        better = (cand["val"]["exact"], cand["val"]["item_f1"]) > (
            champ["val"]["exact"], champ["val"]["item_f1"],
        )  # fmt: skip
        promoted = better and dev_ok
        if promoted:
            self.champion = candidate
            st["champion"] = {"round": rnd, **cand, **self._blind(candidate)}
            st["stagnant"] = 0
            self._save_champion()
        else:
            st["stagnant"] += 1
            if st["stagnant"] % 4 == 0:
                st["lr"] = max(MIN_LR, st["lr"] / 2)

        # Probe the champion on fresh examples: its mistakes become next round's practice.
        probe = dataset(level, PROBE_N, f"probe:{rnd}", st["focus"])
        preds = self.champion.parse_tokens([e.tokens for e in probe])
        m = score(preds, [e.clearance for e in probe], [e.features for e in probe])
        st["focus"] = self._focus(m["feature_error"])
        wrong = [e for e, p in zip(probe, preds, strict=True) if not same(p, e.clearance)]
        self.replay = (wrong * 3)[:REPLAY_MAX]

        event = None
        if st["champion"]["val"]["exact"] >= LEVEL_PASS and level < 3:
            st["levels_passed"].append({"level": level, "round": rnd, "at": now()})
            st["level"] = level + 1
            st["lr"], st["stagnant"] = LR / 2, 0
            st["champion"].update(self._evaluate(self.champion))
            event = f"passed level {level}"
        elif (
            st["champion"]["val"]["exact"] >= LEVEL_PASS
            and level == 3
            and not any(p["level"] == 3 for p in st["levels_passed"])
        ):
            st["levels_passed"].append({"level": 3, "round": rnd, "at": now()})
            event = "passed level 3"
        was_optimal = st["optimal"]
        st["optimal"] = self.is_optimal()
        if st["optimal"] and not was_optimal:
            st["optimal_since"] = now()
            event = (event + "; " if event else "") + "optimal"
        if st["optimal"] and st["level"] == 3:
            st["level"] = MAX_LEVEL
            st["lr"], st["stagnant"] = LR / 2, 0
            st["champion"].update(self._evaluate(self.champion))
            event = (event + "; " if event else "") + f"moved on to stress level {MAX_LEVEL}"

        st["examples_seen"] += len(data) * EPOCHS
        st["train_seconds"] = round(st["train_seconds"] + train_s, 1)
        entry = {
            "round": rnd, "at": now(), "level": level, "loss": round(loss, 4),
            "lr": st["lr"], "promoted": promoted, "candidate": cand,
            "champion_round": st["champion"]["round"],
            "champion": {k: st["champion"][k].get(m) for k, m in (
                ("val", "exact"), ("gold_dev", "item_f1"), ("gold_test", "item_f1"),
                ("blind", "item_f1"))
                if k in st["champion"]},
            "probe_exact": round(m["exact"], 4),
            "weak": sorted(st["focus"], key=st["focus"].get, reverse=True)[:5],
            "examples": len(data), "seconds": round(time.time() - t0, 1), "event": event,
        }  # fmt: skip
        self._log(entry)
        self._write_status()
        return entry

    def is_optimal(self) -> bool:
        st = self.status
        test = st["champion"].get("gold_test")
        rules_test = st["baseline"]["rules"].get("gold_test")
        return bool(
            any(p["level"] == 3 for p in st["levels_passed"])
            and test
            and rules_test
            and test["item_f1"] >= GOLD_BAR
            and test["item_f1"] > rules_test["item_f1"]
        )

    # -- persistence --------------------------------------------------------------------
    def _save_champion(self) -> None:
        self.champion.save(self.champion_path, {"round": self.status["round"],
                                                "generator_version": VERSION})  # fmt: skip

    def _write_status(self) -> None:
        self.status["updated"] = now()
        if self.blind and (info := blind_info()):
            keep = ("transmissions", "airports", "frozen", "generator_unchanged")
            self.status["blind"] = {k: info[k] for k in keep}
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.status, indent=2), encoding="utf-8")
        tmp.replace(self.status_path)

    def _log(self, entry: dict) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def fresh_check(tagger: Tagger, seed: str) -> dict:
    """A brand-new level-3 test set (date-seeded) plus the real-radio gold and blind sets."""
    exs = dataset(3, VAL_N, f"check:{seed}")
    preds = tagger.parse_tokens([e.tokens for e in exs])
    out = {"seed": seed, "synthetic": _slim(score(preds, [e.clearance for e in exs]))}
    gold = load_gold()
    for split in ("dev", "test"):
        rows = [g for g in gold if g.split == split]
        if rows:
            p = tagger.parse_tokens([g.tokens for g in rows])
            out[f"gold_{split}"] = _slim(score(p, [g.clearance for g in rows]))
    if blind := load_blind():
        p = tagger.parse_tokens([g.tokens for g in blind])
        out["blind"] = _slim(score(p, [g.clearance for g in blind]))
    return out


def gold_mistakes(tagger: Tagger, split: str = "dev") -> list[dict]:
    """What the tagger gets wrong on real radio, for reading (dev split only by default)."""
    rows = [g for g in load_gold() if g.split == split]
    preds = tagger.parse_tokens([g.tokens for g in rows])
    return [
        {"id": g.id, "text": " ".join(g.tokens), "gold": g.clearance.to_dict(), "pred": p.to_dict()}
        for g, p in zip(rows, preds, strict=True)
        if not same(p, g.clearance)
    ]
