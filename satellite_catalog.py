"""
DeadSat Resurrection — Satellite Catalog
AI-2 owned module

Loads real orbital element data (GP format) from 3 CSV datasets:
  - data/input.csv       : 663 satellites (general catalog)
  - data/input__1_.csv   : 91  CubeSats
  - data/input__2_.csv   : 97  amateur radio satellites (OSCAR, ISS)

Provides:
  1. TLE generation from GP elements — replaces CelesTrak calls
  2. Orbital anomaly baselines for AI-1 classifier
  3. Satellite lookup by NORAD ID or name
  4. Contact calculator seeding with real orbital elements
"""

import csv
import math
import os
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────
# Data paths
# ──────────────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data"

CSV_FILES = [
    DATA_DIR / "input.csv",
    DATA_DIR / "input__1_.csv",
    DATA_DIR / "input__2_.csv",
]


# ──────────────────────────────────────────────
# TLE Line Builder from GP Elements
# ──────────────────────────────────────────────

def _tle_checksum(line: str) -> int:
    """Calculate TLE line checksum."""
    total = 0
    for ch in line[:-1]:
        if ch.isdigit():
            total += int(ch)
        elif ch == '-':
            total += 1
    return total % 10


def build_tle_from_gp(row: dict) -> Optional[dict]:
    """
    Build TLE line1 + line2 from GP orbital elements.
    GP format is what Space-Track exports — same data as TLE but in CSV.

    Returns: { name, line1, line2, source }
    """
    try:
        norad_id   = int(row["NORAD_CAT_ID"].strip())
        epoch_str  = row["EPOCH"].strip()
        inc        = float(row["INCLINATION"])
        raan       = float(row["RA_OF_ASC_NODE"])
        ecc        = float(row["ECCENTRICITY"])
        aop        = float(row["ARG_OF_PERICENTER"])
        ma         = float(row["MEAN_ANOMALY"])
        mm         = float(row["MEAN_MOTION"])
        bstar_raw  = row["BSTAR"].strip()
        mm_dot_raw = row["MEAN_MOTION_DOT"].strip()
        mm_ddot    = float(row["MEAN_MOTION_DDOT"])
        rev        = int(row["REV_AT_EPOCH"])
        el_set     = int(row["ELEMENT_SET_NO"])
        obj_id     = row["OBJECT_ID"].strip()
        cls_type   = row["CLASSIFICATION_TYPE"].strip()

        # Parse epoch: "2026-06-10T14:46:49.161792" → TLE epoch "26161.61585604"
        from datetime import datetime, timezone
        epoch_dt = datetime.fromisoformat(epoch_str.replace("Z", "+00:00"))
        year_2d  = epoch_dt.year % 100
        day_of_year = epoch_dt.timetuple().tm_yday
        frac_day = (epoch_dt.hour * 3600 + epoch_dt.minute * 60 +
                    epoch_dt.second + epoch_dt.microsecond / 1e6) / 86400.0
        epoch_tle = f"{year_2d:02d}{day_of_year:03d}{frac_day:.8f}"[:-1]  # trim to fit

        # Format BSTAR in TLE scientific notation
        def _fmt_tle_float(val_str):
            try:
                val = float(val_str)
                if val == 0:
                    return " 00000+0"
                exp = math.floor(math.log10(abs(val)))
                mant = val / (10 ** exp)
                sign = "-" if mant < 0 else " "
                exp_sign = "+" if exp >= 0 else "-"
                return f"{sign}{abs(mant):.5f}".replace("0.", "").replace(".", "") + f"{exp_sign}{abs(exp)}"
            except Exception:
                return " 00000+0"

        bstar_fmt  = _fmt_tle_float(bstar_raw)
        mm_dot_fmt = _fmt_tle_float(mm_dot_raw)

        # International designator from OBJECT_ID e.g. "1964-083D" → "64083D "
        try:
            yr, rest = obj_id.split("-")
            intl_desig = f"{yr[2:]}{rest:<6}"[:8]
        except Exception:
            intl_desig = "        "

        # LINE 1
        # 1 NNNNNC NNNNNAAA NNNNN.NNNNNNNN +.NNNNNNNN +NNNNN-N +NNNNN-N N NNNNN
        line1_body = (
            f"1 {norad_id:05d}{cls_type} "
            f"{intl_desig} "
            f"{epoch_tle:<14} "
            f"{mm_dot_fmt} "
            f"00000-0 "
            f"{bstar_fmt} "
            f"0 {el_set:4d}"
        )
        # Pad to 68 chars then add checksum
        line1_body = line1_body[:68].ljust(68)
        line1 = line1_body + str(_tle_checksum(line1_body + "0"))

        # LINE 2
        # 2 NNNNN NNN.NNNN NNN.NNNN NNNNNNN NNN.NNNN NNN.NNNN NN.NNNNNNNNNNNNNN
        ecc_str = f"{ecc:.7f}"[2:]  # strip "0."
        line2_body = (
            f"2 {norad_id:05d} "
            f"{inc:8.4f} "
            f"{raan:8.4f} "
            f"{ecc_str} "
            f"{aop:8.4f} "
            f"{ma:8.4f} "
            f"{mm:11.8f}{rev:5d}"
        )
        line2_body = line2_body[:68].ljust(68)
        line2 = line2_body + str(_tle_checksum(line2_body + "0"))

        return {
            "name":     row["OBJECT_NAME"].strip(),
            "line1":    line1,
            "line2":    line2,
            "norad_id": norad_id,
            "source":   "csv_gp",
        }

    except Exception as e:
        return None


# ──────────────────────────────────────────────
# Catalog Loader
# ──────────────────────────────────────────────

class SatelliteCatalog:
    """
    Loads all 3 CSV datasets into a unified catalog.
    Provides TLE generation, anomaly baselines, and satellite lookup.
    """

    def __init__(self):
        self._catalog: dict[int, dict] = {}   # NORAD_ID → row
        self._loaded = False

    def load(self) -> int:
        """Load all CSV files. Returns number of unique satellites loaded."""
        seen = set()
        for csv_path in CSV_FILES:
            if not csv_path.exists():
                print(f"[Catalog] WARNING: {csv_path} not found")
                continue
            with open(csv_path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        nid = int(row["NORAD_CAT_ID"].strip())
                        if nid not in seen:
                            seen.add(nid)
                            self._catalog[nid] = row
                    except (ValueError, KeyError):
                        pass
        self._loaded = True
        print(f"[Catalog] Loaded {len(self._catalog)} unique satellites from {len(CSV_FILES)} CSV files")
        return len(self._catalog)

    def get_by_norad(self, norad_id: int) -> Optional[dict]:
        """Get raw GP row by NORAD ID."""
        if not self._loaded:
            self.load()
        return self._catalog.get(norad_id)

    def get_by_name(self, name: str) -> Optional[dict]:
        """Get raw GP row by partial name match (case-insensitive)."""
        if not self._loaded:
            self.load()
        name_lower = name.lower()
        for row in self._catalog.values():
            if name_lower in row["OBJECT_NAME"].lower():
                return row
        return None

    def get_tle(self, norad_id: int) -> Optional[dict]:
        """
        Build TLE from GP data for a satellite.
        Returns: { name, line1, line2, source: 'csv_gp' }
        """
        if not self._loaded:
            self.load()
        row = self._catalog.get(norad_id)
        if not row:
            return None
        tle = build_tle_from_gp(row)
        if tle:
            print(f"[Catalog] TLE built from GP data: {tle['name']} (NORAD {norad_id})")
        return tle

    def get_anomaly_baselines(self, norad_id: int) -> Optional[dict]:
        """
        Extract orbital anomaly baselines for AI-1 classifier.
        Returns nominal orbital parameter ranges for this satellite.
        A satellite deviating significantly from these = anomaly.
        """
        if not self._loaded:
            self.load()
        row = self._catalog.get(norad_id)
        if not row:
            return None
        try:
            mm        = float(row["MEAN_MOTION"])
            ecc       = float(row["ECCENTRICITY"])
            inc       = float(row["INCLINATION"])
            bstar     = float(row["BSTAR"])
            mm_dot    = float(row["MEAN_MOTION_DOT"])

            # Orbital period in minutes
            period_min = 1440.0 / mm if mm > 0 else 0

            # Altitude estimate (circular orbit approx)
            mu = 398600.4418   # km^3/s^2
            n_rad = mm * 2 * math.pi / 86400
            a_km  = (mu / (n_rad ** 2)) ** (1/3)
            alt_km = a_km - 6371.0

            return {
                "norad_id":          norad_id,
                "name":              row["OBJECT_NAME"].strip(),
                "mean_motion_nominal": mm,
                "mean_motion_dot":   mm_dot,
                "eccentricity":      ecc,
                "inclination_deg":   inc,
                "bstar":             bstar,
                "period_minutes":    round(period_min, 2),
                "altitude_km_approx": round(alt_km, 1),
                # Anomaly thresholds (±5% from nominal)
                "mm_threshold":      round(mm * 0.05, 6),
                "ecc_threshold":     max(0.001, ecc * 0.1),
                "source":            "csv_gp",
            }
        except Exception as e:
            print(f"[Catalog] Baseline error for NORAD {norad_id}: {e}")
            return None

    def get_all_baselines(self) -> list:
        """
        Get anomaly baselines for ALL satellites in catalog.
        Used to build the training dataset for AI-1's Isolation Forest.
        Returns list of baseline dicts.
        """
        if not self._loaded:
            self.load()
        baselines = []
        for nid in self._catalog:
            b = self.get_anomaly_baselines(nid)
            if b:
                baselines.append(b)
        print(f"[Catalog] Generated baselines for {len(baselines)} satellites")
        return baselines

    def export_training_csv(self, output_path: str) -> int:
        """
        Export a clean training CSV for AI-1's anomaly detector.
        Columns: norad_id, mean_motion, eccentricity, inclination,
                 bstar, mean_motion_dot, altitude_km, period_minutes
        Returns: number of rows written
        """
        if not self._loaded:
            self.load()

        baselines = self.get_all_baselines()
        if not baselines:
            return 0

        fieldnames = [
            "norad_id", "name", "mean_motion_nominal", "eccentricity",
            "inclination_deg", "bstar", "mean_motion_dot",
            "altitude_km_approx", "period_minutes"
        ]

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(baselines)

        print(f"[Catalog] Exported {len(baselines)} training rows to {output_path}")
        return len(baselines)

    def list_satellites(self, limit: int = 20) -> list:
        """List satellite names and NORAD IDs."""
        if not self._loaded:
            self.load()
        return [
            {"norad_id": nid, "name": row["OBJECT_NAME"].strip(), "inclination": row["INCLINATION"]}
            for nid, row in list(self._catalog.items())[:limit]
        ]

    def __len__(self):
        if not self._loaded:
            self.load()
        return len(self._catalog)


# ──────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────

_catalog_instance: Optional[SatelliteCatalog] = None

def get_catalog() -> SatelliteCatalog:
    global _catalog_instance
    if _catalog_instance is None:
        _catalog_instance = SatelliteCatalog()
        _catalog_instance.load()
    return _catalog_instance


# ──────────────────────────────────────────────
# Smoke test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    cat = SatelliteCatalog()
    cat.load()

    print(f"\n=== Catalog has {len(cat)} satellites ===")

    # Test TLE build for NOAA-18
    print("\n--- TLE from GP (NOAA-18, NORAD 28654) ---")
    tle = cat.get_tle(28654)
    if tle:
        print(f"  Name:  {tle['name']}")
        print(f"  Line1: {tle['line1']}")
        print(f"  Line2: {tle['line2']}")

    # Test TLE build for ISS
    print("\n--- TLE from GP (ISS, NORAD 25544) ---")
    tle_iss = cat.get_tle(25544)
    if tle_iss:
        print(f"  Name:  {tle_iss['name']}")
        print(f"  Line1: {tle_iss['line1']}")
        print(f"  Line2: {tle_iss['line2']}")

    # Test anomaly baselines
    print("\n--- Anomaly Baselines (NOAA-18) ---")
    b = cat.get_anomaly_baselines(28654)
    if b:
        for k, v in b.items():
            print(f"  {k}: {v}")

    # Export training CSV
    print("\n--- Exporting training CSV ---")
    n = cat.export_training_csv("/home/claude/deadsat/data/training_baselines.csv")
    print(f"  Exported {n} rows")

    # List first 5
    print("\n--- First 5 satellites ---")
    for s in cat.list_satellites(5):
        print(f"  NORAD {s['norad_id']:6d} | {s['name']}")