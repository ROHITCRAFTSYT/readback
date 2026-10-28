import random

from readback.keywords import MAX_CHARS, callsign_keywords, fit
from readback.metrics import (
    contains,
    digit_errors,
    discover_designators,
    extract_callsign,
    wer,
)
from readback.normalize import canonical

DESIGNATORS = {"csa", "lufthansa"}


def test_wer_counts_edits():
    assert wer(["a", "b", "c"], ["a", "x", "c"]) == (1, 3)
    assert wer(["a", "b"], []) == (2, 2)
    assert wer([], ["a"]) == (1, 0)


def test_digit_errors_ignore_words():
    ref = canonical("descend flight level one hundred")
    hyp = canonical("descending FL100")
    assert digit_errors(ref, hyp) == (0, 3)
    assert digit_errors(ref, canonical("descend FL110")) == (1, 3)


def test_extract_designator_callsign():
    tokens = canonical("csa one delta zulu descend flight level one hundred")
    assert extract_callsign(tokens, DESIGNATORS) == ["csa", "1", "d", "z"]


def test_extract_callsign_at_end_of_readback():
    tokens = canonical("descending flight level one hundred free speed csa one delta zulu")
    assert extract_callsign(tokens, DESIGNATORS) == ["csa", "1", "d", "z"]


def test_extract_registration():
    tokens = canonical("oscar kilo foxtrot alfa oscar taxi to holding point runway two seven")
    assert extract_callsign(tokens, DESIGNATORS) == ["o", "k", "f", "a", "o"]


def test_waypoint_is_not_a_registration():
    tokens = canonical("confirm proceeding to tango bravo four zero two")
    assert extract_callsign(tokens, DESIGNATORS) is None


def test_contains_needs_contiguous_span():
    assert contains(["csa", "1", "d"], ["roger", "csa", "1", "d", "z"])
    assert not contains(["csa", "1", "d"], ["csa", "1", "x", "d"])


def test_discover_designators():
    texts = [
        "csa one two three climb flight level one hundred",
        "climbing flight level one hundred csa one two three",
        "csa four five contact radar",
        "runway two seven cleared to land",
        "runway two four cleared to land",
        "runway zero six cleared to land",
    ]
    assert discover_designators(texts, min_count=3) == {"csa"}


def test_callsign_keywords_include_target_and_distractors():
    target = ["csa", "1", "d", "z"]
    pool = [target, ["lufthansa", "2", "a"], ["o", "k", "f", "a", "o"], ["csa", "4", "5"]]
    phrases = callsign_keywords(target, pool, 2, random.Random(0))
    assert "csa one delta zulu" in phrases
    assert len(phrases) == 3


def test_long_callsigns_are_cut_at_a_word_boundary():
    assert fit("air portugal nine three one charlie") == "air portugal nine three one"
    assert fit("csa one delta zulu") == "csa one delta zulu"
    assert len(fit("x" * 40)) == MAX_CHARS
