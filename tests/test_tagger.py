import json

import pytest

from readback.clearance import Clearance, Item
from readback.normalize import canonical
from readback.tagger import train as train_mod
from readback.tagger.decode import decode, spans, tidy
from readback.tagger.evaluate import same, score
from readback.tagger.gold import split_of
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


def test_trainer_round_resume_and_report(tmp_path, monkeypatch):
    for name, value in {"TRAIN_PER_ROUND": 120, "VAL_N": 60, "PROBE_N": 60, "EPOCHS": 1,
                        "REPLAY_MAX": 30}.items():  # fmt: skip
        monkeypatch.setattr(train_mod, name, value)
    monkeypatch.setattr(train_mod, "load_gold", lambda: [])
    monkeypatch.setattr(train_mod.Trainer, "_baseline", lambda self: {"rules": {}})
    monkeypatch.setattr(train_mod, "dataset", _small_dataset)
    state = tmp_path / "run"
    t = train_mod.Trainer(state)
    entry = t.round()
    assert entry["round"] == 1 and entry["level"] == 1
    assert (state / "champion.pt").exists()
    status = json.loads((state / "status.json").read_text())
    assert status["round"] == 1 and not status["optimal"]
    again = train_mod.Trainer(state)  # resumes
    assert again.round()["round"] == 2

    from readback.tagger.report import render

    page = render(state)
    assert "# Tagger training" in page and "| 2 | 1 |" in page


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


def test_lock_is_exclusive(tmp_path):
    with FileLock(tmp_path / "x.lock"):
        with pytest.raises(Locked):
            FileLock(tmp_path / "x.lock").__enter__()
    with FileLock(tmp_path / "x.lock"):  # released
        pass
