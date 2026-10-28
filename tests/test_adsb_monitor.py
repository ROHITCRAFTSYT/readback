import httpx

from readback import rules
from readback.adsb import (
    KNOWN_DESIGNATORS,
    ClearanceTrack,
    OpenSky,
    StateVector,
    spoken_callsign,
)
from readback.clearance import Clearance, Item, plausible
from readback.monitor import Monitor, Transmission, by_number, describe, infer_role, snap

T0 = 1_000_000.0


def sv(t, alt=None, vr=0.0, track=None, squawk=None, callsign="IGO961"):
    return StateVector("800001", callsign, T0 + t, 13.0, 77.0, alt, 250.0, track, vr, False, squawk)


def test_from_opensky_converts_units():
    row = ["80177c", "AXB2508 ", "India", 10, 11, 77.85, 13.37, 1000.0, False, 100.0, 333.6,
           5.08, None, 1050.0, "4721", False, 0]  # fmt: skip
    s = StateVector.from_opensky(row)
    assert (s.callsign, s.time, s.lat, s.lon, s.squawk) == ("AXB2508", 11.0, 13.37, 77.85, "4721")
    assert round(s.altitude_ft) == 3281
    assert round(s.ground_speed_kt) == 194
    assert round(s.vertical_rate_fpm) == 1000


def test_spoken_callsign():
    assert spoken_callsign("SWR123") == "swiss 1 2 3"
    assert spoken_callsign("CSA1DZ") == "csa 1 d z"
    assert spoken_callsign("IGO961") == "ifly 9 6 1"
    assert spoken_callsign("VTFIS") == "v t f i s"


def test_opensky_anonymous_and_oauth():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("Authorization")))
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1800})
        row = ["abc", "IGO961", "India", 1, 1, 77.7, 13.2, 3000.0, False, 120.0, 90.0, 0.0,
               None, 3000.0, None, False, 0]  # fmt: skip
        return httpx.Response(200, json={"time": 1, "states": [row]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    states = OpenSky(client).states((12.0, 77.0, 14.0, 78.0))
    assert [s.callsign for s in states] == ["IGO961"]
    assert seen[-1] == ("/api/states/all", None)

    OpenSky(client, client_id="id", client_secret="secret").states((12.0, 77.0, 14.0, 78.0))
    assert seen[-2][0].endswith("/token")
    assert seen[-1] == ("/api/states/all", "Bearer tok")


def test_level_bust_on_descent():
    track = ClearanceTrack(Item("flight_level", "100", "descend"), T0)
    assert track.check(sv(5, alt=14000, vr=-1800)) is None
    assert track.check(sv(90, alt=10100, vr=-1800)) is None
    dev = track.check(sv(110, alt=9500, vr=-1800))
    assert (dev.kind, dev.severity) == ("level_bust", "error")


def test_leaving_cleared_level_is_a_bust():
    track = ClearanceTrack(Item("flight_level", "100", "descend"), T0)
    track.check(sv(5, alt=12000, vr=-1800))
    assert track.check(sv(80, alt=10000)) is None
    assert track.check(sv(200, alt=10500, vr=600)).kind == "level_bust"


def test_level_off_short_and_wrong_direction():
    short = ClearanceTrack(Item("altitude", "5000", "descend"), T0)
    short.check(sv(5, alt=8000, vr=-1800))
    assert short.check(sv(60, alt=6000)) is None
    assert short.check(sv(130, alt=6000)).kind == "level_off"

    wrong = ClearanceTrack(Item("flight_level", "100", "descend"), T0)
    wrong.check(sv(5, alt=12000))
    assert wrong.check(sv(70, alt=12500, vr=1500)).kind == "wrong_vertical_direction"


def test_heading_and_squawk():
    hdg = ClearanceTrack(Item("heading", "240", "left"), T0)
    assert hdg.check(sv(30, track=200)) is None  # still turning
    assert hdg.check(sv(60, track=250)) is None  # within wind tolerance
    assert hdg.check(sv(60, track=200)).kind == "heading"

    sq = ClearanceTrack(Item("squawk", "4721"), T0)
    assert sq.check(sv(60, squawk="4721")) is None
    assert sq.check(sv(60, squawk="4712")).kind == "squawk"


def _monitor():
    return Monitor(lambda text: rules.parse(text, KNOWN_DESIGNATORS))


def test_describe():
    assert describe("flight_level", "80") == "FL080"
    assert describe("callsign", "lufthansa 5 0 4") == "LUFTHANSA 504"
    assert describe("callsign", "o k f a o") == "OKFAO"


def test_infer_role():
    parse = _monitor().parse
    text = "ifly nine six one descend flight level one hundred"
    assert infer_role(text, parse(text)) == "controller"
    text = "descending flight level one hundred ifly nine six one"
    assert infer_role(text, parse(text)) == "pilot"
    text = "ifly nine six one ready for departure runway two seven"
    assert infer_role(text, parse(text)) == "pilot"
    text = "ifly nine six one say request"
    assert infer_role(text, parse(text)) == "controller"


def test_misparsed_callsign_recovered_from_the_flight_number():
    m = _monitor()
    m.traffic = {"ifly 9 6 1", "swiss 1 2 3"}
    m.on_transmission(Transmission(T0, "ifly nine six one, squawk four three one two"))
    # "squawk" lost to the radio: "bravo four three one three" reads as a callsign.
    clearance, role, [alert] = m.on_transmission(
        Transmission(T0 + 5, "nine six one, one bravo four three one three", id="tx2")
    )
    assert clearance.callsign == "ifly 9 6 1" and role == "pilot" and "tx2" in m.snapped
    assert alert.kind == "squawk" and alert.callsign == "ifly 9 6 1"
    assert by_number("one two three and nine six one", m.traffic) is None  # two aircraft


def test_reply_that_opens_with_the_callsign_is_the_pilot():
    m = _monitor()
    m.on_transmission(Transmission(T0, "ifly nine six one, squawk four three one two"))
    _, role, [alert] = m.on_transmission(
        Transmission(T0 + 5, "ifly nine six one, squawk four three one three")
    )
    assert (role, alert.kind, alert.heard) == ("pilot", "squawk", "4313")
    # The next instruction to the same aircraft, once answered, is the controller again.
    _, role, _ = m.on_transmission(Transmission(T0 + 9, "ifly nine six one, squawk two zero"))
    assert role == "controller"


def test_monitor_raises_readback_error_then_track_bust():
    m = _monitor()
    _, role, alerts = m.on_transmission(
        Transmission(T0, "ifly nine six one, descend flight level one hundred", id="tx1")
    )
    assert (role, alerts) == ("controller", [])
    _, role, alerts = m.on_transmission(
        Transmission(T0 + 6, "descending flight level niner zero, ifly nine six one", id="tx2")
    )
    assert role == "pilot"
    assert [(a.kind, a.severity, a.expected, a.heard, a.ref) for a in alerts] == [
        ("flight_level", "error", "100", "90", "tx2")
    ]
    assert "read back FL090, cleared FL100" in alerts[0].message

    assert m.on_state(sv(10, alt=12000, vr=-1800)) == []
    [bust] = m.on_state(sv(120, alt=9400, vr=-1800))
    assert (bust.source, bust.kind) == ("track", "level_bust")
    assert m.on_state(sv(125, alt=9300, vr=-1800)) == []  # raised once


def test_monitor_callsign_confusion_and_no_readback():
    m = _monitor()
    m.on_transmission(Transmission(T0, "ifly nine six one, descend flight level one hundred"))
    _, _, alerts = m.on_transmission(
        Transmission(T0 + 6, "descending flight level one hundred, ifly nine one six")
    )
    assert [(a.kind, a.expected, a.heard) for a in alerts] == [
        ("callsign", "ifly 9 6 1", "ifly 9 1 6")
    ]
    expired = m.expire(T0 + 30)
    assert [(a.kind, a.callsign) for a in expired] == [("no_readback", "ifly 9 6 1")]


def test_snap_to_the_only_nearby_ads_b_callsign():
    traffic = {"ifly 7 8 5", "ifly 7 5 8", "lufthansa 5 0 4"}
    assert snap("ifly 7 8 4", traffic) == "ifly 7 8 5"  # one digit misheard
    assert snap("i fly 7 8 5", traffic) == "ifly 7 8 5"  # airline misheard, digits right
    assert snap("5 0 4", traffic) == "lufthansa 5 0 4"  # abbreviated
    assert snap("ifly 7 5 8", traffic) == "ifly 7 5 8"  # a real aircraft stays itself
    assert snap("ifly 7 5 5", traffic) == "ifly 7 5 5"  # one edit from two aircraft: unsure
    assert snap("speedbird 1 2", traffic) == "speedbird 1 2"  # nothing close


def test_monitor_snaps_misheard_callsigns_but_keeps_confusion():
    m = _monitor()
    m.on_state(sv(0, alt=12000, callsign="IGO961"))
    m.on_state(sv(0, alt=12000, callsign="IGO916"))
    m.on_transmission(Transmission(T0, "ifly nine six one, descend flight level one hundred"))
    _, _, alerts = m.on_transmission(
        Transmission(T0 + 6, "descending flight level one hundred, ifly nine six two", id="tx2")
    )
    assert alerts == [] and m.snapped == {"tx2": "ifly 9 6 2"}
    m.on_transmission(Transmission(T0 + 10, "ifly nine six one, turn left heading two seven zero"))
    _, _, alerts = m.on_transmission(
        Transmission(T0 + 16, "left heading two seven zero, ifly nine one six")
    )
    assert [(a.kind, a.expected, a.heard) for a in alerts] == [
        ("callsign", "ifly 9 6 1", "ifly 9 1 6")
    ]


def test_plausible_values():
    ok = ["flight_level 80", "altitude 5000", "heading 360", "speed 250", "squawk 7700",
          "qnh 1013", "qnh 2992", "frequency 118.1", "frequency 121.755", "runway 27l"]  # fmt: skip
    bad = ["flight_level 2770", "altitude 90000", "heading 400", "speed 10",
           "squawk 7248", "squawk 123", "qnh 1610", "frequency 1181"]  # fmt: skip
    assert all(plausible(Item(*x.split())) for x in ok)
    assert not any(plausible(Item(*x.split())) for x in bad)


def test_impossible_instruction_is_dropped_but_impossible_readback_is_an_error():
    cs = "ifly 9 6 1"
    heard = {  # what a parser made of each transmission, as Spark did with a stutter
        "ifly nine six one climb": Clearance(cs, [Item("flight_level", "2770", "climb")]),
        "climbing ifly nine six one": Clearance(cs, [Item("flight_level", "270", "climb")]),
        "ifly nine six one squawk": Clearance(cs, [Item("squawk", "7241")]),
        "squawk ifly nine six one": Clearance(cs, [Item("squawk", "7248")]),
    }
    m = Monitor(heard.__getitem__)
    m.on_transmission(Transmission(T0, "ifly nine six one climb", "controller"))
    _, _, alerts = m.on_transmission(Transmission(T0 + 5, "climbing ifly nine six one", "pilot"))
    assert alerts == [] and m.tracks[cs] == {}  # nothing to check against FL2770
    m.on_transmission(Transmission(T0 + 30, "ifly nine six one squawk", "controller"))
    _, _, alerts = m.on_transmission(Transmission(T0 + 35, "squawk ifly nine six one", "pilot"))
    assert [(a.kind, a.expected, a.heard) for a in alerts] == [("squawk", "7241", "7248")]
