import json
import random

import httpx

from readback.clearance import Clearance
from readback.compare import compare
from readback.datasets import Transcript, timing
from readback.llm import MuseChat, SparkParser
from readback.pairs import inject_error, mine_pairs
from readback.rules import parse

DESIGNATORS = {"csa", "lufthansa"}


def _t(id_, text, role, start, end):
    return Transcript(id_, "uwb-atcc", text, "S", role, "S", start, end)


def _parse(text):
    return parse(text, DESIGNATORS)


def test_timing_from_ids():
    assert timing("uwb-atcc_TWR-34720N_001666_001952_AT") == ("TWR-34720N", 16.66, 19.52)
    atco2 = "atco2_test-set-1h_LKTB_BRNO_Tower_119_605MHz_20201028_185619-A__000000-000536"
    assert timing(atco2) == (atco2.split("__")[0], 0.0, 5.36)


def test_mine_pairs_matches_by_callsign_and_order():
    ts = [
        _t("a", "csa one two descend flight level one hundred", "controller", 0.0, 3.0),
        _t("b", "lufthansa three four roger", "pilot", 3.5, 4.5),
        _t("c", "descending flight level one hundred csa one two", "pilot", 5.0, 7.0),
        _t("d", "squawk one two three four", "controller", 30.0, 32.0),
        _t("e", "squawk one two three four", "pilot", 32.5, 34.0),
        _t("f", "runway two seven cleared to land", "controller", 60.0, 62.0),
        _t("g", "cleared to land runway two seven", "pilot", 80.0, 82.0),  # too late
    ]
    pairs = mine_pairs(ts, _parse)
    assert [(p.instruction.id, p.readback.id) for p in pairs] == [("a", "c"), ("d", "e")]


def test_injected_errors_are_detected():
    instruction = "csa one delta zulu descend flight level one hundred squawk four seven two one"
    readback = "descending flight level one hundred squawk four seven two one csa one delta zulu"
    assert compare(_parse(instruction), _parse(readback)) == []
    seen = set()
    for seed in range(40):
        result = inject_error(instruction, readback, DESIGNATORS, random.Random(seed))
        if result is None:
            continue
        text, label = result
        seen.add(label["type"])
        findings = compare(_parse(instruction), _parse(text))
        assert any(f.severity == "error" for f in findings), (text, label)
    assert seen == {"value", "callsign"}


def test_no_injection_when_nothing_is_echoed():
    assert inject_error("csa one two contact tower", "roger", DESIGNATORS, random.Random(0)) is None


def test_spark_parser_requests_strict_schema_and_caches(tmp_path):
    calls = []
    answer = {"callsign": "csa 1 d z", "items": [
        {"kind": "flight_level", "value": "100", "qualifier": "descend"},
    ]}  # fmt: skip

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        content = json.dumps(answer)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    chat = MuseChat("key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    parser = SparkParser(chat, cache_dir=tmp_path)
    first = parser("CSA1DZ descend FL100")
    second = parser("csa one delta zulu descend flight level one hundred")  # same canonical text

    assert first == second == Clearance.from_dict(answer)
    assert len(calls) == 1
    fmt = calls[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert calls[0]["model"] == "muse-spark-1.3"
    assert calls[0]["messages"][1]["content"] == "csa 1 d z descend flight level 1 0 0"
