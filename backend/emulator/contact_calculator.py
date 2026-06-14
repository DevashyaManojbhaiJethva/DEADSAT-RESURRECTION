"""
DeadSat Resurrection — Ground Contact Calculator
AI-2 owned module

Fetches live TLE from CelesTrak for NOAA-18 (or any NORAD ID).
Calculates next ground contact window over Ahmedabad ground station.
Uses sgp4 for orbital propagation.
"""

import math
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional

try:
    from sgp4.api import Satrec, jday  # type: ignore
    SGP4_AVAILABLE = True
except ImportError:
    SGP4_AVAILABLE = False
    Satrec = None  # type: ignore
    jday   = None  # type: ignore
    print("[ContactCalc] WARNING: sgp4 not installed. Run: pip install sgp4")


# ──────────────────────────────────────────────
# Ground Station — Ahmedabad
# ──────────────────────────────────────────────

GROUND_STATION = {
    "name":      "Ahmedabad Ground Station",
    "lat_deg":   23.0225,
    "lon_deg":   72.5714,
    "alt_m":     53.0,
    "min_elevation_deg": 5.0,   # minimum elevation to establish link
}

# Meteor-M2-3 (NORAD 57166) — Active 2026, 137.900 MHz LRPT
# NOAA-18 decommissioned June 2025
DEFAULT_NORAD_ID = 57166
FREQUENCY_MHZ    = 137.900
CELESTRAK_URL    = "https://celestrak.org/SPACETRACK/query/GP.php?CATNR={norad_id}&FORMAT=TLE"
CELESTRAK_BACKUP = "https://celestrak.org/satcat/tle.txt"
FREQUENCY_MHZ    = 137.900  # Meteor-M2-3/4 LRPT frequency


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _deg_to_rad(deg: float) -> float:
    return deg * math.pi / 180.0

def _rad_to_deg(rad: float) -> float:
    return rad * 180.0 / math.pi


def _eci_to_azel(sat_eci: tuple, gs_lat: float, gs_lon: float, gs_alt: float,
                 jd: float, fr: float) -> dict:
    """
    Convert satellite ECI position to Azimuth/Elevation/Range
    as seen from the ground station.

    sat_eci: (x, y, z) in km (ECI frame)
    gs_lat, gs_lon: degrees
    gs_alt: km
    jd, fr: Julian date (integer + fraction) from sgp4
    """
    # GMST (Greenwich Mean Sidereal Time)
    jd_total = jd + fr
    T = (jd_total - 2451545.0) / 36525.0
    gmst_deg = (280.46061837
                + 360.98564736629 * (jd_total - 2451545.0)
                + T * T * (0.000387933 - T / 38710000.0)) % 360.0
    gmst_rad = _deg_to_rad(gmst_deg)

    lat_rad = _deg_to_rad(gs_lat)
    lon_rad = _deg_to_rad(gs_lon)
    lst_rad = gmst_rad + lon_rad   # Local Sidereal Time

    # Earth radius (km)
    R_E = 6378.137
    gs_r = R_E + gs_alt / 1000.0

    # Ground station ECI
    gs_x = gs_r * math.cos(lat_rad) * math.cos(lst_rad)
    gs_y = gs_r * math.cos(lat_rad) * math.sin(lst_rad)
    gs_z = gs_r * math.sin(lat_rad)

    # Range vector
    rx = sat_eci[0] - gs_x
    ry = sat_eci[1] - gs_y
    rz = sat_eci[2] - gs_z
    rng = math.sqrt(rx*rx + ry*ry + rz*rz)

    # SEZ frame (South-East-Z)
    sin_lat, cos_lat = math.sin(lat_rad), math.cos(lat_rad)
    sin_lst, cos_lst = math.sin(lst_rad), math.cos(lst_rad)

    s = ( sin_lat * cos_lst * rx
        + sin_lat * sin_lst * ry
        - cos_lat * rz)
    e = (-sin_lst * rx + cos_lst * ry)
    z = ( cos_lat * cos_lst * rx
        + cos_lat * sin_lst * ry
        + sin_lat * rz)

    el_rad  = math.asin(z / rng)
    az_rad  = math.atan2(-e, s) + math.pi   # 0–2π

    return {
        "azimuth_deg":   round(_rad_to_deg(az_rad), 2),
        "elevation_deg": round(_rad_to_deg(el_rad), 2),
        "range_km":      round(rng, 2),
    }


# ──────────────────────────────────────────────
# TLE Fetcher
# ──────────────────────────────────────────────

# Hardcoded fallback TLE for NOAA-18 (use if CelesTrak is unreachable on Pi)
FALLBACK_TLE = {
    "name":  "METEOR-M2-3",
    "line1": "1 57166U 23091A   26158.50000000  .00000020  00000-0  11435-4 0  9998",
    "line2": "2 57166  98.6420 220.1234 0001820  95.4321 264.7012 14.23651234 16789",
    "note":  "NOAA-18 decommissioned June 2025 — Meteor-M2-3 active on 137.900 MHz"
}


def fetch_tle(norad_id: int = DEFAULT_NORAD_ID) -> dict:
    """
    Fetch current TLE from CelesTrak.
    Falls back to hardcoded TLE if network unavailable (Pi offline scenario).
    Returns dict with name, line1, line2.
    """
    url = CELESTRAK_URL.format(norad_id=norad_id)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DeadSat/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            lines = resp.read().decode("utf-8").strip().splitlines()
            if len(lines) >= 3:
                print(f"[ContactCalc] TLE fetched from CelesTrak: {lines[0].strip()}")
                return {
                    "name":  lines[0].strip(),
                    "line1": lines[1].strip(),
                    "line2": lines[2].strip(),
                }
    except Exception as e:
        print(f"[ContactCalc] CelesTrak fetch failed ({e}), using fallback TLE")

    return FALLBACK_TLE


# ──────────────────────────────────────────────
# Contact Calculator
# ──────────────────────────────────────────────

class ContactCalculator:
    """
    Calculates upcoming ground contact windows for a satellite
    over the Ahmedabad ground station using sgp4.
    """

    def __init__(self, norad_id: int = DEFAULT_NORAD_ID):
        self.norad_id = norad_id
        self.tle      = None
        self.sat      = None

    def load_tle(self):
        """Fetch TLE and initialise sgp4 satellite object."""
        if not SGP4_AVAILABLE:
            print("[ContactCalc] sgp4 unavailable — cannot calculate contacts")
            return False
        self.tle = fetch_tle(self.norad_id)
        self.sat = Satrec.twoline2rv(self.tle["line1"], self.tle["line2"])  # type: ignore
        print(f"[ContactCalc] Loaded TLE for: {self.tle['name']}")
        return True

    def get_current_azel(self) -> Optional[dict]:
        """
        Return current azimuth, elevation, range of satellite
        over Ahmedabad right now.
        """
        if not self.sat:
            return None
        now = datetime.now(timezone.utc)
        jd, fr = jday(now.year, now.month, now.day,  # type: ignore
                      now.hour, now.minute, now.second + now.microsecond / 1e6)
        e, r, v = self.sat.sgp4(jd, fr)
        if e != 0:
            return None
        return _eci_to_azel(
            r,
            GROUND_STATION["lat_deg"],
            GROUND_STATION["lon_deg"],
            GROUND_STATION["alt_m"],
            jd, fr
        )

    def find_next_contact(self, search_hours: float = 24.0,
                          step_seconds: float = 30.0) -> Optional[dict]:
        """
        Scan forward from now to find the next contact window
        where elevation > min_elevation_deg.

        Returns dict with:
            aos      - Acquisition of Signal (datetime ISO)
            los      - Loss of Signal (datetime ISO)
            max_el   - Maximum elevation during pass (deg)
            duration - Contact duration (seconds)
        """
        if not self.sat:
            return None

        min_el = GROUND_STATION["min_elevation_deg"]
        gs_lat = GROUND_STATION["lat_deg"]
        gs_lon = GROUND_STATION["lon_deg"]
        gs_alt = GROUND_STATION["alt_m"]

        now          = datetime.now(timezone.utc)
        t            = now
        step         = timedelta(seconds=step_seconds)
        end_t        = now + timedelta(hours=search_hours)

        in_contact   = False
        aos_time     = None
        los_time     = None
        max_el       = -90.0

        while t < end_t:
            jd, fr = jday(t.year, t.month, t.day,  # type: ignore
                          t.hour, t.minute, t.second + t.microsecond / 1e6)
            e, r, v = self.sat.sgp4(jd, fr)
            if e != 0:
                t += step
                continue

            azel = _eci_to_azel(r, gs_lat, gs_lon, gs_alt, jd, fr)
            el   = azel["elevation_deg"]

            if el > min_el and not in_contact:
                in_contact = True
                aos_time   = t
                max_el     = el

            elif el > min_el and in_contact:
                if el > max_el:
                    max_el = el

            elif el <= min_el and in_contact:
                in_contact = False
                los_time   = t
                break

            t += step

        if aos_time is None:
            print("[ContactCalc] No contact window found in next 24 hours")
            return None

        if los_time is None:
            los_time = t   # still in contact at end of search

        duration = (los_time - aos_time).total_seconds()

        result = {
            "satellite":   self.tle["name"] if self.tle else "Unknown",
            "ground_station": GROUND_STATION["name"],
            "aos":         aos_time.isoformat(),
            "los":         los_time.isoformat(),
            "max_elevation_deg": round(max_el, 2),
            "duration_seconds":  round(duration),
            "in_contact_now":    aos_time <= now <= los_time if los_time else False,
        }

        print(f"[ContactCalc] Next contact: AOS={result['aos']} | Max El={result['max_elevation_deg']}° | Duration={result['duration_seconds']}s")
        return result

    def is_in_contact_now(self) -> bool:
        """Quick check — is satellite above horizon right now?"""
        azel = self.get_current_azel()
        if azel is None:
            return False
        return azel["elevation_deg"] > GROUND_STATION["min_elevation_deg"]

    def get_contact_summary(self) -> dict:
        """
        Full summary for FastAPI /contact endpoint.
        Returns current AzEl + next window.
        """
        current  = self.get_current_azel()
        next_win = self.find_next_contact()

        return {
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "current_azel":  current,
            "in_contact_now": self.is_in_contact_now(),
            "next_window":   next_win,
        }


# ──────────────────────────────────────────────
# Quick smoke test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    calc = ContactCalculator(norad_id=DEFAULT_NORAD_ID)
    if calc.load_tle():
        print("\n--- Current AzEl ---")
        azel = calc.get_current_azel()
        print(azel)

        print("\n--- Next Contact Window ---")
        window = calc.find_next_contact(search_hours=24.0, step_seconds=30.0)
        if window:
            print(f"  AOS:      {window['aos']}")
            print(f"  LOS:      {window['los']}")
            print(f"  Max El:   {window['max_elevation_deg']}°")
            print(f"  Duration: {window['duration_seconds']}s")