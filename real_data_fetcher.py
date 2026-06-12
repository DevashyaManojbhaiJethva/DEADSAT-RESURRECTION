"""
DeadSat Resurrection — Real Data Fetcher
AI-2 owned module

Pulls live data from:
  1. N2YO API    — live TLE, real-time AzEl, contact windows over Ahmedabad
                   Requires free API key: register at https://www.n2yo.com/login/
                   Set env var: N2YO_API_KEY=your_key

  2. SatNOGS DB  — real decoded telemetry + TLE
                   Requires free account token: https://db.satnogs.org/accounts/login/
                   Set env var: SATNOGS_TOKEN=your_token

  3. CelesTrak   — TLE fallback (no key needed)

  4. Hardcoded   — final fallback if all else fails

Priority chain (automatic):
  N2YO → SatNOGS → CelesTrak → hardcoded fallback
"""

import os
import json
import time
import urllib.request
import urllib.parse
from typing import Optional
from datetime import datetime, timezone

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

N2YO_BASE    = "https://api.n2yo.com/rest/v1/satellite"
SATNOGS_BASE = "https://db.satnogs.org/api"

# Ahmedabad ground station
GS_LAT = 23.0225
GS_LON = 72.5714
GS_ALT = 53       # metres

# Default satellites
NOAA_18_ID   = 28654   # 137 MHz NOAA downlink — visible on RTL-SDR
CARTOSAT3_ID = 44233   # ISRO — thematic match
ISS_ID       = 25544   # always active


# ──────────────────────────────────────────────
# N2YO Client
# ──────────────────────────────────────────────

class N2YOClient:
    """
    N2YO REST API v1.
    Free tier limits: 1000 TLE / 1000 positions / 100 passes per hour.
    Get API key: n2yo.com → register → profile → generate key.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("N2YO_API_KEY", "")
        if not self.api_key:
            print("[N2YO] No API key — set N2YO_API_KEY env var for live data")

    def _get(self, path: str) -> Optional[dict]:
        if not self.api_key:
            return None
        url = f"{N2YO_BASE}/{path}&apiKey={self.api_key}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DeadSat/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            print(f"[N2YO] Request failed: {e}")
            return None

    def get_tle(self, norad_id: int) -> Optional[dict]:
        data = self._get(f"tle/{norad_id}")
        if not data or "tle" not in data:
            return None
        lines = data["tle"].split("\r\n")
        if len(lines) < 2:
            return None
        return {
            "name":   data["info"]["satname"],
            "line1":  lines[0].strip(),
            "line2":  lines[1].strip(),
            "source": "n2yo",
        }

    def get_current_azel(self, norad_id: int) -> Optional[dict]:
        """Live AzEl over Ahmedabad right now."""
        path = f"positions/{norad_id}/{GS_LAT}/{GS_LON}/{GS_ALT}/1"
        data = self._get(path)
        if not data or "positions" not in data or not data["positions"]:
            return None
        p = data["positions"][0]
        return {
            "azimuth_deg":   round(p["azimuth"], 2),
            "elevation_deg": round(p["elevation"], 2),
            "altitude_km":   round(p["sataltitude"], 2),
            "sat_lat":       round(p["satlatitude"], 4),
            "sat_lon":       round(p["satlongitude"], 4),
            "timestamp":     p["timestamp"],
            "source":        "n2yo_live",
        }

    def get_radio_passes(self, norad_id: int, days: int = 2,
                         min_elevation: int = 5) -> Optional[list]:
        """Upcoming contact windows over Ahmedabad."""
        path = f"radiopasses/{norad_id}/{GS_LAT}/{GS_LON}/{GS_ALT}/{days}/{min_elevation}"
        data = self._get(path)
        if not data or "passes" not in data:
            return None
        enriched = []
        for p in data["passes"]:
            enriched.append({
                "aos":               datetime.fromtimestamp(p["startUTC"], tz=timezone.utc).isoformat(),
                "los":               datetime.fromtimestamp(p["endUTC"],   tz=timezone.utc).isoformat(),
                "aos_utc":           p["startUTC"],
                "los_utc":           p["endUTC"],
                "max_elevation_deg": p["maxEl"],
                "start_compass":     p.get("startAzCompass"),
                "duration_seconds":  p["endUTC"] - p["startUTC"],
                "source":            "n2yo",
            })
        print(f"[N2YO] {len(enriched)} radio passes found for NORAD {norad_id}")
        return enriched

    def get_next_pass(self, norad_id: int) -> Optional[dict]:
        passes = self.get_radio_passes(norad_id, days=2)
        if not passes:
            return None
        now_ts = int(time.time())
        future = [p for p in passes if p["aos_utc"] > now_ts]
        return future[0] if future else passes[0]


# ──────────────────────────────────────────────
# SatNOGS Client
# ──────────────────────────────────────────────

class SatNOGSClient:
    """
    SatNOGS DB REST API.
    Requires free account token: https://db.satnogs.org/accounts/login/
    Set env var: SATNOGS_TOKEN=your_token
    CC BY-SA licensed data.
    """

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("SATNOGS_TOKEN", "")
        if not self.token:
            print("[SatNOGS] No token — set SATNOGS_TOKEN env var for real telemetry")

    def _get(self, endpoint: str, params: dict = {}) -> Optional[dict]:
        if not self.token:
            return None
        qs  = urllib.parse.urlencode(params)
        url = f"{SATNOGS_BASE}/{endpoint}/?{qs}" if qs else f"{SATNOGS_BASE}/{endpoint}/"
        headers = {
            "User-Agent":    "DeadSat/1.0",
            "Accept":        "application/json",
            "Authorization": f"Token {self.token}",
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            print(f"[SatNOGS] Request failed: {e} | endpoint: {endpoint}")
            return None

    def get_tle(self, norad_id: int) -> Optional[dict]:
        data = self._get("tle", {"satellite": norad_id})
        if not data:
            return None
        entries = data if isinstance(data, list) else data.get("results", [])
        if not entries:
            return None
        e = entries[0]
        return {
            "name":   e.get("tle0", f"SAT-{norad_id}").strip(),
            "line1":  e.get("tle1", "").strip(),
            "line2":  e.get("tle2", "").strip(),
            "source": "satnogs",
        }

    def get_telemetry(self, norad_id: int, limit: int = 100) -> list:
        """
        Real decoded telemetry frames received by SatNOGS ground stations.
        Returns list of frame dicts, each with a 'decoded' field containing
        the satellite's actual beacon data.
        """
        data = self._get("telemetry", {"satellite": norad_id, "format": "json"})
        if not data:
            return []
        frames = data if isinstance(data, list) else data.get("results", [])
        print(f"[SatNOGS] Got {len(frames)} real telemetry frames for NORAD {norad_id}")
        return frames[:limit]

    def extract_telemetry_baselines(self, frames: list) -> dict:
        """
        Parse real SatNOGS frames to extract realistic emulator baseline values.
        Maps common beacon field names to our telemetry schema.
        """
        if not frames:
            return {}

        buckets = {
            "battery_pct":   [],
            "obc_temp_c":    [],
            "power_w":       [],
            "bus_voltage_v": [],
        }
        field_map = {
            "battery_pct":   ["battery_voltage_mv", "bat_voltage", "battery_pct",
                               "batt_volt", "eps_battery_voltage", "battery"],
            "obc_temp_c":    ["obc_temperature", "cpu_temp", "internal_temp",
                               "temperature_obc", "temp_obc", "obc_temp"],
            "power_w":       ["solar_power", "eps_solar_power", "solar_current_ma",
                               "solar_voltage", "power_generated"],
            "bus_voltage_v": ["bus_voltage", "eps_bus_voltage", "5v_bus",
                               "3v3_bus", "bus_volt"],
        }

        for frame in frames:
            decoded = frame.get("decoded") or {}
            for our_field, candidates in field_map.items():
                for candidate in candidates:
                    val = decoded.get(candidate)
                    if val is None:
                        continue
                    try:
                        fval = float(val)
                        if our_field == "battery_pct" and 0 < fval < 100000:
                            if fval > 100: fval = min(100, fval / 42.0)
                            buckets[our_field].append(fval)
                        elif our_field == "obc_temp_c" and -50 < fval < 150:
                            buckets[our_field].append(fval)
                        elif our_field == "power_w" and 0 < fval < 10000:
                            if fval > 1000: fval /= 1000.0
                            buckets[our_field].append(fval)
                        elif our_field == "bus_voltage_v" and 0 < fval < 100:
                            if fval > 50: fval /= 1000.0
                            buckets[our_field].append(fval)
                    except (ValueError, TypeError):
                        pass

        result = {}
        for field, values in buckets.items():
            if values:
                result[field] = round(sum(values) / len(values), 2)
                print(f"[SatNOGS] Baseline {field} = {result[field]} (n={len(values)})")
        return result


# ──────────────────────────────────────────────
# Combined Fetcher
# ──────────────────────────────────────────────

class RealDataFetcher:
    """
    Single entry point for all real satellite data.
    Handles priority chain automatically: N2YO → SatNOGS → CelesTrak → fallback.
    """

    # Hardcoded fallback TLE (NOAA-18)
    FALLBACK_TLE = {
        "name":   "NOAA 18 (FALLBACK)",
        "line1":  "1 28654U 05018A   26158.50000000  .00000057  00000-0  52000-4 0  9991",
        "line2":  "2 28654  98.8820 217.4320 0013900  87.1200 273.1100 14.10120001 07823",
        "source": "fallback",
    }

    def __init__(self, n2yo_api_key: Optional[str] = None,
                 satnogs_token: Optional[str] = None,
                 norad_id: int = NOAA_18_ID):
        self.norad_id  = norad_id
        self.n2yo      = N2YOClient(api_key=n2yo_api_key)
        self.satnogs   = SatNOGSClient(token=satnogs_token)
        self._tle_cache: Optional[dict] = None
        self._tle_ts:    float          = 0
        self._cache_ttl: float          = 3600  # 1 hour

    def get_tle(self) -> dict:
        """TLE with 1-hour cache. Priority: N2YO → SatNOGS → CelesTrak → fallback."""
        now = time.time()
        if self._tle_cache and (now - self._tle_ts) < self._cache_ttl:
            return self._tle_cache

        # 1. N2YO
        if self.n2yo.api_key:
            tle = self.n2yo.get_tle(self.norad_id)
            if tle and tle["line1"]:
                self._tle_cache, self._tle_ts = tle, now
                return tle

        # 2. SatNOGS
        if self.satnogs.token:
            tle = self.satnogs.get_tle(self.norad_id)
            if tle and tle["line1"]:
                self._tle_cache, self._tle_ts = tle, now
                return tle

        # 3. Local CSV catalog (712 real satellites — no network needed)
        try:
            from satellite_catalog import get_catalog
            tle = get_catalog().get_tle(self.norad_id)
            if tle and tle["line1"]:
                self._tle_cache, self._tle_ts = tle, now
                return tle
        except Exception as e:
            print(f"[RealData] Catalog TLE failed: {e}")

        # 4. CelesTrak
        try:
            url = f"https://celestrak.org/SPACETRACK/query/GP.php?CATNR={self.norad_id}&FORMAT=TLE"
            req = urllib.request.Request(url, headers={"User-Agent": "DeadSat/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                lines = resp.read().decode().strip().splitlines()
                if len(lines) >= 3:
                    tle = {"name": lines[0].strip(), "line1": lines[1].strip(),
                           "line2": lines[2].strip(), "source": "celestrak"}
                    print(f"[RealData] TLE from CelesTrak: {tle['name']}")
                    self._tle_cache, self._tle_ts = tle, now
                    return tle
        except Exception as e:
            print(f"[RealData] CelesTrak failed: {e}")

        # 5. Hardcoded fallback
        print("[RealData] Using hardcoded fallback TLE")
        return self.FALLBACK_TLE

    def get_contact_summary(self) -> dict:
        """
        Full contact summary for /contact endpoint.
        N2YO if key set, else sgp4 with best available TLE.
        """
        result = {
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "satellite":     f"NORAD-{self.norad_id}",
            "ground_station":"Ahmedabad Ground Station",
        }

        if self.n2yo.api_key:
            azel      = self.n2yo.get_current_azel(self.norad_id)
            next_pass = self.n2yo.get_next_pass(self.norad_id)
            result["source"]         = "n2yo_live"
            result["current_azel"]   = azel
            result["in_contact_now"] = bool(azel and azel["elevation_deg"] > 5) if azel else False
            result["next_window"]    = next_pass
        else:
            # Fall back to sgp4 with best TLE we have
            try:
                from contact_calculator import ContactCalculator
                tle  = self.get_tle()
                calc = ContactCalculator(norad_id=self.norad_id)
                calc.tle = tle
                from sgp4.api import Satrec
                calc.sat = Satrec.twoline2rv(tle["line1"], tle["line2"])
                summary  = calc.get_contact_summary()
                summary["source"] = f"sgp4+{tle['source']}"
                return summary
            except Exception as e:
                result["source"] = "error"
                result["error"]  = str(e)

        return result

    def get_satnogs_baselines(self, limit: int = 50) -> dict:
        """Pull real SatNOGS telemetry and return baseline values for emulator seeding."""
        if not self.satnogs.token:
            print("[RealData] SATNOGS_TOKEN not set — skipping real telemetry baselines")
            return {}
        frames = self.satnogs.get_telemetry(self.norad_id, limit=limit)
        return self.satnogs.extract_telemetry_baselines(frames)


# ──────────────────────────────────────────────
# Smoke test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    n2yo_key     = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("N2YO_API_KEY", "")
    satnogs_tok  = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SATNOGS_TOKEN", "")

    fetcher = RealDataFetcher(
        n2yo_api_key=n2yo_key,
        satnogs_token=satnogs_tok,
        norad_id=NOAA_18_ID,
    )

    print("\n=== TLE (priority chain) ===")
    tle = fetcher.get_tle()
    print(f"  Name:   {tle['name']}")
    print(f"  Source: {tle['source']}")

    print("\n=== Contact Summary ===")
    summary = fetcher.get_contact_summary()
    print(json.dumps(summary, indent=2, default=str))

    if satnogs_tok:
        print("\n=== SatNOGS Real Telemetry Baselines ===")
        baselines = fetcher.get_satnogs_baselines(limit=30)
        if baselines:
            for k, v in baselines.items():
                print(f"  {k}: {v}")
        else:
            print("  No decoded frames found for this satellite")
    else:
        print("\n=== SatNOGS skipped (no token) ===")
        print("  Get token: db.satnogs.org → login → profile → API token")
        print("  Then: python real_data_fetcher.py YOUR_N2YO_KEY YOUR_SATNOGS_TOKEN")