"""ADS-B state vectors (OpenSky Network) and checks of an aircraft's track against its clearance."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from readback.clearance import Item
from readback.normalize import canonical

API = "https://opensky-network.org/api"
TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
)
M_TO_FT = 3.28084
MS_TO_KT = 1.943844

# ICAO airline designator -> radiotelephony designator, so "SWR123" on ADS-B matches
# "swiss one two three" on the radio. Designators whose telephony is the code itself
# (CSA, KLM) need no entry.
TELEPHONY = {
    "AFR": "airfrans", "AIC": "airindia", "AUA": "austrian", "AXB": "expressindia",
    "BAW": "speedbird", "DLH": "lufthansa", "EZY": "easy", "IGO": "ifly", "QTR": "qatari",
    "RYR": "ryanair", "SEJ": "spicejet", "SIA": "singapore", "SWR": "swiss", "THY": "turkish",
    "TVS": "skytravel", "UAE": "emirates", "WZZ": "wizzair", "AFL": "aeroflot",
}  # fmt: skip
# Telephony designators the parser should recognise even without corpus data.
KNOWN_DESIGNATORS = set(TELEPHONY.values()) | {"csa", "klm", "lot", "wizz", "jet", "ifly"}


@dataclass(frozen=True)
class StateVector:
    icao24: str
    callsign: str  # as broadcast, e.g. "CSA1DZ"
    time: float  # unix seconds
    lat: float | None
    lon: float | None
    altitude_ft: float | None  # barometric
    ground_speed_kt: float | None
    track_deg: float | None
    vertical_rate_fpm: float | None
    on_ground: bool = False
    squawk: str | None = None

    @classmethod
    def from_opensky(cls, row: list) -> StateVector:
        def scaled(v, k):
            return None if v is None else v * k

        return cls(
            icao24=row[0],
            callsign=(row[1] or "").strip(),
            time=float(row[4] or row[3] or 0),
            lat=row[6],
            lon=row[5],
            altitude_ft=scaled(row[7], M_TO_FT),
            ground_speed_kt=scaled(row[9], MS_TO_KT),
            track_deg=row[10],
            vertical_rate_fpm=scaled(row[11], M_TO_FT * 60),
            on_ground=bool(row[8]),
            squawk=row[14],
        )


def spoken_callsign(adsb_callsign: str) -> str:
    """Canonical spoken form of a broadcast callsign: "SWR123" -> "swiss 1 2 3"."""
    cs = adsb_callsign.strip().upper()
    if len(cs) > 3 and cs[:3].isalpha() and cs[3].isdigit():
        prefix = TELEPHONY.get(cs[:3], cs[:3])
        return " ".join([prefix.lower(), *canonical(cs[3:])])
    return " ".join(canonical(cs))


class OpenSky:
    """OpenSky REST client. Works anonymously (400 credits/day); set OPENSKY_CLIENT_ID and
    OPENSKY_CLIENT_SECRET for an OAuth2 client and higher limits."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client or httpx.Client(timeout=30)
        self._id = client_id or os.environ.get("OPENSKY_CLIENT_ID")
        self._secret = client_secret or os.environ.get("OPENSKY_CLIENT_SECRET")
        self._token: tuple[str, float] | None = None
        self._clock = clock

    def _headers(self) -> dict[str, str]:
        if not (self._id and self._secret):
            return {}
        if self._token is None or self._clock() > self._token[1]:
            resp = self._client.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._id,
                    "client_secret": self._secret,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            self._token = (body["access_token"], self._clock() + body.get("expires_in", 1800) - 60)
        return {"Authorization": f"Bearer {self._token[0]}"}

    def states(self, bbox: tuple[float, float, float, float]) -> list[StateVector]:
        """Latest state vectors inside (lat_min, lon_min, lat_max, lon_max)."""
        lamin, lomin, lamax, lomax = bbox
        resp = self._client.get(
            f"{API}/states/all",
            params={"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return [StateVector.from_opensky(row) for row in resp.json().get("states") or []]


@dataclass(frozen=True)
class Deviation:
    kind: str  # "level_bust", "wrong_vertical_direction", "heading", "squawk"
    severity: str  # "error" or "warning"
    expected: str
    actual: str
    detail: str


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


@dataclass
class ClearanceTrack:
    """Tracks one aircraft's compliance with the last clearance it was given."""

    item: Item
    issued_at: float
    start_altitude_ft: float | None = None
    reached: bool = False

    LEVEL_TOLERANCE_FT = 200
    BUST_MARGIN_FT = 300  # ICAO treats 300 ft beyond the cleared level as a level bust
    VERTICAL_GRACE_S = 60
    LEVEL_OFF_GRACE_S = 120
    HEADING_GRACE_S = 45
    HEADING_TOLERANCE_DEG = 20  # ground track differs from heading by the wind correction

    def target_ft(self) -> float | None:
        if self.item.kind == "flight_level":
            return int(self.item.value) * 100.0
        if self.item.kind == "altitude":
            return float(self.item.value)
        return None

    def check(self, sv: StateVector) -> Deviation | None:
        if self.item.kind in ("flight_level", "altitude"):
            return self._vertical(sv)
        if self.item.kind == "heading":
            return self._heading(sv)
        if self.item.kind == "squawk" and sv.squawk and sv.time - self.issued_at > 30:
            if sv.squawk != self.item.value:
                return Deviation(
                    "squawk", "error", self.item.value, sv.squawk, "transponder code differs"
                )
        return None

    def _vertical(self, sv: StateVector) -> Deviation | None:
        target, alt = self.target_ft(), sv.altitude_ft
        if target is None or alt is None:
            return None
        if self.start_altitude_ft is None:
            self.start_altitude_ft = alt
        direction = (target > self.start_altitude_ft) - (target < self.start_altitude_ft)
        label = (
            f"FL{int(target / 100):03d}" if self.item.kind == "flight_level" else f"{target:.0f} ft"
        )
        if abs(alt - target) <= self.LEVEL_TOLERANCE_FT:
            self.reached = True
            return None
        overshoot = (alt - target) * direction
        if direction and overshoot > self.BUST_MARGIN_FT:
            return Deviation(
                "level_bust", "error", label, f"{alt:.0f} ft",
                f"{'climbed' if direction > 0 else 'descended'} {overshoot:.0f} ft through {label}",
            )  # fmt: skip
        if self.reached and abs(alt - target) > self.BUST_MARGIN_FT:
            return Deviation("level_bust", "error", label, f"{alt:.0f} ft", f"left {label}")
        vr = sv.vertical_rate_fpm or 0.0
        elapsed = sv.time - self.issued_at
        if direction and elapsed > self.VERTICAL_GRACE_S and vr * direction < -300:
            return Deviation(
                "wrong_vertical_direction", "error", label, f"{vr:+.0f} fpm",
                f"moving away from {label}",
            )  # fmt: skip
        if direction and elapsed > self.LEVEL_OFF_GRACE_S and abs(vr) < 200:
            return Deviation(
                "level_off", "warning", label, f"{alt:.0f} ft",
                f"level at {alt:.0f} ft, {abs(alt - target):.0f} ft short of {label}",
            )  # fmt: skip
        return None

    def _heading(self, sv: StateVector) -> Deviation | None:
        if sv.track_deg is None or sv.time - self.issued_at < self.HEADING_GRACE_S:
            return None
        target = float(self.item.value)
        diff = _angle_diff(sv.track_deg, target)
        if diff > self.HEADING_TOLERANCE_DEG:
            return Deviation(
                "heading", "warning", f"{int(target):03d}", f"{sv.track_deg:.0f}",
                f"track {diff:.0f} degrees off assigned heading",
            )  # fmt: skip
        return None
