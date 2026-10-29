import json

from readback.glimmer import GlimmerParser, decode


def test_decode_accepts_clearance_json_only():
    c = decode(
        '{"callsign": "csa 1 d z", "items": [{"kind": "squawk", "value": "1404", "qualifier": ""}]}'
    )
    assert c.callsign == "csa 1 d z" and c.items[0].value == "1404"
    assert decode("not json") is None
    assert decode("[1, 2]") is None
    assert decode('{"items": 3}') is None
    c = decode('{"callsign": "", "items": [{"kind": "orbit", "value": "right"}, {"kind": "squawk"},'
               ' {"kind": "qnh", "value": "1007", "qualifier": null}]}')  # fmt: skip
    assert c.callsign is None and [(i.kind, i.value, i.qualifier) for i in c.items] == [
        ("squawk", "", ""),
        ("qnh", "1007", ""),
    ]


def test_batches_once_and_caches(tmp_path):
    calls = []

    def remote(texts):
        calls.append(texts)
        return [json.dumps({"callsign": None, "items": []}) if "1" in t else "oops" for t in texts]

    class Fake:
        pass

    fake = Fake()
    fake.remote = remote
    parser = GlimmerParser(tmp_path, remote=fake)
    out = parser.many(["squawk one four zero four", "hello", "squawk one four zero four"])
    assert len(calls) == 1 and len(calls[0]) == 2  # canonical form, deduplicated
    assert "squawk 1 4 0 4" in calls[0]
    assert [c.items for c in out] == [[], [], []] and parser.invalid == 1

    again = GlimmerParser(tmp_path, remote=fake)
    again.many(["squawk one four zero four", "hello"])
    assert len(calls) == 1  # both answers came from the cache
