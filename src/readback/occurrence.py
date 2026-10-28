"""Occurrence-report agent: Muse Spark investigates one alert with tools over what the monitor
saw (transmissions, ADS-B tracks, other alerts) and files a structured occurrence report, the
first draft a supervisor or safety manager would otherwise write by hand."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from readback.adsb import spoken_callsign
from readback.compare import same_callsign
from readback.monitor import describe

OCCURRENCE_TYPES = [
    "readback error", "callsign confusion", "no readback", "level bust",
    "vertical deviation", "heading deviation", "squawk deviation", "other",
]  # fmt: skip

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "one line: 'Readback error: FL090 for FL100'"},
        "occurrence_type": {"type": "string", "enum": OCCURRENCE_TYPES},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string", "description": "what happened, 2-4 sentences, facts only"},
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"time": {"type": "string"}, "event": {"type": "string"}},
                "required": ["time", "event"],
            },
        },
        "contributing_factors": {"type": "array", "items": {"type": "string"}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "evidence_quality": {
            "type": "string",
            "description": "how far the evidence can be trusted (speech recognition, gaps)",
        },
    },
    "required": [
        "title", "occurrence_type", "risk", "summary", "timeline",
        "contributing_factors", "recommended_actions", "evidence_quality",
    ],
}  # fmt: skip

SYSTEM = """\
You are an air traffic services safety investigator. The automatic monitor raised the alert \
below on a recorded frequency. Investigate it with the tools: read the transmissions to and \
from the aircraft involved (and any aircraft with a similar callsign), check the ADS-B track \
against what was cleared, and look for related alerts. Then call submit_report exactly once.

Rules:
- Facts only. Every statement must come from a tool result. Do not invent times, values or \
causes; if something is unknown, say so.
- Transcripts come from speech recognition and can contain errors. When a transcript is the \
only evidence (no ADS-B confirmation), say so in evidence_quality and do not overstate.
- Times are mm:ss from the start of the session.
- Risk: high if separation or terrain could be affected (a level bust, a wrong level or \
heading taken up, a clearance taken by the wrong aircraft); medium if a wrong value was read \
back but not acted on; low otherwise.
- Recommended actions are for the controller or the unit (e.g. challenge readbacks, \
similar-callsign warning), short and specific."""


def mmss(t: float) -> str:
    s = max(0, round(t))
    return f"{s // 60:02d}:{s % 60:02d}"


@dataclass
class Log:
    """What the monitor saw in one session, in seconds from its start."""

    transmissions: list[dict] = field(default_factory=list)  # t, role, text, callsign, items
    alerts: list[dict] = field(default_factory=list)  # Alert.to_dict() with t
    states: dict[str, list[dict]] = field(default_factory=dict)  # broadcast callsign -> states

    def aircraft(self) -> list[dict]:
        return [{"adsb": cs, "spoken": spoken_callsign(cs)} for cs in sorted(self.states)]

    # -- tools --------------------------------------------------------------------------
    def tool_transmissions(self, callsign: str = "", start: float = 0, end: float = 1e9) -> list:
        out = []
        for tx in self.transmissions:
            if not start <= tx["t"] <= end:
                continue
            if callsign and not (tx.get("callsign") and same_callsign(tx["callsign"], callsign)):
                continue
            out.append(
                {
                    "time": mmss(tx["t"]),
                    "t": tx["t"],
                    "speaker": tx.get("role"),
                    "heard": tx["text"],
                    "callsign": tx.get("callsign"),
                    "parsed": [describe(i["kind"], i["value"]) for i in tx.get("items", [])],
                }
            )
        return out[:40]

    def tool_adsb_track(self, callsign: str, start: float = 0, end: float = 1e9) -> dict:
        for bcast, states in self.states.items():
            if same_callsign(spoken_callsign(bcast), callsign) or bcast.lower() == callsign.lower():
                rows, last = [], None
                for s in states:
                    if start <= s["t"] <= end and (last is None or s["t"] - last >= 10):
                        rows.append(
                            {
                                "time": mmss(s["t"]),
                                "altitude_ft": s.get("altitude_ft"),
                                "vertical_rate_fpm": s.get("vertical_rate_fpm"),
                                "track_deg": s.get("track_deg"),
                                "squawk": s.get("squawk"),
                            }
                        )
                        last = s["t"]
                return {"adsb_callsign": bcast, "states": rows[-60:]}
        return {"error": f"no ADS-B track for {callsign!r}", "aircraft": self.aircraft()}

    def tool_alerts(self, callsign: str = "") -> list:
        return [
            {"time": mmss(a["t"]), **{k: a[k] for k in ("callsign", "kind", "severity", "message")}}
            for a in self.alerts
            if not callsign or same_callsign(a["callsign"], callsign)
        ]


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_CS = {"type": "string", "description": "spoken callsign, e.g. 'ifly 9 6 1', or ADS-B 'IGO961'"}
_START = {"type": "number", "description": "seconds from session start"}
_END = {"type": "number", "description": "seconds from session start"}
TOOLS = [
    _fn("transmissions", "Radio transmissions, optionally for one callsign and a time window.",
        {"callsign": _CS, "start": _START, "end": _END}, []),
    _fn("adsb_track", "One aircraft's ADS-B track (about every 10 s) in a time window.",
        {"callsign": _CS, "start": _START, "end": _END}, ["callsign"]),
    _fn("alerts", "Alerts the monitor raised, optionally for one callsign.", {"callsign": _CS}, []),
    _fn("submit_report", "File the occurrence report. Call once, at the end.",
        REPORT_SCHEMA["properties"], REPORT_SCHEMA["required"]),
]  # fmt: skip


class AgentError(RuntimeError):
    pass


def investigate(chat, log: Log, alert: dict, max_steps: int = 8) -> dict:
    """Run the agent on one alert. Returns {"report": ..., "trace": [tool calls]}."""
    tools = {
        "transmissions": log.tool_transmissions,
        "adsb_track": log.tool_adsb_track,
        "alerts": log.tool_alerts,
    }
    brief = {
        "alert": {"time": mmss(alert["t"]), **{k: alert.get(k) for k in
                  ("callsign", "source", "kind", "severity", "message", "expected", "heard")}},
        "aircraft_on_adsb": log.aircraft(),
        "session_length": mmss(max([tx["t"] for tx in log.transmissions] or [0])),
    }  # fmt: skip
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(brief)},
    ]
    trace: list[dict] = []
    for _ in range(max_steps):
        msg = chat.chat(messages, TOOLS)
        calls = msg.get("tool_calls") or []
        turn = {"role": "assistant", "content": msg.get("content") or ""}
        if calls:
            turn["tool_calls"] = calls
        messages.append(turn)
        if not calls:
            messages.append({"role": "user", "content": "Call submit_report now."})
            continue
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "submit_report":
                missing = [k for k in REPORT_SCHEMA["required"] if k not in args]
                if not missing:
                    return {"report": args, "trace": trace}
                result: object = {"error": f"missing fields: {missing}"}
            elif name in tools:
                try:
                    result = tools[name](**args)
                except TypeError as exc:
                    result = {"error": str(exc)}
                trace.append({"tool": name, "args": args})
            else:
                result = {"error": f"unknown tool {name}"}
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)}
            )
    raise AgentError(f"no report after {max_steps} steps")


def to_markdown(report: dict, trace: list[dict]) -> str:
    lines = [
        f"# {report['title']}",
        "",
        f"**Type:** {report['occurrence_type']}  ",
        f"**Risk:** {report['risk']}",
        "",
        "## Summary",
        "",
        report["summary"],
        "",
        "## Timeline",
        "",
        *[f"- **{e['time']}** {e['event']}" for e in report["timeline"]],
        "",
        "## Contributing factors",
        "",
        *[f"- {f}" for f in report["contributing_factors"] or ["None identified."]],
        "",
        "## Recommended actions",
        "",
        *[f"- {a}" for a in report["recommended_actions"] or ["None."]],
        "",
        "## Evidence",
        "",
        report["evidence_quality"],
        "",
        "Evidence gathered: "
        + (", ".join(f"`{t['tool']}({_args(t['args'])})`" for t in trace) or "none")
        + ".",
        "",
        "*Draft by the Readback occurrence-report agent (Muse Spark). Review before filing.*",
    ]
    return "\n".join(lines) + "\n"


def _args(args: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in args.items())
