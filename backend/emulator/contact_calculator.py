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
    gs_alt: METRES — this said "km" while GROUND_STATION["alt_m"] is 53.0 m
            and the arithmetic below divides by 1000.0. The maths was right;
            only the docstring was wrong. Reading it as km would have put the
            Ahmedabad antenna 53 kilometres up, which is the kind of error
            that survives review precisely because the code still works.
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

# ──────────────────────────────────────────────────────────────────────
# Fallback TLE — used only when CelesTrak is unreachable (Pi offline).
#
# ⚠ THESE ELEMENTS ARE SYNTHETIC. They are a plausible element set for
# Meteor-M2-3, not a published one. Both lines originally carried INVALID
# checksums (line 1 stated 8, computes 7; line 2 stated 9, computes 0), which
# is the giveaway that they were hand-written rather than fetched. The
# checksums are now correct so the record is well-formed and passes
# validate_tle(), but correcting a checksum does not make the orbit real.
#
# Consequence: contact windows computed from this fallback are approximately
# right for a sun-synchronous LEO satellite and specifically wrong for
# Meteor-M2-3. load_tle() warns whenever this path is taken.
#
# To fix properly, on a machine with network access:
#     curl 'https://celestrak.org/NORAD/elements/gp.php?CATNR=57166&FORMAT=tle'
# and paste the result here.
# ──────────────────────────────────────────────────────────────────────
FALLBACK_TLE = {
    "name":  "METEOR-M2-3 (SYNTHETIC FALLBACK)",
    "line1": "1 57166U 23091A   26158.50000000  .00000020  00000-0  11435-4 0  9997",
    "line2": "2 57166  98.6420 220.1234 0001820  95.4321 264.7012 14.23651234 16780",
    "note":  "SYNTHETIC placeholder elements — not a published element set. "
             "NOAA-18 was decommissioned June 2025; Meteor-M2-3 is the active "
             "137.900 MHz target.",
    "synthetic": True,
}


class InvalidTLEError(ValueError):
    """A TLE that would produce nonsense if handed to Satrec.twoline2rv()."""


def _tle_checksum(line: str) -> int:
    """
    TLE checksum: digits sum mod 10, with '-' counting as 1 and everything
    else as 0. Column 69 carries the expected value.
    """
    total = 0
    for ch in line[:68]:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10


def validate_tle(line1: str, line2: str, *, strict: bool = True) -> None:
    """
    Reject a malformed TLE BEFORE it reaches Satrec.twoline2rv().

    Nothing validated these lines. When CelesTrak returns an error page,
    a rate-limit notice or an HTML redirect, `fetch_tle()` happily took the
    second and third lines of that page and handed them to sgp4, which either
    raised something opaque or — worse — parsed garbage into a Satrec and
    produced contact windows for a satellite that does not exist.

    Checks, in the order a human would:
      * both lines present and non-empty
      * line 1 starts with "1 ", line 2 with "2 "
      * both are 69 characters (the fixed TLE record width)
      * the satellite numbers in columns 3-7 agree between the two lines
      * the column-69 checksum matches

    `strict=False` downgrades the checksum to a warning — some sources emit
    TLEs with a stale checksum but otherwise correct elements.

    Raises InvalidTLEError with a message that names the actual problem.
    """
    for label, line in (("line1", line1), ("line2", line2)):
        if not isinstance(line, str) or not line.strip():
            raise InvalidTLEError(f"{label} is empty or not a string")

    l1, l2 = line1.rstrip("\r\n"), line2.rstrip("\r\n")

    for label, line, expect in (("line1", l1, "1 "), ("line2", l2, "2 ")):
        if not line.startswith(expect):
            preview = line[:40].replace("\t", " ")
            raise InvalidTLEError(
                f"{label} does not start with {expect!r} — got {preview!r}. "
                f"This is what an HTML error page from CelesTrak looks like.")
        if len(line) != 69:
            raise InvalidTLEError(
                f"{label} is {len(line)} characters, expected 69 "
                f"(TLE records are fixed width)")

    if l1[2:7] != l2[2:7]:
        raise InvalidTLEError(
            f"satellite number mismatch: line1 says {l1[2:7]!r}, "
            f"line2 says {l2[2:7]!r} — the two lines are from different objects")

    for label, line in (("line1", l1), ("line2", l2)):
        expected = _tle_checksum(line)
        try:
            actual = int(line[68])
        except (IndexError, ValueError):
            raise InvalidTLEError(f"{label} has no checksum digit in column 69")
        if actual != expected:
            msg = (f"{label} checksum is {actual}, computed {expected} — "
                   f"the line is corrupted or truncated")
            if strict:
                raise InvalidTLEError(msg)
            print(f"[ContactCalc] WARNING: {msg}")


def tle_epoch_datetime(line1: str) -> Optional[datetime]:
    """
    Parse the epoch out of TLE line 1 (columns 19-32: YYDDD.DDDDDDDD).

    Used to warn when the elements in use are stale. LEO TLEs degrade within
    days; propagating a month-old element set produces a contact window that
    is confidently wrong.
    """
    try:
        raw = line1[18:32].strip()
        yy = int(raw[:2])
        doy = float(raw[2:])
        year = 2000 + yy if yy < 57 else 1900 + yy      # TLE two-digit convention
        return (datetime(year, 1, 1, tzinfo=timezone.utc)
                + timedelta(days=doy - 1.0))
    except (ValueError, IndexError):
        return None


#: Warn when the TLE in use is older than this. LEO element sets are usually
#: refreshed daily; beyond a few weeks the propagation error is large enough
#: that an AOS prediction is not worth reporting without a caveat.
TLE_STALE_AFTER_DAYS = 30


def warn_if_tle_stale(tle: dict) -> Optional[float]:
    """Print a warning if the TLE epoch is old. Returns its age in days."""
    epoch = tle_epoch_datetime(tle.get("line1", ""))
    if epoch is None:
        print("[ContactCalc] WARNING: could not parse the TLE epoch — "
              "cannot judge staleness")
        return None
    age_days = (datetime.now(timezone.utc) - epoch).total_seconds() / 86400.0
    if age_days > TLE_STALE_AFTER_DAYS:
        print(f"[ContactCalc] " + "!" * 58)
        print(f"[ContactCalc] WARNING: TLE epoch is {age_days:.0f} days old "
              f"({epoch.date()}).")
        print(f"[ContactCalc]          LEO element sets degrade within days. "
              f"Every contact window")
        print(f"[ContactCalc]          computed from this is unreliable. "
              f"Refresh from CelesTrak,")
        print(f"[ContactCalc]          or update FALLBACK_TLE in this file.")
        print(f"[ContactCalc] " + "!" * 58)
    elif age_days > 7:
        print(f"[ContactCalc] Note: TLE epoch is {age_days:.1f} days old "
              f"({epoch.date()})")
    return age_days


def fetch_tle(norad_id: int = DEFAULT_NORAD_ID) -> dict:
    """
    Fetch current TLE from CelesTrak.
    Falls back to the hardcoded TLE if the network is unavailable (Pi offline)
    OR if what came back is not a valid TLE — see validate_tle().
    Returns dict with name, line1, line2.
    """
    url = CELESTRAK_URL.format(norad_id=norad_id)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DeadSat/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            lines = resp.read().decode("utf-8").strip().splitlines()
            if len(lines) >= 3:
                candidate = {
                    "name":  lines[0].strip(),
                    "line1": lines[1].strip(),
                    "line2": lines[2].strip(),
                }
                # Validate BEFORE returning. A CelesTrak error page has three
                # or more lines too, and used to sail straight through.
                validate_tle(candidate["line1"], candidate["line2"])
                print(f"[ContactCalc] TLE fetched from CelesTrak: {candidate['name']}")
                warn_if_tle_stale(candidate)
                return candidate
            print(f"[ContactCalc] CelesTrak returned {len(lines)} line(s), "
                  f"expected >= 3 — using fallback TLE")
    except InvalidTLEError as e:
        print(f"[ContactCalc] CelesTrak returned something that is not a TLE "
              f"({e}) — using fallback TLE")
    except Exception as e:
        print(f"[ContactCalc] CelesTrak fetch failed ({e}), using fallback TLE")

    warn_if_tle_stale(FALLBACK_TLE)
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
        """
        Fetch a TLE and initialise the sgp4 satellite object.

        Validates before handing anything to Satrec.twoline2rv(), which
        accepts malformed input and produces a Satrec that propagates
        nonsense. Returns False on a bad TLE rather than raising, so a
        contact-window failure degrades the recovery graph instead of killing
        it — node_schedule_uplink() already treats a calculator error as
        "no window".
        """
        if not SGP4_AVAILABLE:
            print("[ContactCalc] sgp4 unavailable — cannot calculate contacts")
            return False

        self.tle = fetch_tle(self.norad_id)

        try:
            validate_tle(self.tle["line1"], self.tle["line2"])
        except InvalidTLEError as exc:
            print(f"[ContactCalc] REFUSING to load an invalid TLE: {exc}")
            self.tle = None
            self.sat = None
            return False

        if self.tle.get("synthetic"):
            print("[ContactCalc] WARNING: using SYNTHETIC fallback elements — "
                  "contact windows are illustrative, not predictive")

        try:
            self.sat = Satrec.twoline2rv(self.tle["line1"], self.tle["line2"])  # type: ignore
        except Exception as exc:
            print(f"[ContactCalc] sgp4 rejected the TLE: {exc}")
            self.sat = None
            return False

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

    def _elevation_at(self, t: datetime) -> Optional[float]:
        """Elevation in degrees at `t`, or None if SGP4 rejects the epoch."""
        jd, fr = jday(t.year, t.month, t.day,  # type: ignore
                      t.hour, t.minute, t.second + t.microsecond / 1e6)
        e, r, _v = self.sat.sgp4(jd, fr)
        if e != 0:
            return None
        return _eci_to_azel(
            r,
            GROUND_STATION["lat_deg"],
            GROUND_STATION["lon_deg"],
            GROUND_STATION["alt_m"],
            jd, fr,
        )["elevation_deg"]

    def _bisect_crossing(self, t_lo: datetime, t_hi: datetime,
                         rising: bool, tolerance_s: float = 1.0) -> datetime:
        """
        Refine a horizon crossing bracketed by [t_lo, t_hi] to ~1 second.

        `rising` selects which side of the threshold t_lo is on: True when the
        satellite is below the horizon at t_lo and above at t_hi (AOS), False
        for the reverse (LOS). Costs about log2(coarse_step) evaluations — 6
        for a 60 s bracket — instead of one per second.
        """
        min_el = GROUND_STATION["min_elevation_deg"]
        while (t_hi - t_lo).total_seconds() > tolerance_s:
            mid = t_lo + (t_hi - t_lo) / 2
            el = self._elevation_at(mid)
            if el is None:
                return t_hi
            above = el > min_el
            if above == rising:
                t_hi = mid
            else:
                t_lo = mid
        return t_hi if rising else t_lo

    def find_next_contact(self, search_hours: float = 24.0,
                          step_seconds: float = 60.0) -> Optional[dict]:
        """
        Next contact window where elevation > min_elevation_deg.

        PERFORMANCE: this used a flat scan at `step_seconds`, and the recovery
        agent called it with step_seconds=10 over a 24 h search — 8,640 SGP4
        propagations plus 8,640 coordinate transforms, run SYNCHRONOUSLY
        inside the recovery graph. On a Raspberry Pi 4 that is seconds of dead
        time in the middle of a fault response, and it was described in the
        module header as an optimisation.

        Now coarse-then-refine:
          * scan at `step_seconds` (default 60 s) to bracket the crossing —
            1,440 evaluations for 24 h. A LEO pass lasts 5-15 minutes, so a
            60 s step cannot step over one.
          * bisect each bracket to ~1 s, ~6 evaluations per crossing.
          * sample max elevation over the pass at the coarse step, then refine
            around the peak.

        Roughly 1,450 evaluations instead of 8,640 — about 6x fewer than the
        agent's setting, and ~50x fewer than a 10 s flat scan refined to the
        same 1 s precision would need.

        Returns dict with aos, los, max_elevation_deg, duration_seconds.
        """
        if not self.sat:
            return None

        min_el = GROUND_STATION["min_elevation_deg"]
        now    = datetime.now(timezone.utc)
        step   = timedelta(seconds=step_seconds)
        end_t  = now + timedelta(hours=search_hours)

        # ── Phase 1: coarse scan to bracket AOS ───────────────────────────
        t = now
        prev_t, prev_above = None, None
        aos_time = los_time = None
        evaluations = 0

        while t < end_t:
            el = self._elevation_at(t)
            evaluations += 1
            if el is None:
                t += step
                prev_t, prev_above = None, None
                continue
            above = el > min_el

            if prev_above is None:
                # Already in contact at t=now: this IS the window.
                if above:
                    aos_time = t
                    break
            elif above and not prev_above:
                # Crossing bracketed by [prev_t, t] — refine it.
                aos_time = self._bisect_crossing(prev_t, t, rising=True)
                break

            prev_t, prev_above = t, above
            t += step

        if aos_time is None:
            print(f"[ContactCalc] No contact window in the next {search_hours:g} h "
                  f"({evaluations} propagations)")
            return None

        # ── Phase 2: coarse scan forward for LOS, tracking peak elevation ──
        max_el = self._elevation_at(aos_time) or min_el
        peak_t = aos_time
        t = aos_time + step
        prev_t = aos_time

        while t < end_t:
            el = self._elevation_at(t)
            evaluations += 1
            if el is None:
                break
            if el > max_el:
                max_el, peak_t = el, t
            if el <= min_el:
                los_time = self._bisect_crossing(prev_t, t, rising=False)
                break
            prev_t = t
            t += step

        if los_time is None:
            los_time = min(t, end_t)      # still in contact at the search edge

        # ── Phase 3: refine the peak, which the coarse step may have missed ─
        for probe in (peak_t - step / 2, peak_t + step / 2,
                      peak_t - step / 4, peak_t + step / 4):
            if aos_time <= probe <= los_time:
                el = self._elevation_at(probe)
                evaluations += 1
                if el is not None and el > max_el:
                    max_el = el

        duration = (los_time - aos_time).total_seconds()
        print(f"[ContactCalc] {evaluations} propagations "
              f"(a flat 10 s scan would need {int(search_hours * 360)})")

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

    def is_in_contact_now(self, azel: Optional[dict] = None) -> bool:
        """
        Is the satellite above the horizon right now?

        Accepts an already-computed `azel` so callers that have just done the
        propagation do not pay for a second one — see get_contact_summary().
        """
        if azel is None:
            azel = self.get_current_azel()
        if azel is None:
            return False
        return azel["elevation_deg"] > GROUND_STATION["min_elevation_deg"]

    def get_contact_summary(self) -> dict:
        """
        Full summary for FastAPI /contact endpoint.

        PERFORMANCE: this ran THREE propagation passes for two answers —
        get_current_azel(), then find_next_contact(), then is_in_contact_now()
        which called get_current_azel() a second time for the same instant.
        The current position is now computed once and reused.
        """
        current  = self.get_current_azel()          # pass 1: now
        next_win = self.find_next_contact()         # pass 2: the search

        return {
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "current_azel":  current,
            # reuses `current` — this was a third propagation of the same epoch
            "in_contact_now": self.is_in_contact_now(current),
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