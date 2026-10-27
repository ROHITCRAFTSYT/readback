"""Feed a recorded or simulated scenario through the monitor, event by event."""

from __future__ import annotations

from collections.abc import Iterator

from readback.adsb import StateVector
from readback.monitor import Alert, Monitor, Transmission


def replay(
    scenario: dict, monitor: Monitor, use_roles: bool = False
) -> Iterator[tuple[dict, list[Alert], dict | None]]:
    """Yield (event, alerts, parsed) for every event. parsed holds the clearance and speaker
    role for transmissions, None for ADS-B states. With use_roles=False the monitor infers who
    is talking, as it must on live radio."""
    start = scenario["start"]
    last = start
    for ev in scenario["events"]:
        now = start + ev["t"]
        last = now
        if ev["type"] == "tx":
            tx = Transmission(now, ev["text"], ev.get("role") if use_roles else None, ev["id"])
            clearance, role, alerts = monitor.on_transmission(tx)
            yield ev, alerts, {"clearance": clearance.to_dict(), "role": role}
        elif ev["type"] == "state":
            yield ev, monitor.on_state(StateVector(**ev["state"])), None
    tail = monitor.expire(last + monitor.pairing_window + 1)
    if tail:
        yield {"t": last - start, "type": "end"}, tail, None
