"""TRAINING.md: the training run as a page anyone can read on GitHub."""

from __future__ import annotations

import json
from pathlib import Path

LEVEL_NAMES = {
    1: "standard phraseology",
    2: "everyday variation",
    3: "real radio: cut-offs, hesitations, misheard words",
    4: "stress: every kind of noise at once",
}


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.1f}%"


def render(state_dir: Path) -> str:
    st = json.loads((state_dir / "status.json").read_text(encoding="utf-8"))
    rounds = [e for e in read_jsonl(state_dir / "log.jsonl") if "round" in e]
    checks = read_jsonl(state_dir / "checks.jsonl")
    champ, rules = st["champion"], st["baseline"]["rules"]
    hours = st["train_seconds"] / 3600

    lines = [
        "# Tagger training",
        "",
        "Readback's own clearance parser, a small BiLSTM tagger (`src/readback/tagger`), trains",
        "itself here on synthetic radio traffic and is checked against hand-labeled real",
        "transmissions it never trains on. This page is rewritten after every round by",
        "`scripts/train_tagger.py`; the live dashboard is the Training tab of",
        "`uv run readback-ui`.",
        "",
        f"- **State:** {'optimal' if st['optimal'] else 'training'}, round {st['round']}, "
        f"level {st['level']} ({LEVEL_NAMES.get(st['level'], '')})",
        f"- **Champion:** round {champ['round']}",
        f"- **Trained for:** {hours:.1f} h on {st['examples_seen']:,} examples "
        f"(started {st['started'][:10]}, updated {st['updated'][:16].replace('T', ' ')} UTC)",
        "",
        "## Champion vs the rule-based parser",
        "",
        "| Test | Tagger exact | Rules exact | Tagger item F1 | Rules item F1 |",
        "|---|---|---|---|---|",
    ]
    for key, name in (
        ("gold_test", "Real radio, held-out half"),
        ("gold_dev", "Real radio, dev half"),
    ):
        if key in champ and key in rules:
            c, r = champ[key], rules[key]
            lines.append(
                f"| {name} (n={c['n']}) | {_pct(c['exact'])} | {_pct(r['exact'])} | "
                f"{_pct(c['item_f1'])} | {_pct(r['item_f1'])} |"
            )
    lvl_key = f"val_{st['level']}"
    if lvl_key in rules:
        c, r = champ["val"], rules[lvl_key]
        lines.append(
            f"| Synthetic level {st['level']} (n={c['n']}) | {_pct(c['exact'])} | "
            f"{_pct(r['exact'])} | {_pct(c['item_f1'])} | {_pct(r['item_f1'])} |"
        )
    lines += [
        "",
        "*Exact*: callsign and every item right. *Item F1*: over (kind, value) pairs. The real",
        "transmissions are 287 UWB-ATCC clips from Prague, labeled by hand "
        "(`data/gold/`); the held-out half never influences training.",
        "",
        "## Levels",
        "",
    ]
    passed = {p["level"]: p for p in st["levels_passed"]}
    for lvl, name in LEVEL_NAMES.items():
        mark = (
            f"passed in round {passed[lvl]['round']}"
            if lvl in passed
            else ("current" if lvl == st["level"] else "")
        )
        lines.append(f"{lvl}. {name}" + (f": {mark}" if mark else ""))
    if st["focus"]:
        lines += [
            "",
            "## Current focus",
            "",
            "Practice is weighted toward what the champion got wrong in its last probe:",
            "",
        ]
        for f, w in sorted(st["focus"].items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"- `{f}` ×{w}")
    if checks:
        lines += ["", "## Daily fresh checks", "",
                  "A new date-seeded level-3 test set every day, plus the real transmissions.", "",
                  "| Date | Champion round | Synthetic exact | Real (held-out) item F1 |",
                  "|---|---|---|---|"]  # fmt: skip
        for c in checks[-14:]:
            lines.append(
                f"| {c['seed']} | {c.get('champion_round', '')} | {_pct(c['synthetic']['exact'])} "
                f"| {_pct(c.get('gold_test', {}).get('item_f1'))} |"
            )
    lines += ["", "## Recent rounds", "",
              "| Round | Level | Candidate exact | Real dev F1 | Promoted | Note |",
              "|---|---|---|---|---|---|"]  # fmt: skip
    for e in rounds[-15:][::-1]:
        cand = e["candidate"]
        lines.append(
            f"| {e['round']} | {e['level']} | {_pct(cand['val']['exact'])} | "
            f"{_pct(cand.get('gold_dev', {}).get('item_f1'))} | "
            f"{'yes' if e['promoted'] else ''} | {e.get('event') or ''} |"
        )
    return "\n".join(lines) + "\n"
