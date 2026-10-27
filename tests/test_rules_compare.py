import pytest

from readback.clearance import Clearance, Item
from readback.compare import compare, same_callsign
from readback.rules import parse

DESIGNATORS = {"csa", "lufthansa", "skytravel"}


def items(text: str) -> list[tuple[str, str, str]]:
    return [(i.kind, i.value, i.qualifier) for i in parse(text, DESIGNATORS).items]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("descend flight level one hundred", [("flight_level", "100", "descend")]),
        ("climb flight level zero eight zero", [("flight_level", "80", "climb")]),
        ("initial climb five thousand feet", [("altitude", "5000", "climb")]),
        ("turn left heading two four zero", [("heading", "240", "left")]),
        ("squawk four seven two one", [("squawk", "4721", "")]),
        ("qnh one zero one four", [("qnh", "1014", "")]),
        ("contact radar one two zero decimal five two five", [("frequency", "120.525", "")]),
        ("reduce speed one eight zero knots", [("speed", "180", "")]),
        ("runway two seven left cleared to land", [("cleared_to_land", "27l", "")]),
        ("cleared for takeoff runway three one", [("cleared_for_takeoff", "31", "")]),
        ("line up runway one three", [("line_up", "13", "")]),
        ("hold short of runway two four", [("hold_short", "24", "")]),
        ("proceed direct baltu", [("direct_to", "baltu", "")]),
    ],
)
def test_parse_items(text, expected):
    assert items(text) == expected


def test_wind_is_not_a_speed():
    assert items("wind two four zero degrees one five knots") == []


def test_cut_off_values_are_ignored():
    assert items("cleared to land runway three") == [("cleared_to_land", "", "")]
    assert items("speed one five") == []


def test_callsign_digits_do_not_extend_frequency():
    assert items("ground one two one decimal nine two six nine six sierra") == [
        ("frequency", "121.92", "")
    ]


def test_parse_callsign():
    assert parse("csa one delta zulu descend flight level one hundred", DESIGNATORS).callsign == (
        "csa 1 d z"
    )


def _cmp(instruction: str, readback: str):
    return [
        (f.type, f.kind, f.expected, f.heard)
        for f in compare(parse(instruction, DESIGNATORS), parse(readback, DESIGNATORS))
    ]


def test_correct_readback_has_no_findings():
    assert (
        _cmp(
            "csa one delta zulu descend flight level one hundred",
            "descending flight level one hundred csa one delta zulu",
        )
        == []
    )


def test_wrong_flight_level():
    assert _cmp(
        "csa one delta zulu descend flight level one hundred",
        "descending flight level one one zero csa one delta zulu",
    ) == [("wrong_value", "flight_level", "100", "110")]


def test_wrong_callsign():
    instruction = "csa one delta zulu squawk one two three four"
    readback = "squawk one two three four csa one delta x-ray"
    assert _cmp(instruction, readback) == [("wrong_callsign", "callsign", "csa 1 d z", "csa 1 d x")]


def test_omitted_item_is_a_warning():
    findings = compare(
        parse("climb flight level one two zero squawk one two three four", DESIGNATORS),
        parse("climbing flight level one two zero", DESIGNATORS),
    )
    assert [(f.type, f.kind, f.severity) for f in findings] == [("missing", "squawk", "warning")]


def test_runway_compared_across_clearance_kinds():
    assert _cmp("runway three one cleared to land", "runway three eight") == [
        ("wrong_value", "cleared_to_land", "31", "38")
    ]
    assert (
        _cmp("runway three one cleared to land", "cleared to land three one runway three one") == []
    )


def test_landing_clearance_without_runway_in_readback_is_fine():
    assert _cmp("runway three one cleared to land", "cleared to land csa one two") == []


def test_frequency_prefix_is_partial_warning():
    instr = Clearance("csa 1 2", [Item("frequency", "121.9")])
    readback = Clearance("csa 1 2", [Item("frequency", "121.92")])
    [finding] = compare(instr, readback)
    assert (finding.type, finding.severity) == ("partial", "warning")


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("csa 1 d z", "csa 1 d z", True),
        ("csa 1 d z", "1 d z", True),
        ("o k f a o", "f a o", True),
        ("csa 1 d z", "csa 1 d x", False),
        ("csa 1 d z", "lufthansa 1 d z", True),
        ("o k f a o", "x k f a o", False),
    ],
)
def test_same_callsign(a, b, same):
    assert same_callsign(a, b) is same
