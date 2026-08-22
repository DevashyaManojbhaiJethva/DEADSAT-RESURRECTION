#!/usr/bin/env python3
"""
generate_dataset.py — propagation-based orbital series with injected faults
===========================================================================

WHY THIS EXISTS
---------------
`data/input.csv` + `input__1_.csv` + `input__2_.csv` are a *snapshot*: 854 rows
covering 712 satellites, and (with one exception) no satellite has two distinct
epochs. Measured on the raw files:

    REV_DELTA == 0            for 100% of rows
    ecc_delta == 0            for 100% of rows
    SOFTWARE_BUG 807 | FIRMWARE_CORRUPTION 23 | COMMAND_INJECTION 9
    SEU 0 | NORMAL 0

Three of the four rules in `assign_fault_labels()` are defined as CHANGES
BETWEEN CONSECUTIVE EPOCHS. With one epoch per satellite they can never fire,
and `rev_delta <= 0` then absorbs ~95% of the data as SOFTWARE_BUG.

Root cause, precisely: `prepare_dataframe()` computes

    df["REV_DELTA"] = df.groupby("NORAD_CAT_ID")["REV_AT_EPOCH"].diff().fillna(0)

`.diff()` yields NaN for the first row of every group and `.fillna(0)` turns
that into 0, which satisfies `rev_delta <= 0`. In a one-row-per-satellite
dataset EVERY row is a first row — so the 807 SOFTWARE_BUG labels are almost
entirely a warm-up artefact, not a signal. (This artefact still costs one row
per satellite in a multi-epoch dataset; see NOTES at the bottom.)

Labelled satellite fault telemetry is not publicly available, so synthetic
injection is the right approach. What was wrong was the SHAPE of the data, not
that it was synthetic. This script fixes the shape: it propagates each real
catalogue entry forward through many epochs so the series contains genuine
orbital dynamics, then stamps fault signatures onto the SERIES.

OFFLINE BY CONSTRUCTION
-----------------------
There is no network code path in this file. It does not import `requests`, and
it never contacts CelesTrak, Space-Track or N2YO. The three input CSVs are
local and propagation is local, so there is no `--refresh` flag to gate:
    grep -n "requests\|http\|urllib\|celestrak\|n2yo" generate_dataset.py
returns only this comment block.

USAGE
-----
    python generate_dataset.py                 # write data/synthetic_orbital_series.csv
    python generate_dataset.py --verify        # write, then check against the real labeller
    python generate_dataset.py --epochs 24 --seed 7 --verify
    python generate_dataset.py --propagator sgp4 --verify    # force real SGP4
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "models"))

# ---------------------------------------------------------------------------
# Thresholds — imported from the single source of truth, never re-typed.
# ---------------------------------------------------------------------------
try:
    from feature_spec import CONFIG  # models/feature_spec.py
except ImportError:  # pragma: no cover
    from models.feature_spec import CONFIG

TLE_AGE_STALE_H = CONFIG["tle_age_stale_hours"]          # 72.0
ECC_JUMP_THRESH = CONFIG["eccentricity_jump_threshold"]  # 0.01
BSTAR_THRESH    = CONFIG["bstar_anomaly_threshold"]      # 0.005
MMDOT_THRESH    = CONFIG["mean_motion_dot_threshold"]    # 0.001
SEQ_LEN         = CONFIG["seq_len"]                      # 8

INPUT_CSVS = ["data/input.csv", "data/input__1_.csv", "data/input__2_.csv"]
OUTPUT_CSV = "data/synthetic_orbital_series.csv"

# Column order of input.csv, preserved exactly.
INPUT_COLUMNS = [
    "OBJECT_NAME", "OBJECT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY",
    "INCLINATION", "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY",
    "EPHEMERIS_TYPE", "CLASSIFICATION_TYPE", "NORAD_CAT_ID", "ELEMENT_SET_NO",
    "REV_AT_EPOCH", "BSTAR", "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT",
]

CLASSES = ["NORMAL", "SEU", "SOFTWARE_BUG", "FIRMWARE_CORRUPTION",
           "COMMAND_INJECTION"]

MU_EARTH = 398600.4418   # km^3/s^2
R_EARTH  = 6378.137      # km
J2       = 1.08262668e-3


# ===========================================================================
# 1. LOAD + DEDUPLICATE
# ===========================================================================

def load_inputs(verbose: bool = True) -> pd.DataFrame:
    """Concatenate the three CSVs and drop duplicate (NORAD_CAT_ID, EPOCH)."""
    frames = []
    for rel in INPUT_CSVS:
        p = ROOT / rel
        if not p.exists():
            raise FileNotFoundError(f"missing input: {p}")
        frames.append(pd.read_csv(p))
    df = pd.concat(frames, ignore_index=True)
    raw = len(df)

    exact_dups = raw - len(df.drop_duplicates())
    df["EPOCH"] = pd.to_datetime(df["EPOCH"], errors="coerce", format="mixed")
    df = df.dropna(subset=["EPOCH", "NORAD_CAT_ID"])

    before = len(df)
    df = df.drop_duplicates(subset=["NORAD_CAT_ID", "EPOCH"], keep="first")
    key_dups = before - len(df)

    numeric = ["MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE",
               "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR", "MEAN_MOTION_DOT",
               "MEAN_MOTION_DDOT", "REV_AT_EPOCH", "NORAD_CAT_ID"]
    for c in numeric:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["MEAN_MOTION", "ECCENTRICITY", "INCLINATION"])
    df = df[df["MEAN_MOTION"] > 0]

    # One satellite genuinely has two epochs in the source; keep the newest.
    df = (df.sort_values(["NORAD_CAT_ID", "EPOCH"])
            .drop_duplicates(subset=["NORAD_CAT_ID"], keep="last")
            .reset_index(drop=True))

    if verbose:
        print(f"[load] {raw} raw rows from {len(INPUT_CSVS)} CSVs")
        print(f"[load]   {exact_dups} exact duplicate rows")
        print(f"[load]   {key_dups} duplicate (NORAD_CAT_ID, EPOCH) pairs dropped")
        print(f"[load]   {len(df)} unique satellites retained")
    return df


# ===========================================================================
# 2. PROPAGATION
# ===========================================================================

def _rv_to_elements(r: np.ndarray, v: np.ndarray) -> dict | None:
    """
    Classical (osculating) orbital elements from a TEME state vector.

    Standard rv->coe conversion. Returns degrees for angles and revs/day for
    mean motion, matching the GP/CSV convention.
    """
    rn = float(np.linalg.norm(r))
    vn = float(np.linalg.norm(v))
    if rn <= 0:
        return None

    h_vec = np.cross(r, v)
    h = float(np.linalg.norm(h_vec))
    if h <= 0:
        return None

    n_vec = np.cross([0.0, 0.0, 1.0], h_vec)
    n = float(np.linalg.norm(n_vec))

    e_vec = ((vn ** 2 - MU_EARTH / rn) * r - float(np.dot(r, v)) * v) / MU_EARTH
    ecc = float(np.linalg.norm(e_vec))

    energy = vn ** 2 / 2.0 - MU_EARTH / rn
    if abs(energy) < 1e-12:
        return None
    a = -MU_EARTH / (2.0 * energy)
    if a <= 0:
        return None

    inc = math.degrees(math.acos(max(-1.0, min(1.0, h_vec[2] / h))))

    if n > 1e-10:
        raan = math.degrees(math.acos(max(-1.0, min(1.0, n_vec[0] / n))))
        if n_vec[1] < 0:
            raan = 360.0 - raan
    else:
        raan = 0.0

    if n > 1e-10 and ecc > 1e-10:
        argp = math.degrees(math.acos(
            max(-1.0, min(1.0, float(np.dot(n_vec, e_vec)) / (n * ecc)))))
        if e_vec[2] < 0:
            argp = 360.0 - argp
    else:
        argp = 0.0

    if ecc > 1e-10:
        nu = math.acos(max(-1.0, min(1.0,
                       float(np.dot(e_vec, r)) / (ecc * rn))))
        if float(np.dot(r, v)) < 0:
            nu = 2 * math.pi - nu
        # true -> eccentric -> mean anomaly
        E = 2.0 * math.atan2(math.sqrt(1 - ecc) * math.sin(nu / 2),
                             math.sqrt(1 + ecc) * math.cos(nu / 2))
        M = E - ecc * math.sin(E)
    else:
        M = math.atan2(r[1], r[0]) - math.radians(raan)

    mean_motion = math.sqrt(MU_EARTH / a ** 3) * 86400.0 / (2 * math.pi)

    return {
        "MEAN_MOTION": mean_motion,
        "ECCENTRICITY": ecc,
        "INCLINATION": inc,
        "RA_OF_ASC_NODE": raan % 360.0,
        "ARG_OF_PERICENTER": argp % 360.0,
        "MEAN_ANOMALY": math.degrees(M) % 360.0,
    }


def _propagate_sgp4(base: pd.Series, offsets_min: np.ndarray) -> list[dict] | None:
    """
    Propagate with real SGP4 via a TLE built by satellite_catalog.build_tle_from_gp.
    Returns None if the TLE cannot be built or SGP4 rejects it.
    """
    from sgp4.api import Satrec, jday  # local import: optional dependency

    from satellite_catalog import build_tle_from_gp

    row_str = {c: str(base[c]) for c in INPUT_COLUMNS if c in base.index}
    tle = build_tle_from_gp(row_str)
    if not tle:
        return None
    try:
        sat = Satrec.twoline2rv(tle["line1"], tle["line2"])
    except Exception:
        return None

    epoch0 = base["EPOCH"].to_pydatetime()
    out = []
    for off in offsets_min:
        t = epoch0 + timedelta(minutes=float(off))
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                      t.second + t.microsecond / 1e6)
        err, r, v = sat.sgp4(jd, fr)
        if err != 0:
            return None
        el = _rv_to_elements(np.asarray(r, dtype=float),
                             np.asarray(v, dtype=float))
        if el is None:
            return None
        out.append(el)
    return out


def _propagate_j2(base: pd.Series, offsets_min: np.ndarray) -> list[dict]:
    """
    Secular J2 propagation — the dominant real perturbation on these elements.

    Fallback for when the `sgp4` package is not installed. RAAN, argument of
    perigee and mean anomaly advance at their secular J2 rates; eccentricity
    and inclination are held (their secular J2 rates are zero). This is a
    genuine orbital model, not noise, but it is an approximation: prefer SGP4.
    """
    mm0 = float(base["MEAN_MOTION"])
    ecc = float(base["ECCENTRICITY"])
    inc = math.radians(float(base["INCLINATION"]))

    n_rad = mm0 * 2 * math.pi / 86400.0                     # rad/s
    a = (MU_EARTH / n_rad ** 2) ** (1.0 / 3.0)              # km
    p = max(a * (1 - ecc ** 2), 1e-6)
    k = 1.5 * J2 * (R_EARTH / p) ** 2 * n_rad

    raan_dot = -k * math.cos(inc)                                        # rad/s
    argp_dot = 0.5 * k * (5.0 * math.cos(inc) ** 2 - 1.0)                # rad/s
    m_dot    = n_rad * (1.0 + J2 * (R_EARTH / p) ** 2
                        * math.sqrt(1 - ecc ** 2)
                        * (3.0 * math.cos(inc) ** 2 - 1.0) * 0.75)

    raan0 = math.radians(float(base["RA_OF_ASC_NODE"]))
    argp0 = math.radians(float(base["ARG_OF_PERICENTER"]))
    m0    = math.radians(float(base["MEAN_ANOMALY"]))

    # First-order J2 SHORT-PERIOD terms. Secular J2 leaves eccentricity and
    # inclination exactly constant, but the elements in a GP/TLE set are
    # osculating — they oscillate at twice the argument of latitude. Without
    # this, ecc_delta is identically zero for every row and the dataset
    # reproduces the very defect it exists to fix. Amplitudes are the standard
    # J2 (Re/p)^2 scale, which puts them around 1e-4 — real, and three orders
    # of magnitude below the 0.01 SEU threshold, so they cannot manufacture a
    # false SEU.
    amp = 0.5 * J2 * (R_EARTH / p) ** 2
    ecc_amp = amp * (1.0 - ecc ** 2) * math.sin(inc) ** 2
    inc_amp = amp * math.sin(inc) * math.cos(inc)

    out = []
    for off in offsets_min:
        dt = float(off) * 60.0
        argp_t = argp0 + argp_dot * dt
        m_t    = m0 + m_dot * dt
        u = argp_t + m_t                       # argument of latitude (M ~ nu)
        out.append({
            "MEAN_MOTION": mm0 * (1.0 - 1.5 * amp * math.cos(2.0 * u)
                                  * math.sin(inc) ** 2),
            "ECCENTRICITY": max(0.0, ecc + ecc_amp * math.cos(2.0 * u)),
            "INCLINATION": math.degrees(inc + inc_amp * math.cos(2.0 * u)),
            "RA_OF_ASC_NODE": math.degrees(raan0 + raan_dot * dt) % 360.0,
            "ARG_OF_PERICENTER": math.degrees(argp_t) % 360.0,
            "MEAN_ANOMALY": math.degrees(m_t) % 360.0,
        })
    return out


def resolve_propagator(requested: str) -> str:
    """Decide between 'sgp4' and 'j2', honouring an explicit request."""
    try:
        import sgp4.api  # noqa: F401
        have = True
    except ImportError:
        have = False

    if requested == "sgp4":
        if not have:
            raise SystemExit(
                "ERROR: --propagator sgp4 requested but the sgp4 package is not "
                "installed.\n       pip install sgp4>=2.23   (it is already in "
                "requirements.txt)")
        return "sgp4"
    if requested == "j2":
        return "j2"
    if have:
        return "sgp4"
    print("!" * 74)
    print("WARNING: the `sgp4` package is not installed — falling back to secular")
    print("         J2 propagation. The series will be dynamically plausible but")
    print("         NOT SGP4-accurate. Install sgp4 and re-run before training a")
    print("         model you intend to quote numbers from:")
    print("             pip install sgp4>=2.23")
    print("         Force the check with: --propagator sgp4")
    print("!" * 74)
    return "j2"


# ===========================================================================
# 3. FAULT INJECTION
# ===========================================================================
#
# Precedence in assign_fault_labels().label_row(), highest first:
#   1. TLE_AGE_HOURS > 72                      -> COMMAND_INJECTION
#   2. |BSTAR| > 0.005      or |bstar_z| > 3   -> FIRMWARE_CORRUPTION
#   3. |MMDOT| > 0.001      or |mmdot_z| > 3   -> FIRMWARE_CORRUPTION
#   4. ecc_delta > 0.01                        -> SEU
#   5. REV_DELTA <= 0                          -> SOFTWARE_BUG
#   6. otherwise                               -> NORMAL
#
# Every injection below must stay clear of the rules ABOVE it, or it is
# shadowed and the ground truth will not match. Two consequences drive the
# design:
#
#   * BSTAR and MEAN_MOTION_DOT are held EXACTLY constant for every satellite
#     that is not FIRMWARE_CORRUPTION. The z-score is (s - mean) / (std + 1e-9);
#     a constant series gives std = 0 and therefore z = 0, which makes a
#     z-score false positive impossible. Adding noise here would risk a random
#     outlier crossing |z| > 3 and silently stealing the row.
#   * FIRMWARE ramps start ABOVE the absolute threshold rather than crossing
#     it gradually, so every ramp row is caught by the absolute rule and the
#     ground truth is exact rather than dependent on the z-score.


def _inject_seu(rows: list[dict], rng: np.random.Generator, labels: list[str],
                n_events: int = 2) -> None:
    """
    One-epoch eccentricity step that REVERTS the next epoch.

    ecc_delta is |diff|, so a jump at epoch k and its reversal at k+1 both
    exceed the threshold — each event yields two SEU rows, which is exactly
    the transient bit-flip signature the docstring describes.

    Note: assign_fault_labels() computes `anomaly_delta` but label_row() never
    reads it, so the MEAN_ANOMALY perturbation below is physically consistent
    but contributes nothing to the label. Kept because the fault description
    calls for it. Flagged for Phase 2.
    """
    n = len(rows)
    lo, hi = 2, n - 3
    if hi <= lo:
        return
    picks = sorted(rng.choice(range(lo, hi), size=min(n_events, hi - lo),
                              replace=False))
    used: set[int] = set()
    for k in picks:
        if k in used or (k + 1) in used:
            continue
        step = float(rng.uniform(3.0, 6.0)) * ECC_JUMP_THRESH   # 0.03 - 0.06
        rows[k]["ECCENTRICITY"] += step
        rows[k]["MEAN_ANOMALY"] = (rows[k]["MEAN_ANOMALY"]
                                   + float(rng.uniform(20.0, 40.0))) % 360.0
        labels[k] = "SEU"
        labels[k + 1] = "SEU"      # the reversion is equally anomalous
        used.update({k, k + 1})


def _inject_software_bug(rows: list[dict], rng: np.random.Generator,
                         labels: list[str]) -> None:
    """
    Freeze the revolution counter for a run of epochs -> REV_DELTA == 0.

    The run scales with series length. A fixed 4-6 epoch freeze is a quarter
    of a 20-epoch series but three quarters of an 8-epoch one, which pushes
    the zero-REV_DELTA fraction past the acceptance bar on short runs.
    """
    n = len(rows)
    run = max(2, min(n // 5, 6))
    start = int(rng.integers(2, max(3, n - run - 1)))
    frozen = rows[start]["REV_AT_EPOCH"]
    for i in range(start + 1, min(start + 1 + run, n)):
        rows[i]["REV_AT_EPOCH"] = frozen
        labels[i] = "SOFTWARE_BUG"


def _inject_firmware(rows: list[dict], rng: np.random.Generator,
                     labels: list[str]) -> None:
    """
    Ramp BSTAR (and sometimes MEAN_MOTION_DOT) from just above the threshold
    to well above it — a corrupted drag coefficient written by a bad flash.
    """
    n = len(rows)
    run = int(rng.integers(5, 8))
    start = max(1, n - run)
    use_mmdot = bool(rng.random() < 0.4)

    for j, i in enumerate(range(start, n)):
        frac = (j + 1) / run
        sign = 1.0 if rows[i]["BSTAR"] >= 0 else -1.0
        rows[i]["BSTAR"] = sign * (BSTAR_THRESH * 1.2 + frac * 0.015)
        if use_mmdot:
            # clipped to (-0.01, 0.01) by prepare_dataframe -> stay inside
            rows[i]["MEAN_MOTION_DOT"] = sign * (MMDOT_THRESH * 1.5 + frac * 0.005)
        labels[i] = "FIRMWARE_CORRUPTION"


# ===========================================================================
# 4. SERIES BUILDER
# ===========================================================================

def build_series(df: pd.DataFrame, n_epochs: int, seed: int, propagator: str,
                 verbose: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    # All series are anchored so their LAST epoch sits at t_end. TLE_AGE_HOURS
    # is measured from the global newest epoch, so this keeps every non-stale
    # satellite inside the 72 h window and makes staleness an explicit choice
    # rather than an accident of ordering.
    t_end = df["EPOCH"].max()

    # A satellite is only usable if its whole series fits inside the 72 h
    # window, otherwise its early epochs are forced to COMMAND_INJECTION by
    # rule 1 no matter what we inject. One epoch per revolution keeps
    # REV_DELTA >= 1 (an integer counter cannot advance on a sub-orbit step,
    # which is what made REV_DELTA == 0 unavoidable in the source data).
    period_min = 1440.0 / df["MEAN_MOTION"]
    span_h = (n_epochs - 1) * period_min / 60.0
    usable = span_h <= (TLE_AGE_STALE_H - 12.0)
    dropped = int((~usable).sum())
    df = df[usable].reset_index(drop=True)

    if verbose:
        print(f"[build] propagator: {propagator.upper()}")
        print(f"[build] {dropped} satellites excluded: {n_epochs} epochs at one "
              f"revolution each would span > {TLE_AGE_STALE_H - 12:.0f} h,")
        print(f"[build]   which rule 1 would label COMMAND_INJECTION regardless "
              f"of what is injected")
        print(f"[build] {len(df)} satellites -> {n_epochs} epochs each")

    # Balanced class assignment. Satellites whose REAL drag terms already
    # exceed the thresholds are forced to FIRMWARE_CORRUPTION: they genuinely
    # exhibit that signature and pretending otherwise would inject a
    # contradiction between ground truth and the labeller.
    base_bad = ((df["BSTAR"].abs() > BSTAR_THRESH) |
                (df["MEAN_MOTION_DOT"].abs() > MMDOT_THRESH)).to_numpy()
    assigned = np.array(
        [CLASSES[i % len(CLASSES)] for i in range(len(df))], dtype=object)
    rng.shuffle(assigned)
    assigned[base_bad] = "FIRMWARE_CORRUPTION"
    if verbose and base_bad.sum():
        print(f"[build] {int(base_bad.sum())} satellites forced to "
              f"FIRMWARE_CORRUPTION (real BSTAR/MMDOT already over threshold)")

    records: list[dict] = []
    sgp4_failures = 0

    for idx, base in df.iterrows():
        cls = assigned[idx]
        step = 1440.0 / float(base["MEAN_MOTION"])          # one revolution
        offsets = np.arange(n_epochs, dtype=float) * step

        els = None
        if propagator == "sgp4":
            els = _propagate_sgp4(base, offsets)
            if els is None:
                sgp4_failures += 1
        if els is None:
            els = _propagate_j2(base, offsets)

        # COMMAND_INJECTION: withhold recent epochs so the whole series sits
        # further back than the staleness threshold.
        if cls == "COMMAND_INJECTION":
            stale_h = TLE_AGE_STALE_H + 24.0 + float(rng.uniform(0, 240))
            last_epoch = t_end - timedelta(hours=stale_h)
        else:
            last_epoch = t_end - timedelta(hours=float(rng.uniform(0, 6)))
        first_epoch = last_epoch - timedelta(minutes=float(offsets[-1]))

        rev0 = float(base["REV_AT_EPOCH"])
        bstar0 = float(base["BSTAR"])
        mmdot0 = float(base["MEAN_MOTION_DOT"])

        rows: list[dict] = []
        for k in range(n_epochs):
            el = els[k]
            rows.append({
                "OBJECT_NAME": base["OBJECT_NAME"],
                "OBJECT_ID": base["OBJECT_ID"],
                "EPOCH": first_epoch + timedelta(minutes=float(offsets[k])),
                "MEAN_MOTION": el["MEAN_MOTION"],
                "ECCENTRICITY": el["ECCENTRICITY"],
                "INCLINATION": el["INCLINATION"],
                "RA_OF_ASC_NODE": el["RA_OF_ASC_NODE"],
                "ARG_OF_PERICENTER": el["ARG_OF_PERICENTER"],
                "MEAN_ANOMALY": el["MEAN_ANOMALY"],
                "EPHEMERIS_TYPE": base.get("EPHEMERIS_TYPE", 0),
                "CLASSIFICATION_TYPE": base.get("CLASSIFICATION_TYPE", "U"),
                "NORAD_CAT_ID": int(base["NORAD_CAT_ID"]),
                "ELEMENT_SET_NO": base.get("ELEMENT_SET_NO", 999),
                # exactly one revolution per epoch -> REV_DELTA == 1
                "REV_AT_EPOCH": rev0 + k,
                "BSTAR": bstar0,          # constant unless FIRMWARE (see notes)
                "MEAN_MOTION_DOT": mmdot0,
                "MEAN_MOTION_DDOT": base.get("MEAN_MOTION_DDOT", 0.0),
            })

        # Ground truth starts as NORMAL; injections overwrite what they touch.
        labels = ["NORMAL"] * n_epochs

        if cls == "SEU":
            _inject_seu(rows, rng, labels)
        elif cls == "SOFTWARE_BUG":
            _inject_software_bug(rows, rng, labels)
        elif cls == "FIRMWARE_CORRUPTION":
            if base_bad[idx]:
                # This satellite's REAL drag terms are already over threshold,
                # so rule 2/3 fires on EVERY row, not just the injected ramp.
                # Labelling only the ramp would put ~20 rows per satellite into
                # deliberate disagreement with the labeller.
                labels = ["FIRMWARE_CORRUPTION"] * n_epochs
            else:
                _inject_firmware(rows, rng, labels)
        elif cls == "COMMAND_INJECTION":
            labels = ["COMMAND_INJECTION"] * n_epochs

        # The REV_DELTA warm-up artefact: .diff().fillna(0) makes the first row
        # of every satellite look like a stuck counter. It is indistinguishable
        # from a real SOFTWARE_BUG by rule 5, so the ground truth records what
        # the labeller will unavoidably say — unless a higher-precedence rule
        # already claims the row. See NOTES.
        if labels[0] == "NORMAL":
            labels[0] = "SOFTWARE_BUG"

        for row, lab in zip(rows, labels):
            row["ground_truth_fault"] = lab
        records.extend(rows)

    if sgp4_failures and verbose:
        print(f"[build] {sgp4_failures} satellites fell back to J2 "
              f"(TLE build or SGP4 propagation failed)")

    out = pd.DataFrame.from_records(records)
    out = out.sort_values(["NORAD_CAT_ID", "EPOCH"]).reset_index(drop=True)
    return out[INPUT_COLUMNS + ["ground_truth_fault"]]


# ===========================================================================
# 5. VERIFY
# ===========================================================================

def _assign_labels_reference(df: pd.DataFrame) -> pd.Series:
    """
    Faithful re-implementation of
    models/satellite_fault_classifier_V2.py:assign_fault_labels().

    Used only when the real module cannot be imported (it imports torch at
    module level). `--verify` always reports which of the two ran.
    """
    d = df.copy()
    d["ecc_delta"] = d.groupby("NORAD_CAT_ID")["ECCENTRICITY"].diff().abs().fillna(0)
    d["bstar_zscore"] = (
        d.groupby("NORAD_CAT_ID")["BSTAR"]
         .transform(lambda s: (s - s.mean()) / (s.std() + 1e-9)).fillna(0))
    d["mmdot_zscore"] = (
        d.groupby("NORAD_CAT_ID")["MEAN_MOTION_DOT"]
         .transform(lambda s: (s - s.mean()) / (s.std() + 1e-9)).fillna(0))

    def label_row(row):
        if row["TLE_AGE_HOURS"] > TLE_AGE_STALE_H:
            return "COMMAND_INJECTION"
        if abs(row["BSTAR"]) > BSTAR_THRESH or abs(row["bstar_zscore"]) > 3:
            return "FIRMWARE_CORRUPTION"
        if abs(row["MEAN_MOTION_DOT"]) > MMDOT_THRESH or abs(row["mmdot_zscore"]) > 3:
            return "FIRMWARE_CORRUPTION"
        if row["ecc_delta"] > ECC_JUMP_THRESH:
            return "SEU"
        if row["REV_DELTA"] <= 0:
            return "SOFTWARE_BUG"
        return "NORMAL"

    return d.apply(label_row, axis=1)


def verify(path: Path) -> int:
    """Re-derive features exactly as prepare_dataframe() does, then label."""
    df = pd.read_csv(path)
    df["EPOCH"] = pd.to_datetime(df["EPOCH"], errors="coerce", format="mixed")
    df = df.sort_values(["NORAD_CAT_ID", "EPOCH"]).reset_index(drop=True)

    # Identical to prepare_dataframe(): clipping, then the two derived columns.
    for col, (lo, hi) in {
        "BSTAR": (-0.1, 0.1), "MEAN_MOTION_DOT": (-0.01, 0.01),
        "ECCENTRICITY": (0.0, 1.0), "REV_AT_EPOCH": (0.0, 1e6),
    }.items():
        df[col] = df[col].clip(lo, hi)

    most_recent = df["EPOCH"].max()
    df["TLE_AGE_HOURS"] = (most_recent - df["EPOCH"]).dt.total_seconds() / 3600.0
    df["REV_DELTA"] = df.groupby("NORAD_CAT_ID")["REV_AT_EPOCH"].diff().fillna(0)

    try:
        sys.path.insert(0, str(ROOT / "models"))
        from satellite_fault_classifier_V2 import assign_fault_labels
        labelled = assign_fault_labels(df)
        predicted = labelled["fault_label"]
        source = "models/satellite_fault_classifier_V2.py (the real labeller)"
    except Exception as exc:
        predicted = _assign_labels_reference(df)
        source = (f"embedded reference copy — the real module could not be "
                  f"imported ({type(exc).__name__}: {exc})")

    print("\n" + "=" * 74)
    print("VERIFY")
    print("=" * 74)
    print(f"labeller : {source}")
    print(f"rows     : {len(df)}   satellites: {df['NORAD_CAT_ID'].nunique()}")

    print("\n-- class distribution (labeller output) " + "-" * 34)
    counts = predicted.value_counts()
    for cls in CLASSES:
        print(f"   {cls:<22} {int(counts.get(cls, 0)):>7}")

    epochs_per = df.groupby("NORAD_CAT_ID")["EPOCH"].nunique()
    min_epochs = int(epochs_per.min())
    ecc_delta = df.groupby("NORAD_CAT_ID")["ECCENTRICITY"].diff().abs().fillna(0)
    pct_ecc = 100.0 * float((ecc_delta > 0).mean())
    pct_rev = 100.0 * float((df["REV_DELTA"] != 0).mean())
    agree = 100.0 * float((predicted.values == df["ground_truth_fault"].values).mean())

    print("\n-- acceptance " + "-" * 60)
    checks = [
        (f"every satellite has >= {SEQ_LEN} distinct epochs",
         min_epochs >= SEQ_LEN, f"min = {min_epochs}"),
        ("all four fault classes >= 300 rows",
         all(int(counts.get(c, 0)) >= 300 for c in CLASSES[1:]),
         ", ".join(f"{c.split('_')[0]}={int(counts.get(c,0))}" for c in CLASSES[1:])),
        ("ecc_delta non-zero for > 90% of rows",
         pct_ecc > 90.0, f"{pct_ecc:.1f}%"),
        ("REV_DELTA non-zero for > 90% of rows",
         pct_rev > 90.0, f"{pct_rev:.1f}%"),
        ("labeller agrees with ground_truth_fault > 95%",
         agree > 95.0, f"{agree:.2f}%"),
    ]
    ok = True
    for name, passed, detail in checks:
        print(f"   [{'PASS' if passed else 'FAIL'}] {name:<48} {detail}")
        ok &= passed

    if not ok:
        print("\n-- disagreements by (ground truth -> predicted) " + "-" * 27)
        mism = df.loc[predicted.values != df["ground_truth_fault"].values]
        pred_m = predicted[predicted.values != df["ground_truth_fault"].values]
        for (gt, pr), n in (pd.crosstab(mism["ground_truth_fault"], pred_m)
                            .stack().sort_values(ascending=False).items()):
            if n:
                print(f"   {gt:<22} -> {pr:<22} {n}")

    print("=" * 74)
    print("RESULT:", "ALL CHECKS PASS" if ok else "FAILED")
    print("=" * 74)
    return 0 if ok else 1


# ===========================================================================
# 6. CLI
# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate a propagation-based orbital series with injected "
                    "faults. Runs entirely offline.")
    ap.add_argument("--epochs", type=int, default=20,
                    help="epochs per satellite (default 20, minimum %d)" % SEQ_LEN)
    ap.add_argument("--out", default=OUTPUT_CSV, help=f"output CSV (default {OUTPUT_CSV})")
    ap.add_argument("--seed", type=int, default=CONFIG["random_seed"],
                    help="RNG seed (default %d, from CONFIG)" % CONFIG["random_seed"])
    ap.add_argument("--propagator", choices=["auto", "sgp4", "j2"], default="auto",
                    help="auto = SGP4 if installed, else J2 with a warning")
    ap.add_argument("--verify", action="store_true",
                    help="after writing, re-label with assign_fault_labels() "
                         "and check the acceptance criteria")
    ap.add_argument("--verify-only", action="store_true",
                    help="verify an existing --out file without regenerating")
    args = ap.parse_args()

    out_path = ROOT / args.out

    if args.verify_only:
        if not out_path.exists():
            raise SystemExit(f"ERROR: {out_path} does not exist")
        return verify(out_path)

    if args.epochs < SEQ_LEN:
        raise SystemExit(f"ERROR: --epochs must be >= seq_len ({SEQ_LEN})")

    # prepare_dataframe() computes REV_DELTA with .diff().fillna(0), so the
    # first row of EVERY satellite is forced to zero. That is exactly
    # 1/--epochs of all rows, before any SOFTWARE_BUG injection. Below ~16
    # epochs the artefact alone breaks the "REV_DELTA non-zero for > 90% of
    # rows" criterion and no amount of tuning recovers it.
    if args.epochs < 16:
        floor_pct = 100.0 / args.epochs
        print(f"WARNING: --epochs {args.epochs} forces at least "
              f"{floor_pct:.1f}% of rows to REV_DELTA == 0 (one warm-up row per")
        print(f"         satellite, from .diff().fillna(0) in prepare_dataframe).")
        print(f"         The > 90% acceptance criterion needs --epochs >= 16; "
              f"20 is the default.")

    propagator = resolve_propagator(args.propagator)
    src = load_inputs()
    out = build_series(src, args.epochs, args.seed, propagator)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f"\n[write] {len(out)} rows -> {out_path}")
    print(f"[write] ground truth: "
          + " ".join(f"{k}={v}" for k, v in
                     out['ground_truth_fault'].value_counts().items()))

    return verify(out_path) if args.verify else 0


if __name__ == "__main__":
    raise SystemExit(main())


# ===========================================================================
# NOTES — findings that belong to Phase 2, recorded here so they are not lost
# ===========================================================================
#
# 1. REV_DELTA warm-up artefact.
#    prepare_dataframe() does .groupby(...).diff().fillna(0), so the first row
#    of every satellite has REV_DELTA == 0 and rule 5 labels it SOFTWARE_BUG.
#    In the original one-row-per-satellite data EVERY row was a first row —
#    that, not any real signal, is where the 807 SOFTWARE_BUG labels came from.
#    Here it costs one row per satellite. The fix belongs in the labeller
#    (drop or mask the first row per group), not here.
#
# 2. anomaly_delta is dead.
#    assign_fault_labels() computes df["anomaly_delta"] and label_row() never
#    reads it. The SEU rule is eccentricity-only despite the docstring saying
#    "ECCENTRICITY or MEAN_ANOMALY".
#
# 3. The z-score branches are near-unreachable on a well-formed series.
#    (s - mean) / (std + 1e-9) on a constant series gives 0, and on a ramped
#    series the absolute threshold fires first. They mainly serve to catch
#    satellites whose real drag terms are outliers within their own history.
#
# 4. Excluded satellites.
#    Anything whose orbit is slow enough that N revolutions span more than
#    ~60 h cannot produce a non-COMMAND_INJECTION label, because TLE_AGE_HOURS
#    is measured from the global newest epoch. ~3% of the catalogue at the
#    default settings. Lowering --epochs brings some of them back.
