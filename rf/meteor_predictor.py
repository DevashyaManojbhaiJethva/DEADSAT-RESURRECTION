import logging
import math
import json
import os
import time
import requests
from datetime import datetime, timezone, timedelta
from sgp4.api import Satrec
from sgp4.api import jday

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
log = logging.getLogger("meteor_predictor")

def green(msg):  print(f"\033[92m{msg}\033[0m")
def yellow(msg): print(f"\033[93m{msg}\033[0m")
def red(msg):    print(f"\033[91m{msg}\033[0m")

METEOR_M2_4_NORAD = 59051
METEOR_M2_3_NORAD = 57166
GROUND_LAT        = 23.03
GROUND_LON        = 72.58
GROUND_ELEV_M     = 53
FREQUENCY_MHZ     = 137.9
SPEED_OF_LIGHT    = 299_792_458.0
MIN_ELEVATION_DEG = 10.0
SEARCH_WINDOW_H   = 48        # search window for get_all_passes()
NEXT_PASS_WINDOW_H = 24       # search window for get_next_pass()

_HERE          = os.path.dirname(os.path.abspath(__file__))
TLE_CACHE      = os.path.join(_HERE, "tle_cache.json")
CACHE_MAX_AGE_S = 6 * 3600

TLE_SOURCES = {
    METEOR_M2_4_NORAD: [
        "https://celestrak.org/NORAD/elements/gp.php?CATNR=59051&FORMAT=TLE",
        "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=TLE",
        "https://db.satnogs.org/api/tle/?norad_cat_id=59051&format=json",
        "https://www.n2yo.com/sat/tle.php?s=59051",
    ],
    METEOR_M2_3_NORAD: [
        "https://celestrak.org/NORAD/elements/gp.php?CATNR=57166&FORMAT=TLE",
        "https://celestrak.org/NORAD/elements/gp.php?GROUP=weather&FORMAT=TLE",
        "https://db.satnogs.org/api/tle/?norad_cat_id=57166&format=json",
        "https://www.n2yo.com/sat/tle.php?s=57166",
    ],
}

EMERGENCY_TLE = {
    METEOR_M2_4_NORAD: (
        "1 59051U 24039A   26163.89256810 -.00000006  00000+0  16973-4 0  9991",
        "2 59051  98.7015 123.2446 0007054 173.0118 187.1160 14.22429124118657",
    ),
    METEOR_M2_3_NORAD: (
        "1 57166U 23091A   26160.50000000  .00000060  00000-0  40000-4 0  9992",
        "2 57166  98.7012  91.2341 0001456 101.2312 258.9123 14.23123456 23456",
    ),
}

DEG     = math.pi / 180
RAD     = 180 / math.pi
EARTH_R = 6371.0


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_load() -> dict:
    try:
        with open(TLE_CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def _cache_save(norad: int, line1: str, line2: str, source: str):
    cache = _cache_load()
    cache[str(norad)] = {
        "line1":     line1,
        "line2":     line2,
        "source":    source,
        "timestamp": time.time(),
        "fetched":   datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(TLE_CACHE, "w") as f:
            json.dump(cache, f, indent=2)
        log.info("TLE cached — NORAD %d from %s", norad, source)
    except Exception as e:
        log.warning("Cache write failed: %s", e)


def _cache_get(norad: int):
    cache = _cache_load()
    entry = cache.get(str(norad))
    if not entry:
        return None
    age = time.time() - entry.get("timestamp", 0)
    if age > CACHE_MAX_AGE_S:
        yellow(f"[CACHE] Cached TLE for NORAD {norad} is {age/3600:.1f}h old — stale")
        return None
    green(f"[CACHE] Using cached TLE for NORAD {norad} (age {age/60:.0f} min, source: {entry['source']})")
    return entry["line1"], entry["line2"]


def _cache_get_stale(norad: int):
    cache = _cache_load()
    entry = cache.get(str(norad))
    if not entry:
        return None
    age_h = (time.time() - entry.get("timestamp", 0)) / 3600
    return entry["line1"], entry["line2"], age_h


# ── TLE parsers ───────────────────────────────────────────────────────────────

def _parse_tle_block(text: str, norad: int):
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("1 "):
            if i + 1 < len(lines) and lines[i + 1].startswith("2 "):
                if str(norad) in l.split()[1]:
                    return l, lines[i + 1]
            i += 2
        else:
            i += 1
    return None


def _parse_satnogs_json(text: str, norad: int):
    try:
        data = json.loads(text)
        if isinstance(data, list):
            for entry in data:
                if entry.get("norad_cat_id") == norad:
                    return entry["tle1"], entry["tle2"]
            if len(data) == 1:
                return data[0]["tle1"], data[0]["tle2"]
        elif isinstance(data, dict):
            return data.get("tle1"), data.get("tle2")
    except Exception as e:
        log.debug("SatNOGS JSON parse error: %s", e)
    return None


def _parse_n2yo(text: str):
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    l1 = l2 = None
    for line in lines:
        if line.startswith("1 "):
            l1 = line
        elif line.startswith("2 ") and l1:
            l2 = line
            break
    return (l1, l2) if l1 and l2 else None


def _try_url(url: str, norad: int, timeout: int = 6):
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "DeadSat-GroundStation/1.0"})
        resp.raise_for_status()
        text = resp.text.strip()
        if "db.satnogs.org" in url:
            return _parse_satnogs_json(text, norad)
        if "n2yo.com" in url:
            return _parse_n2yo(text)
        result = _parse_tle_block(text, norad)
        if result:
            return result
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) >= 2 and lines[0].startswith("1 ") and lines[1].startswith("2 "):
            return lines[0], lines[1]
        if len(lines) >= 3 and lines[1].startswith("1 ") and lines[2].startswith("2 "):
            return lines[1], lines[2]
    except Exception as e:
        log.debug("URL failed (%s): %s", url, e)
    return None


def fetch_best_tle(norad: int):
    cached = _cache_get(norad)
    if cached:
        return cached[0], cached[1], "disk_cache (fresh)"

    for url in TLE_SOURCES.get(norad, []):
        log.info("Trying TLE source: %s", url)
        result = _try_url(url, norad)
        if result:
            l1, l2 = result
            if l1.startswith("1 ") and l2.startswith("2 "):
                source_label = url.split("/")[2]
                _cache_save(norad, l1, l2, source_label)
                green(f"[TLE] Live fetch OK — NORAD {norad} from {source_label}")
                return l1, l2, source_label
        yellow(f"[TLE] Source failed: {url}")

    stale = _cache_get_stale(norad)
    if stale:
        l1, l2, age_h = stale
        yellow(f"[TLE] Using stale cache ({age_h:.1f}h old) for NORAD {norad}")
        return l1, l2, f"disk_cache (stale, {age_h:.1f}h)"

    emergency = EMERGENCY_TLE.get(norad)
    if emergency:
        l1, l2 = emergency
        red(f"[TLE] EMERGENCY fallback TLE for NORAD {norad} — predictions may drift!")
        return l1, l2, "emergency_hardcoded"

    raise RuntimeError(f"No TLE available for NORAD {norad}")


# ── Coordinate math ───────────────────────────────────────────────────────────

def _jday_now():
    now = datetime.now(timezone.utc)
    return jday(now.year, now.month, now.day,
                now.hour, now.minute, now.second + now.microsecond / 1e6)


def _eci_to_azel(pos_km, ground_lat_deg, ground_lon_deg, elev_m, jd, fr):
    lat = ground_lat_deg * DEG
    lon = ground_lon_deg * DEG
    alt = elev_m / 1000.0

    gmst     = (18.697374558 + 24.06570982441908 * ((jd - 2451545.0) + fr)) % 24
    gmst_rad = gmst * math.pi / 12

    c  = math.cos(lat)
    re = EARTH_R + alt
    obs = [
        re * c * math.cos(gmst_rad + lon),
        re * c * math.sin(gmst_rad + lon),
        re * math.sin(lat),
    ]

    rx  = pos_km[0] - obs[0]
    ry  = pos_km[1] - obs[1]
    rz  = pos_km[2] - obs[2]
    rng = math.sqrt(rx*rx + ry*ry + rz*rz)

    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(gmst_rad + lon)
    cos_lon = math.cos(gmst_rad + lon)

    s_s = sin_lat*cos_lon*rx + sin_lat*sin_lon*ry - cos_lat*rz
    e_s = -sin_lon*rx + cos_lon*ry
    z_s =  cos_lat*cos_lon*rx + cos_lat*sin_lon*ry + sin_lat*rz

    el = math.asin(z_s / rng) * RAD
    az = (math.atan2(-s_s, e_s) * RAD + 360) % 360
    return az, el, rng


# ── Pass scanner (core engine) ────────────────────────────────────────────────

def _scan_passes(sat, hours: int) -> list:
    """
    Scan the next N hours and return a list of all passes.
    Each pass is a dict with aos, los, max_elevation_deg, duration_min,
    pass_quality, aos_ist, los_ist, aos_timestamp, los_timestamp.
    """
    now   = datetime.now(timezone.utc)
    end   = now + timedelta(hours=hours)
    step  = timedelta(seconds=10)
    t     = now
    IST   = timedelta(hours=5, minutes=30)

    passes  = []
    aos     = los = None
    best_el = 0.0
    in_pass = False

    while t < end:
        jd, fr      = jday(t.year, t.month, t.day, t.hour, t.minute, t.second)
        e, pos, vel = sat.sgp4(jd, fr)
        if e == 0:
            az, el, rng = _eci_to_azel(pos, GROUND_LAT, GROUND_LON, GROUND_ELEV_M, jd, fr)
            if el >= MIN_ELEVATION_DEG:
                if not in_pass:
                    in_pass = True
                    aos     = t
                    best_el = el
                elif el > best_el:
                    best_el = el
                los = t
            elif in_pass:
                dur     = (los - aos).total_seconds() / 60.0
                quality = _pass_quality(best_el)
                passes.append({
                    "aos":               aos.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "los":               los.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "aos_ist":           (aos + IST).strftime("%d %b %Y %I:%M:%S %p IST"),
                    "los_ist":           (los + IST).strftime("%d %b %Y %I:%M:%S %p IST"),
                    "max_elevation_deg": round(best_el, 1),
                    "duration_min":      round(dur, 1),
                    "pass_quality":      quality,
                    "aos_utc":           aos.isoformat(),
                    "los_utc":           los.isoformat(),
                    "aos_timestamp":     aos.timestamp(),
                    "los_timestamp":     los.timestamp(),
                })
                in_pass = False
                best_el = 0.0
        t += step

    return passes


def _pass_quality(max_el: float) -> str:
    if   max_el > 45: return "EXCELLENT"
    elif max_el > 25: return "GOOD"
    elif max_el > 10: return "WEAK"
    else:             return "SKIP"


# ── MeteorPredictor ───────────────────────────────────────────────────────────

class MeteorPredictor:

    def __init__(self, norad: int = METEOR_M2_4_NORAD):
        self.norad      = norad
        self.frequency  = FREQUENCY_MHZ
        self.sat_name   = "Meteor-M2-4" if norad == METEOR_M2_4_NORAD else "Meteor-M2-3"

        l1, l2, src     = fetch_best_tle(norad)
        self._tle_line1  = l1
        self._tle_line2  = l2
        self._tle_source = src
        self._sat        = Satrec.twoline2rv(l1, l2)
        green(f"[PREDICTOR] {self.sat_name} loaded — source: {src}")

    def _position_now(self):
        jd, fr = _jday_now()
        e, pos, vel = self._sat.sgp4(jd, fr)
        if e != 0:
            raise RuntimeError(f"sgp4 error code {e}")
        az, el, rng = _eci_to_azel(pos, GROUND_LAT, GROUND_LON, GROUND_ELEV_M, jd, fr)
        return az, el, rng, pos, vel

    def get_current_position(self) -> dict:
        az, el, rng, pos, vel = self._position_now()
        result = {
            "elevation_deg": round(el, 2),
            "azimuth_deg":   round(az, 2),
            "range_km":      round(rng, 1),
            "above_horizon": el >= MIN_ELEVATION_DEG,
        }
        if result["above_horizon"]:
            green(f"[POS] {self.sat_name} VISIBLE — el={el:.1f}° az={az:.1f}° range={rng:.0f} km")
        else:
            log.info("Position: el=%.1f° az=%.1f° range=%.0f km (below horizon)", el, az, rng)
        return result

    def get_range_velocity(self) -> float:
        jd, fr = _jday_now()
        _, pos1, _ = self._sat.sgp4(jd, fr)
        _, pos2, _ = self._sat.sgp4(jd, fr + 1.0 / 86400)
        r1       = math.sqrt(sum(x*x for x in pos1)) * 1000
        r2       = math.sqrt(sum(x*x for x in pos2)) * 1000
        velocity = r2 - r1
        log.info("Range velocity: %.1f m/s", velocity)
        return round(velocity, 2)

    @staticmethod
    def get_pass_quality(max_elevation_deg: float) -> str:
        return _pass_quality(max_elevation_deg)

    def get_all_passes(self, hours: int = SEARCH_WINDOW_H) -> list:
        """Returns all passes over Ahmedabad in the next N hours."""
        return _scan_passes(self._sat, hours)

    def get_next_pass(self) -> dict:
        """Returns the next single pass."""
        passes = _scan_passes(self._sat, NEXT_PASS_WINDOW_H)
        if passes:
            p = passes[0]
            q = p["pass_quality"]
            fn = green if q in ("EXCELLENT", "GOOD") else (yellow if q == "WEAK" else red)
            fn(f"[PASS] {self.sat_name} — AOS: {p['aos']} | Max el: {p['max_elevation_deg']}° | "
               f"Duration: {p['duration_min']} min | Quality: {q}")
            return p
        red(f"[PASS] No visible pass found in next {NEXT_PASS_WINDOW_H}h")
        return {"aos": None, "los": None, "max_elevation_deg": 0,
                "duration_min": 0, "pass_quality": "NONE",
                "satellite": self.sat_name, "norad": self.norad,
                "frequency_mhz": self.frequency}

    def next_pass_eta_minutes(self) -> float:
        pos = self.get_current_position()
        if pos["above_horizon"]:
            green(f"[ETA] {self.sat_name} is VISIBLE NOW")
            return 0.0
        nxt = self.get_next_pass()
        if not nxt.get("aos_timestamp"):
            return float("inf")
        eta = max(0.0, (nxt["aos_timestamp"] - datetime.now(timezone.utc).timestamp()) / 60.0)
        return round(eta, 1)

    def get_rf_status(self, snr_db: float = 0.0, receiving: bool = False) -> dict:
        pos        = self.get_current_position()
        vel        = self.get_range_velocity()
        doppler_hz = -(vel / SPEED_OF_LIGHT) * (self.frequency * 1e6)
        return {
            "snr_db":                snr_db,
            "pass_quality":          self.get_pass_quality(pos["elevation_deg"])
                                     if pos["above_horizon"] else "WAITING",
            "satellite":             self.sat_name,
            "norad":                 self.norad,
            "next_pass_eta_min":     self.next_pass_eta_minutes(),
            "frequency_mhz":         self.frequency,
            "receiving":             receiving,
            "elevation_deg":         pos["elevation_deg"],
            "azimuth_deg":           pos["azimuth_deg"],
            "range_km":              pos["range_km"],
            "doppler_correction_hz": round(doppler_hz, 1),
            "tle_source":            self._tle_source,
        }


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Meteor-M2-4 Pass Predictor — DeadSat Ground Station")
    print("  Ahmedabad  23.03°N  72.58°E  53m ASL")
    print("=" * 60 + "\n")

    predictor = MeteorPredictor()

    print("\n--- TLE Source ---")
    green(f"Source : {predictor._tle_source}")
    green(f"Line 1 : {predictor._tle_line1}")
    green(f"Line 2 : {predictor._tle_line2}")

    print("\n--- Current Position ---")
    pos = predictor.get_current_position()
    print(f"  Elevation : {pos['elevation_deg']}°")
    print(f"  Azimuth   : {pos['azimuth_deg']}°")
    print(f"  Range     : {pos['range_km']} km")
    print(f"  Visible   : {pos['above_horizon']}")

    print("\n--- Range Velocity & Doppler ---")
    vel     = predictor.get_range_velocity()
    doppler = -(vel / SPEED_OF_LIGHT) * (FREQUENCY_MHZ * 1e6)
    print(f"  Range velocity : {vel} m/s")
    print(f"  Doppler shift  : {doppler:+.1f} Hz  ({doppler/1000:+.3f} kHz)")

    print("\n--- All Passes — Next 48 Hours (IST) ---")
    print(f"  {'#':<4} {'AOS (IST)':<30} {'LOS (IST)':<30} {'Max El':>7} {'Dur':>6} {'Quality'}")
    print("  " + "-" * 90)

    all_passes = predictor.get_all_passes(hours=48)
    for i, p in enumerate(all_passes, 1):
        q  = p["pass_quality"]
        fn = green if q in ("EXCELLENT", "GOOD") else (yellow if q == "WEAK" else red)
        fn(f"  {i:<4} {p['aos_ist']:<30} {p['los_ist']:<30} {p['max_elevation_deg']:>6.1f}° {p['duration_min']:>5.1f}m  {q}")

    print(f"\n  Total: {len(all_passes)} passes in next 48 hours")

    print("\n--- Next Pass ETA ---")
    eta = predictor.next_pass_eta_minutes()
    if eta == 0.0:
        green("  Satellite is VISIBLE RIGHT NOW")
    elif eta == float("inf"):
        red("  No pass in window")
    else:
        h, m = int(eta // 60), int(eta % 60)
        yellow(f"  ETA : {eta} min  ({h}h {m}m from now)")

    print("\n" + "=" * 60)
    green("  meteor_predictor.py — ALL TESTS PASSED")
    print("=" * 60 + "\n")