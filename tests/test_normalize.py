import pytest

from readback.normalize import canonical, spoken


@pytest.mark.parametrize(
    ("ref", "hyp"),
    [
        ("csa one delta zulu descend flight level one hundred", "CSA1DZ descend FL100"),
        ("csa one delta zulu", "CSA 1DZ"),
        ("initial climb five thousand feet", "initial climb 5,000 feet"),
        ("qnh one zero one four", "QNH 1014"),
        ("contact one one nine decimal six zero five", "contact 119.605"),
        ("contact one one nine point six zero five", "contact 119.605"),
        ("oscar kilo foxtrot alfa oscar", "OK-FAO"),
        ("oscar kilo triple hotel", "oscar kilo hotel hotel hotel"),
        ("runway two seven", "runway 27"),
        ("aero flot two one", "Aeroflot 21"),
        ("wind two four zero degrees twenty five knots", "wind 240 degrees 25 knots"),
        ("ok", "Okay."),
        ("niner", "nine"),
        ("niner thousand", "nineer thousand"),  # how Voice Transcribe spells it
        ("juliett", "Juliet"),
    ],
)
def test_reference_and_written_forms_match(ref, hyp):
    assert canonical(ref) == canonical(hyp)


def test_canonical_tokens():
    assert canonical("csa one delta zulu descend flight level one hundred") == [
        "csa", "1", "d", "z", "descend", "flight", "level", "1", "0", "0",
    ]  # fmt: skip


def test_acronyms_stay_words():
    assert canonical("ILS runway 06") == ["ils", "runway", "0", "6"]


def test_disfluency_markers_are_dropped():
    assert canonical("roger (er) thank you") == ["roger", "thank", "you"]


def test_spoken_round_trip():
    tokens = canonical("CSA1DZ")
    assert spoken(tokens) == "csa one delta zulu"
    assert canonical(spoken(tokens)) == tokens
