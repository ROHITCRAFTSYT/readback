import json

import pytest

from readback.clearance import Clearance, Item
from readback.normalize import canonical
from readback.tagger import train as train_mod
from readback.tagger.decode import decode, spans, tidy
from readback.tagger.evaluate import same, score
from readback.tagger.gold import BlindChanged, GoldExample, blind_info, load_blind, sha256, split_of
from readback.tagger.lock import FileLock, Locked
from readback.tagger.synth import MAX_LEVEL, Generator, dataset

torch = pytest.importorskip("torch")
from readback.tagger.model import Tagger, viterbi  # noqa: E402


def test_canonical_hundreds_after_thousands():
    assert canonical("four thousand five hundred feet") == ["4", "5", "0", "0", "feet"]
    assert canonical("one thousand fourteen") == ["1", "0", "1", "4"]
    assert canonical("two thousand") == ["2", "0", "0", "0"]


def test_decode_spans_and_values():
    toks = "csa 1 d z descend flight level 1 0 0 contact praha 1 2 7 decimal 1 2 5".split()
    tags = (["B-callsign"] + ["I-callsign"] * 3 + ["B-flight_level"] + ["I-flight_level"] * 5
            + ["O", "O"] + ["B-frequency"] + ["I-frequency"] * 6)  # fmt: skip
    assert spans(tags)[0] == ("callsign", 0, 4)
    assert decode(toks, tags) == Clearance(
        "csa 1 d z", [Item("flight_level", "100", "descend"), Item("frequency", "127.125")]
    )


def test_tidy_gives_runway_clearance_its_runway():
    said_apart = Clearance("csa 1", [Item("runway", "13"), Item("cleared_to_land", "")])
    assert tidy(said_apart).items == [Item("cleared_to_land", "13")]
    lineup = Clearance("csa 1", [Item("line_up", "31"), Item("cleared_for_takeoff", "")])
    assert tidy(lineup).items == [Item("line_up", "31"), Item("cleared_for_takeoff", "31")]
    two = Clearance(None, [Item("runway", "22"), Item("hold_short", ""), Item("runway", "31")])
    assert tidy(two).items == two.items  # ambiguous: left alone


def test_generator_is_deterministic_and_labels_match_truth():
    assert [e.tokens for e in dataset(3, 50, 1)] == [e.tokens for e in dataset(3, 50, 1)]
    for level in range(1, MAX_LEVEL + 1):
        for e in dataset(level, 400, f"t{level}"):
            assert len(e.tokens) == len(e.tags)
            if not e.features & {"noise:truncate", "noise:misheard", "noise:correction"}:
                assert same(e.clearance, tidy(e.truth)), (e.tokens, e.clearance, e.truth)


class ScriptedRng:
    """Stands in for random.Random with the answers a test needs, in order."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def randint(self, a, b):
        return self.answers.pop(0)

    def random(self):
        return self.answers.pop(0)


def _cut(text, tags, owner, *answers):
    gen = Generator(3, ScriptedRng(*answers))
    tokens, tags, owner = gen._truncate(text.split(), tags, owner)
    return tokens, decode(tokens, tags)


def test_truncate_labels_only_what_can_still_be_heard():
    tags = ["B-heading", "I-heading", "I-heading", "I-heading",
            "B-callsign", "I-callsign", "I-callsign", "I-callsign"]  # fmt: skip
    owner = [1, 1, 1, 1, 0, 0, 0, 0]
    # cut 1 token at the start, no fragment: "heading" is gone, so "0 3 0" is just digits
    tokens, c = _cut("heading 0 3 0 csa 1 d z", tags[:], owner[:], 1, 0.1, 0.9)
    assert tokens == "0 3 0 csa 1 d z".split()
    assert c == Clearance("csa 1 d z", [])

    tags = ["B-callsign"] + ["I-callsign"] * 3 + ["B-flight_level"] + ["I-flight_level"] * 5
    owner = [0, 0, 0, 0, 1, 1, 1, 1, 1, 1]
    # cut "csa" and half of "one": what is left of the callsign word stays in the callsign
    tokens, c = _cut("csa 1 d z descend flight level 1 0 0", tags, owner, 1, 0.1, 0.1, 2)
    assert tokens[:3] == ["ne", "d", "z"]
    assert c == Clearance("ne d z", [Item("flight_level", "100", "descend")])


def test_generator_rejects_unknown_level():
    import random

    with pytest.raises(ValueError):
        Generator(MAX_LEVEL + 1, random.Random(0))


def test_score():
    gold = [Clearance("a 1", [Item("speed", "180")]), Clearance(None, [])]
    pred = [Clearance("a 1", [Item("speed", "190")]), Clearance(None, [])]
    m = score(pred, gold, [{"kind:speed"}, set()])
    assert m["exact"] == 0.5 and m["callsign"] == 1.0
    assert m["item_precision"] == 0.0 and m["item_recall"] == 0.0
    assert m["feature_error"] == {"kind:speed": 1.0}


def test_viterbi_never_starts_or_jumps_into_a_span():
    import numpy as np

    from readback.tagger.decode import TAGS

    logp = np.full((3, len(TAGS)), -5.0)
    logp[0, TAGS.index("I-speed")] = 0.0  # best alone, but illegal at the start
    logp[1, TAGS.index("I-squawk")] = 0.0  # illegal after anything but squawk
    logp[2, TAGS.index("O")] = 0.0
    best = [TAGS[i] for i in viterbi(logp)]
    assert not best[0].startswith("I-")
    assert best[1] != "I-squawk" or best[0] in ("B-squawk", "I-squawk")


def test_tagger_learns_and_round_trips(tmp_path):
    torch.manual_seed(0)
    data = dataset(1, 600, "unit")
    tagger = Tagger.build([e.tokens for e in data], hidden=32, word_dim=16, char_out=16)
    trainer = train_mod.Trainer.__new__(train_mod.Trainer)
    import random

    trainer.rng = random.Random(0)

    def f1():
        return score(tagger.parse_tokens([e.tokens for e in data]), [e.clearance for e in data])[
            "item_f1"
        ]

    before = f1()
    for _ in range(3):
        trainer._train(tagger, list(data), 3e-3)
    assert f1() > before + 0.2
    path = tmp_path / "t.pt"
    tagger.save(path, {"round": 1})
    back = Tagger.load(path)
    assert back.meta == {"round": 1}
    text = "csa one two three descend flight level one hundred"
    assert back.parse(text) == tagger.parse(text)  # half-precision weights, same answers


def test_grow_adds_words_without_changing_answers():
    import random

    from readback.tagger.model import Tagger

    torch.manual_seed(0)
    tagger = Tagger.build([e.tokens for e in dataset(1, 300, "grow")])
    sentences = [e.tokens for e in dataset(3, 40, "grow-check")]
    before = tagger.predict(sentences)
    new = [["touch", "and", "go", "gusting", "hectopascal", "heli"]] * 3
    assert tagger.grow(new) >= 3
    assert "hectopascal" in tagger.word_ix and tagger.predict(sentences) == before
    words, _, _ = tagger.encode([["hectopascal"]], 0.0, random.Random(0))
    assert words[0, 0].item() == tagger.word_ix["hectopascal"]


def test_trainer_round_resume_and_report(tmp_path, monkeypatch):
    for name, value in {"TRAIN_PER_ROUND": 120, "VAL_N": 60, "PROBE_N": 60, "EPOCHS": 1,
                        "REPLAY_MAX": 30}.items():  # fmt: skip
        monkeypatch.setattr(train_mod, name, value)
    monkeypatch.setattr(train_mod, "load_gold", lambda: [])
    monkeypatch.setattr(train_mod.Trainer, "_baseline", lambda self: {"rules": {}})
    monkeypatch.setattr(train_mod, "dataset", _small_dataset)
    blind = [GoldExample(f"b{i}", " ".join(e.tokens), e.tokens, e.clearance, "blind")
             for i, e in enumerate(dataset(3, 20, "fake-blind"))]  # fmt: skip
    monkeypatch.setattr(train_mod, "load_blind", lambda: blind)
    monkeypatch.setattr(train_mod, "load_seen", lambda: blind[:5])
    monkeypatch.setattr(train_mod, "blind_info", lambda: {
        "transmissions": 20, "airports": {"LSGS": 20}, "frozen": "2026-10-27T00:00:00Z",
        "generator_unchanged": True})  # fmt: skip
    state = tmp_path / "run"
    t = train_mod.Trainer(state)
    entry = t.round()
    assert entry["round"] == 1 and entry["level"] == 1
    assert (state / "champion.pt").exists()
    status = json.loads((state / "status.json").read_text())
    assert status["round"] == 1 and not status["optimal"]
    assert status["champion"]["blind"]["n"] == 20 and status["blind"]["transmissions"] == 20
    assert "blind" in entry["champion"] and "blind" not in entry["candidate"]
    assert status["champion"]["atco2_seen"]["n"] == 5 and "atco2_seen" in entry["champion"]
    again = train_mod.Trainer(state)  # resumes
    assert again.round()["round"] == 2

    from readback.tagger.report import render

    page = render(state)
    assert "# Tagger training" in page and "| 2 | 1 |" in page
    assert "The **blind** test is 20 ATCO2 transmissions (LSGS)" in page


def _small_dataset(level, n, seed, focus=None):
    return dataset(level, min(n, 200), seed, focus)


def test_focus_weights_only_controllable_knobs():
    t = train_mod.Trainer.__new__(train_mod.Trainer)
    focus = t._focus({"kind:speed": 0.4, "kind:squawk": 0.0, "noise:truncate": 0.2,
                      "form:fl_no_keyword": 0.9, "role:pilot": 0.5})  # fmt: skip
    assert set(focus) == {"kind:speed"}
    assert 1.0 < focus["kind:speed"] <= train_mod.FOCUS_MAX


def test_gold_split_is_stable():
    assert split_of("uwb-atcc_X_1_2_PI") == split_of("uwb-atcc_X_1_2_PI")
    assert {split_of(f"id{i}") for i in range(20)} == {"dev", "test"}


def test_blind_labels_must_match_the_frozen_hash(tmp_path):
    labels, corpus, manifest = tmp_path / "b.jsonl", tmp_path / "c.parquet", tmp_path / "m.json"
    assert load_blind(labels, corpus, manifest) == []  # nothing on this machine
    labels.write_text('{"id": "x", "callsign": null, "items": []}\n', encoding="utf-8")
    corpus.write_bytes(b"")
    manifest.write_text(json.dumps({"labels_sha256": "0" * 64}), encoding="utf-8")
    with pytest.raises(BlindChanged):
        load_blind(labels, corpus, manifest)


def test_blind_info_tracks_the_generator(tmp_path):
    synth, manifest = tmp_path / "synth.py", tmp_path / "m.json"
    synth.write_bytes(b"VERSION = 2\r\n")
    manifest.write_text(json.dumps({"generator_sha256": sha256(synth)}), encoding="utf-8")
    assert blind_info(manifest, synth)["generator_unchanged"]
    synth.write_bytes(b"VERSION = 2\n")  # line endings don't count as a change
    assert blind_info(manifest, synth)["generator_unchanged"]
    synth.write_bytes(b"VERSION = 3\n")
    assert not blind_info(manifest, synth)["generator_unchanged"]
    assert blind_info(tmp_path / "none.json", synth) is None


def test_lock_is_exclusive(tmp_path):
    with FileLock(tmp_path / "x.lock"):
        with pytest.raises(Locked):
            FileLock(tmp_path / "x.lock").__enter__()
    with FileLock(tmp_path / "x.lock"):  # released
        pass
