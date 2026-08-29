"""Nearest-station live conditions, via the public Aviation Weather
Center METAR API (aviationweather.gov -- free, no API key, no auth).

Scope, deliberately limited: fills temperature, humidity, pressure, and
altitude reliably from GPS + the nearest reporting station, plus wind
*speed*. Wind *direction* is NOT filled in. METAR reports wind direction
as an absolute compass bearing; Ballistica's wind field is a "clock
position" relative to whichever way the shooter is actually facing down
range -- GPS coordinates say where you are, not which way you're aimed,
so there's no way to convert one into the other without information this
endpoint doesn't have. Silently guessing would produce a wind-direction
value that looks auto-filled and trustworthy but could easily be wrong --
not an acceptable tradeoff for a field that directly drives a live-fire
correction. Left for manual entry, same as today.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

_METAR_URL = "https://aviationweather.gov/api/data/metar"
_USER_AGENT = "Ballistica/1.0 (https://ballistica.onrender.com)"

# Degrees of lat/lon around the requested point to search -- generous
# enough to reliably find at least one reporting station even in
# sparser rural areas (roughly 50-60 miles at CONUS latitudes) without
# pulling in a station so far away its conditions are meaningless for
# the shooter's actual position.
_SEARCH_RADIUS_DEG = 0.75


@dataclass
class NearestStationConditions:
    temp_f: float
    humidity_pct: float
    pressure_inhg: float
    altitude_ft: float
    wind_speed_mph: float
    station_id: str
    station_name: str
    distance_mi: float
    observed_at: str  # ISO 8601 UTC


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_mi = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r_mi * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _relative_humidity_pct(temp_c: float, dewpoint_c: float) -> float:
    """Magnus-Tetens approximation -- METAR reports dewpoint, not RH
    directly. Standard meteorological approximation, accurate to within
    about 1% RH over normal outdoor temperature ranges; consistent with
    this app's existing reference-only framing for atmospheric inputs."""
    def sat_vapor(t):
        return math.exp((17.625 * t) / (243.04 + t))
    return max(0.0, min(100.0, 100.0 * sat_vapor(dewpoint_c) / sat_vapor(temp_c)))


def fetch_nearest_station_conditions(lat: float, lon: float) -> NearestStationConditions | None:
    """Returns conditions from the nearest METAR station with usable
    temperature/pressure data, or None if nothing was found within the
    search radius (sparse rural area, or the upstream API is down --
    both treated as "couldn't auto-fill," not an error the caller
    should propagate as a 500)."""
    bbox = (
        f"{lat - _SEARCH_RADIUS_DEG},{lon - _SEARCH_RADIUS_DEG},"
        f"{lat + _SEARCH_RADIUS_DEG},{lon + _SEARCH_RADIUS_DEG}"
    )
    try:
        resp = httpx.get(
            _METAR_URL, params={"bbox": bbox, "format": "json"},
            headers={"User-Agent": _USER_AGENT}, timeout=15,
        )
        resp.raise_for_status()
        stations = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    # Only stations reporting the fields this needs -- an automated
    # wind-only station (no temp/dewp/altim) is geographically valid
    # but useless for filling conditions; skip straight to the next-
    # nearest one that actually has what's needed rather than erroring.
    usable = [
        s for s in stations
        if s.get("temp") is not None and s.get("dewp") is not None
        and s.get("altim") is not None and s.get("wspd") is not None
        and s.get("lat") is not None and s.get("lon") is not None
    ]
    if not usable:
        return None

    nearest = min(usable, key=lambda s: _haversine_mi(lat, lon, s["lat"], s["lon"]))
    distance_mi = _haversine_mi(lat, lon, nearest["lat"], nearest["lon"])
    observed_at = datetime.fromtimestamp(nearest["obsTime"], tz=timezone.utc).isoformat()

    return NearestStationConditions(
        temp_f=nearest["temp"] * 9 / 5 + 32,
        humidity_pct=_relative_humidity_pct(nearest["temp"], nearest["dewp"]),
        pressure_inhg=nearest["altim"] * 0.0295299830714,  # hPa -> inHg
        altitude_ft=nearest["elev"] * 3.28084,  # meters -> feet
        wind_speed_mph=nearest["wspd"] * 1.15078,  # knots -> mph
        station_id=nearest["icaoId"],
        station_name=nearest.get("name", nearest["icaoId"]),
        distance_mi=round(distance_mi, 1),
        observed_at=observed_at,
    )
