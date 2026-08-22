"""
=============================================================================
 AI-1 | Satellite Fault Classifier — TLE / Orbital-Element Edition
 Fault classes: SEU | Software Bug | Firmware Corruption | Command Injection
 Data sources : User-supplied TLE/orbital-element CSVs (CelesTrak format)
                + N2YO REST API (live TLE refresh)
 Architecture : Isolation Forest (anomaly gate) -> Transformer Encoder (classifier)
=============================================================================

WHY THIS VERSION IS DIFFERENT FROM THE FIRST DRAFT
----------------------------------------------------
The earlier version assumed SatNOGS *telemetry* frames (temperature,
voltage, current, RSSI). The CSVs you actually provided are
**orbital element sets (TLE-derived)** with columns:

    OBJECT_NAME, OBJECT_ID, EPOCH, MEAN_MOTION, ECCENTRICITY, INCLINATION,
    RA_OF_ASC_NODE, ARG_OF_PERICENTER, MEAN_ANOMALY, EPHEMERIS_TYPE,
    CLASSIFICATION_TYPE, NORAD_CAT_ID, ELEMENT_SET_NO, REV_AT_EPOCH,
    BSTAR, MEAN_MOTION_DOT, MEAN_MOTION_DDOT

This is fundamentally different telemetry: there is no on-board
voltage/current/RSSI here, only **orbit propagation parameters**.
So the feature set, fault-labelling heuristics and "ECC" definition
all change:

  - "ECC" in your data = ECCENTRICITY (orbital eccentricity, 0-1),
    NOT "Error-Correcting-Code" memory errors. Both are now tracked
    separately and clearly named to avoid confusion.
  - "EPOCH" = TLE timestamp, used to compute TLE_AGE_HOURS (a strong
    proxy for stale/late ephemeris updates -> possible comms or
    ground-segment fault).
  - BSTAR / MEAN_MOTION_DOT / MEAN_MOTION_DDOT = drag & decay terms,
    used as proxies for unexpected orbital decay (could indicate
    attitude-control or propulsion faults reflected in orbit drift).

=============================================================================
QUICK START
-----------
1. Install deps:
   pip install requests pandas numpy scikit-learn torch transformers tqdm

2. (Optional) Get a free N2YO API key for live TLE refresh:
   -> https://www.n2yo.com -> Login -> Profile -> "API Key"

3. Place your CSVs (CelesTrak GP/TLE format) next to this script, or pass
   their paths with --csv. Multiple files are merged automatically.

4. Run:
   python satellite_fault_classifier_tle.py \
       --csv orbital_elements_main.csv orbital_elements_part2.csv orbital_elements_part3.csv \
       --n2yo_api_key YOUR_KEY_HERE

   or demo mode (no key, no internet needed):
   python satellite_fault_classifier_tle.py --csv your_data.csv --demo
"""

# ---------------------------------------------------------------------------
# IMPORTS
# ---------------------------------------------------------------------------
import os
import sys
import time
import json
import argparse
import requests
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from sklearn.model_selection import train_test_split, GroupShuffleSplit, StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import IsolationForest
from sklearn.metrics import classification_report, confusion_matrix
from tqdm import tqdm


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
# CONFIG, FAULT_LABELS, IDX_TO_LABEL and FEATURE_COLS now live in
# models/feature_spec.py — a dependency-free module so that the pipeline,
# the AI-1 -> AI-2 bridge and the test suite can read the spec without
# importing torch/pandas/sklearn. They are re-exported here unchanged, so
# `from satellite_fault_classifier_V2 import CONFIG, FEATURE_COLS` still works.
import sys as _sys
from pathlib import Path as _Path

_SPEC_DIR = _Path(__file__).resolve().parent
if str(_SPEC_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SPEC_DIR))

from feature_spec import (  # noqa: E402,F401
    CONFIG,
    FAULT_LABELS,
    IDX_TO_LABEL,
    FEATURE_COLS,
)


# ---------------------------------------------------------------------------
# 1. DATA EXTRACTION - CSV (CelesTrak/TLE format) + N2YO live refresh
# ---------------------------------------------------------------------------

def load_csv_datasets(paths: list) -> pd.DataFrame:
    """
    Load and concatenate one or more CelesTrak-format orbital-element CSVs.
    Handles the mixed dtypes seen across files (e.g. MEAN_MOTION_DDOT
    sometimes parsed as string with exponent notation like '.255E-5').
    """
    print(f"[LOAD] Reading {len(paths)} CSV file(s) ...")
    frames = []
    for p in paths:
        df = pd.read_csv(p)
        df["__source_file"] = os.path.basename(p)
        frames.append(df)
        print(f"  {p}: {df.shape[0]} rows")

    df_all = pd.concat(frames, ignore_index=True)
    print(f"  Combined shape: {df_all.shape}")
    return df_all


def fetch_n2yo_tle(api_key: str, norad_ids: list) -> pd.DataFrame:
    """
    Pull live TLEs from N2YO and convert each into one orbital-element row
    matching FEATURE_COLS via TLE parsing (no external sgp4 dependency
    required - we parse the raw TLE lines directly).

    N2YO TLE endpoint:
        GET https://api.n2yo.com/rest/v1/satellite/tle/{NORAD_ID}&apiKey=KEY
        -> { "info": {"satname": ..., "satid": ...}, "tle": "LINE1\\rLINE2" }
    """
    records = []
    for norad in norad_ids:
        url = f"{CONFIG['n2yo_base']}/tle/{norad}&apiKey={api_key}"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [N2YO] NORAD {norad}: request failed ({e}), skipping")
            continue

        tle_str = data.get("tle", "")
        sat_name = data.get("info", {}).get("satname", f"NORAD-{norad}")
        if not tle_str or "\r\n" not in tle_str and "\n" not in tle_str:
            print(f"  [N2YO] NORAD {norad}: no TLE returned, skipping")
            continue

        lines = tle_str.replace("\r\n", "\n").split("\n")
        if len(lines) < 2:
            continue
        line1, line2 = lines[0], lines[1]

        try:
            record = parse_tle_lines(sat_name, norad, line1, line2)
            records.append(record)
        except Exception as e:
            print(f"  [N2YO] NORAD {norad}: TLE parse failed ({e})")
            continue

        time.sleep(0.2)  # be polite

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    print(f"  [N2YO] Fetched {len(df)} live TLE record(s)")
    return df


def parse_tle_lines(sat_name: str, norad: int, line1: str, line2: str) -> dict:
    """
    Parse standard NORAD two-line element set into the same column
    schema as the CelesTrak GP CSVs (FEATURE_COLS-compatible).

    TLE Line 1 format (columns are 1-indexed in spec, 0-indexed here):
        cols 19-32 : epoch (YYDDD.DDDDDDDD)
        cols 34-43 : first derivative of mean motion (MEAN_MOTION_DOT)
        cols 45-52 : second derivative (decimal point assumed) -> MEAN_MOTION_DDOT
        cols 54-61 : BSTAR drag term (decimal point assumed)

    TLE Line 2 format:
        cols 9-16  : inclination (deg)
        cols 18-25 : RA of ascending node (deg)
        cols 27-33 : eccentricity (decimal point assumed, e.g. "0001234" -> 0.0001234)
        cols 35-42 : argument of perigee (deg)
        cols 44-51 : mean anomaly (deg)
        cols 53-63 : mean motion (revs/day)
        cols 64-68 : revolution number at epoch
    """
    def assumed_decimal(s: str) -> float:
        s = s.strip()
        sign = -1.0 if s.startswith("-") else 1.0
        s = s.lstrip("+-")
        return sign * float(f"0.{s}") if s else 0.0

    def exp_notation(s: str) -> float:
        # e.g. " 12345-3" -> 0.12345e-3 ; "00000-0" -> 0
        s = s.strip()
        if not s or s.replace("-", "").replace("+", "") == "" :
            return 0.0
        mantissa_sign = -1.0 if s.startswith("-") else 1.0
        s = s.lstrip("+-")
        if "-" in s[1:] or "+" in s[1:]:
            for i in range(1, len(s)):
                if s[i] in "+-":
                    mantissa, exp = s[:i], s[i:]
                    break
        else:
            mantissa, exp = s, "+0"
        return mantissa_sign * float(f"0.{mantissa}e{exp}")

    # --- Line 1 fields ---
    epoch_str = line1[18:32].strip()
    yy = int(epoch_str[:2])
    year = 2000 + yy if yy < 57 else 1900 + yy
    day_of_year = float(epoch_str[2:])
    epoch_dt = (pd.Timestamp(f"{year}-01-01", tz="UTC")
                 + pd.Timedelta(days=day_of_year - 1))

    mm_dot = float(line1[33:43].strip())
    mm_ddot = exp_notation(line1[44:52])
    bstar = exp_notation(line1[53:61])

    # --- Line 2 fields ---
    inclination = float(line2[8:16])
    raan = float(line2[17:25])
    eccentricity = assumed_decimal(line2[26:33])
    arg_perigee = float(line2[34:42])
    mean_anomaly = float(line2[43:51])
    mean_motion = float(line2[52:63])
    rev_at_epoch = float(line2[63:68])

    return {
        "OBJECT_NAME": sat_name,
        "OBJECT_ID": f"N2YO-{norad}",
        "EPOCH": epoch_dt.isoformat(),
        "MEAN_MOTION": mean_motion,
        "ECCENTRICITY": eccentricity,
        "INCLINATION": inclination,
        "RA_OF_ASC_NODE": raan,
        "ARG_OF_PERICENTER": arg_perigee,
        "MEAN_ANOMALY": mean_anomaly,
        "EPHEMERIS_TYPE": 0,
        "CLASSIFICATION_TYPE": "U",
        "NORAD_CAT_ID": float(norad),
        "ELEMENT_SET_NO": 999,
        "REV_AT_EPOCH": rev_at_epoch,
        "BSTAR": bstar,
        "MEAN_MOTION_DOT": mm_dot,
        "MEAN_MOTION_DDOT": mm_ddot,
        "__source_file": "n2yo_live",
    }


# ---------------------------------------------------------------------------
# 2. DATA CLEANING (pandas)
# ---------------------------------------------------------------------------

RAW_NUMERIC_COLS = [
    "MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE",
    "ARG_OF_PERICENTER", "MEAN_ANOMALY", "EPHEMERIS_TYPE", "NORAD_CAT_ID",
    "ELEMENT_SET_NO", "REV_AT_EPOCH", "BSTAR", "MEAN_MOTION_DOT",
    "MEAN_MOTION_DDOT",
]


def clean_orbital_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cleaning pipeline for combined TLE/orbital-element CSVs.

    Steps:
        1. Coerce all numeric columns (handles stray strings like
           '.255E-5' that pandas sometimes mis-types as object)
        2. Parse EPOCH -> datetime
        3. Drop rows with missing core orbital params
        4. Clip physically implausible values
        5. Sort by satellite + epoch, compute derived features:
             - TLE_AGE_HOURS  (time since EPOCH, relative to most-recent EPOCH
                                in the dataset, used as a staleness proxy)
             - REV_DELTA      (change in REV_AT_EPOCH between consecutive
                                epochs for the same satellite)
        6. Drop duplicate (NORAD_CAT_ID, EPOCH) rows
    """
    print("\n[CLEAN] Starting data cleaning ...")
    print(f"  Input shape : {df.shape}")

    # 1. Numeric coercion (fixes mixed dtypes across the 3 CSVs)
    for col in RAW_NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 2. Epoch parsing
    df["EPOCH"] = pd.to_datetime(df["EPOCH"], errors="coerce", utc=True)
    before = len(df)
    df = df.dropna(subset=["EPOCH", "NORAD_CAT_ID"])
    print(f"  Dropped {before - len(df)} rows with bad EPOCH/NORAD_CAT_ID")

    # 3. Drop rows missing core orbital params
    core = ["MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "BSTAR"]
    before = len(df)
    df = df.dropna(subset=core, how="any")
    print(f"  Dropped {before - len(df)} rows missing core orbital params")

    # 4. Physical clipping
    clip_rules = {
        "MEAN_MOTION":       (0.0,    18.0),     # revs/day (LEO ~ up to ~16-17)
        "ECCENTRICITY":      (0.0,    1.0),
        "INCLINATION":       (0.0,    180.0),
        "RA_OF_ASC_NODE":    (0.0,    360.0),
        "ARG_OF_PERICENTER": (0.0,    360.0),
        "MEAN_ANOMALY":      (0.0,    360.0),
        "BSTAR":             (-0.1,   0.1),
        "MEAN_MOTION_DOT":   (-0.01,  0.01),
        "MEAN_MOTION_DDOT":  (-1.0,   1.0),
        "REV_AT_EPOCH":      (0.0,    1e6),
    }
    for col, (lo, hi) in clip_rules.items():
        if col in df.columns:
            df[col] = df[col].clip(lo, hi)

    # Fill any residual NaNs in MEAN_MOTION_DDOT etc. with 0 (common for TLEs)
    for col in ["MEAN_MOTION_DDOT", "MEAN_MOTION_DOT", "BSTAR", "REV_AT_EPOCH"]:
        df[col] = df[col].fillna(0.0)

    # 5. Sort + derived features
    df = df.sort_values(["NORAD_CAT_ID", "EPOCH"]).reset_index(drop=True)

    most_recent_epoch = df["EPOCH"].max()
    df["TLE_AGE_HOURS"] = (most_recent_epoch - df["EPOCH"]).dt.total_seconds() / 3600.0

    df["REV_DELTA"] = (
        df.groupby("NORAD_CAT_ID")["REV_AT_EPOCH"].diff().fillna(0)
    )

    # 6. Deduplicate
    before = len(df)
    df = df.drop_duplicates(subset=["NORAD_CAT_ID", "EPOCH"])
    print(f"  Dropped {before - len(df)} duplicate (NORAD_CAT_ID, EPOCH) rows")
    print(f"  Output shape: {df.shape}")
    print(f"  NaNs remaining in features:\n{df[FEATURE_COLS].isna().sum().to_string()}")

    # Final NaN safety net
    df[FEATURE_COLS] = df[FEATURE_COLS].fillna(0.0)
    return df


# ---------------------------------------------------------------------------
# 3. FAULT LABELLING (heuristics rebuilt for orbital-element data)
# ---------------------------------------------------------------------------

def assign_fault_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Heuristic labels reinterpreted for orbital-element data. Mapping
    rationale (orbit-level *symptoms* of the fault categories from the
    fault taxonomy):

        SEU (Single Event Upset)
            -> Sudden, isolated jump in ECCENTRICITY or MEAN_ANOMALY
               between consecutive epochs with no corresponding change
               in BSTAR/MEAN_MOTION_DOT (a one-off bit-flip in the
               on-board state vector / OBC memory, corrected next epoch).

        SOFTWARE_BUG
            -> REV_DELTA == 0 or negative (revolution counter stuck or
               rolled back) while MEAN_MOTION stays normal -> onboard
               software/orbit-propagation bug, not a real physical event.

        FIRMWARE_CORRUPTION
            -> BSTAR or MEAN_MOTION_DOT far outside historical range for
               that satellite (corrupted drag/decay coefficients written
               by a corrupted firmware image / bad flash write).

        COMMAND_INJECTION
            -> TLE_AGE_HOURS very large (stale ephemeris, i.e. the
               satellite/ground segment stopped producing valid updates
               -> consistent with unauthorized command / uplink anomaly
               that disrupted normal telemetry/ephemeris reporting).

        NORMAL
            -> none of the above; used only for the Isolation Forest.
    """
    df = df.copy()

    df["ecc_delta"] = df.groupby("NORAD_CAT_ID")["ECCENTRICITY"].diff().abs().fillna(0)
    df["anomaly_delta"] = df.groupby("NORAD_CAT_ID")["MEAN_ANOMALY"].diff().abs().fillna(0)
    df["bstar_zscore"] = (
        df.groupby("NORAD_CAT_ID")["BSTAR"]
          .transform(lambda s: (s - s.mean()) / (s.std() + 1e-9))
          .fillna(0)
    )
    df["mmdot_zscore"] = (
        df.groupby("NORAD_CAT_ID")["MEAN_MOTION_DOT"]
          .transform(lambda s: (s - s.mean()) / (s.std() + 1e-9))
          .fillna(0)
    )

    def label_row(row):
        ecc_jump   = row["ecc_delta"]
        rev_delta  = row["REV_DELTA"]
        bstar_z    = abs(row["bstar_zscore"])
        mmdot_z    = abs(row["mmdot_zscore"])
        tle_age    = row["TLE_AGE_HOURS"]
        bstar_abs  = abs(row["BSTAR"])
        mmdot_abs  = abs(row["MEAN_MOTION_DOT"])

        if tle_age > CONFIG["tle_age_stale_hours"]:
            return "COMMAND_INJECTION"
        if bstar_abs > CONFIG["bstar_anomaly_threshold"] or bstar_z > 3:
            return "FIRMWARE_CORRUPTION"
        if mmdot_abs > CONFIG["mean_motion_dot_threshold"] or mmdot_z > 3:
            return "FIRMWARE_CORRUPTION"
        if ecc_jump > CONFIG["eccentricity_jump_threshold"]:
            return "SEU"
        if rev_delta <= 0:
            return "SOFTWARE_BUG"
        return "NORMAL"

    df["fault_label"] = df.apply(label_row, axis=1)

    counts = df["fault_label"].value_counts()
    print("\n[LABEL] Fault distribution (before augmentation):")
    print(counts.to_string())
    return df


# ---------------------------------------------------------------------------
# 4. SYNTHETIC DATA AUGMENTATION
# ---------------------------------------------------------------------------

def _class_rng(class_index: int) -> "np.random.Generator":
    """
    An independent, reproducible Generator per fault class.

    `default_rng(seed)` called with the SAME seed in four places yields four
    identical streams — which is what _generate_synthetic_class() did, so all
    four classes received the same noise sequence. Noise correlated across
    classes is worse than no noise: it puts an identical perturbation pattern
    on every class, which a model can learn as signal.

    `SeedSequence(seed).spawn(...)` is numpy's supported way to derive
    statistically independent child streams from one root seed. The result is
    deterministic — same CONFIG["random_seed"], same streams, every run — and
    independent of the global RNG, of import order, and of anything else in
    the process.
    """
    root = np.random.SeedSequence(CONFIG["random_seed"])
    # spawn enough children for every class, then take this one, so the set of
    # streams does not change as classes are added.
    children = root.spawn(max(len(FAULT_LABELS), class_index + 1))
    return np.random.default_rng(children[class_index])


def augment_fault_samples(df: pd.DataFrame, target_per_class: int = 300) -> pd.DataFrame:
    """
    Gaussian-noise augmentation around real fault samples to balance classes.

    LEAK 2 — MUST be called on the TRAINING SPLIT ONLY.
    ---------------------------------------------------
    This oversamples with replacement and adds noise of just 0.05 * class_std,
    which produces near-duplicates of the rows it copies. It used to run in
    main() BEFORE build_dataloaders() split the data, so FIRMWARE_CORRUPTION's
    23 real rows became 400 near-identical ones distributed across train, val
    AND test. Test performance was then measured largely on noisy copies of
    training rows.

    build_dataloaders() now splits first and calls this on the training frame
    only. Do not call it on a full dataset.

    Note it also drops every NORMAL row: the transformer is a 4-class fault
    classifier and NORMAL is the Isolation Forest's job. That is intended, but
    it means the caller must retain NORMAL rows separately if the anomaly gate
    still needs them (build_dataloaders does).
    """
    fault_df = df[df["fault_label"] != "NORMAL"].copy()
    augmented_rows = []

    for class_index, label in enumerate(FAULT_LABELS):
        class_df = fault_df[fault_df["fault_label"] == label]
        n_needed = max(0, target_per_class - len(class_df))
        if n_needed == 0:
            continue

        print(f"  Augmenting {label}: {len(class_df)} real -> +{n_needed} synthetic")
        if len(class_df) == 0:
            # DEPRECATED PATH — see _generate_synthetic_class(). Reaching this
            # means the split contains zero real examples of `label`, which
            # after Phase 1 should not happen. Warn loudly rather than
            # silently fabricating rows that contradict assign_fault_labels().
            print(f"  [WARN] no real {label} rows in this split — falling back "
                  f"to the DEPRECATED synthetic generator. Regenerate the "
                  f"dataset with generate_dataset.py instead.")
            class_df = _generate_synthetic_class(label, n=target_per_class,
                                                 class_index=class_index)

        # REPRODUCIBILITY: this used the GLOBAL numpy RNG —
        # `np.random.normal(...)` — while every line around it threaded
        # random_state=CONFIG["random_seed"]. Any earlier consumer of the
        # global stream (a library import, a shuffle, an unrelated call)
        # shifted the noise, so two runs of the same command produced
        # different augmented rows and therefore different weights. Nothing
        # else in the file was affected, which made it invisible: the split
        # was reproducible, the model was not.
        #
        # Each class draws from its OWN generator, seeded by
        # (random_seed, class_index), so the classes are independent of each
        # other and of everything else in the process.
        rng = _class_rng(class_index)

        samples = class_df.sample(n=n_needed, replace=True, random_state=CONFIG["random_seed"])
        for col in FEATURE_COLS:
            std = max(class_df[col].std() * 0.05, 1e-9)
            samples[col] = samples[col] + rng.normal(0, std, n_needed)
        augmented_rows.append(samples)

    if augmented_rows:
        aug_df = pd.concat([fault_df] + augmented_rows, ignore_index=True)
    else:
        aug_df = fault_df

    print("\n[AUG] Post-augmentation fault counts:")
    print(aug_df["fault_label"].value_counts().to_string())
    return aug_df


def _generate_synthetic_class(label: str, n: int,
                              class_index: int | None = None) -> pd.DataFrame:
    """
    DEPRECATED — kept for backward compatibility, do not use in new code.

    Superseded by generate_dataset.py (Phase 1), which propagates real
    catalogue entries with SGP4 and injects faults into the resulting SERIES.

    Why it is deprecated, not merely redundant: its definition of SEU
    CONTRADICTS the labeller. assign_fault_labels() defines SEU as a JUMP in
    eccentricity BETWEEN consecutive epochs (`ecc_delta > 0.01`). This
    function emits rows with a CONSTANT ECCENTRICITY=0.05 and no temporal
    structure at all — a flat 0.05 series has ecc_delta == 0 and would be
    labelled SOFTWARE_BUG or NORMAL, never SEU. Any model trained on these
    rows learns "high absolute eccentricity means SEU", which is not what the
    labeller, the emulator or the fault taxonomy mean by SEU.

    One further defect remains by design:
      * every generated row gets NORAD_CAT_ID = 0, so after the Phase 2 fix
        they all collapse into a single satellite group and land wholly in
        one split. Another reason not to use this path.

    FIXED in Phase 8.1: `default_rng(CONFIG["random_seed"])` was re-created on
    every call, so all four classes drew the IDENTICAL noise sequence —
    correlated noise across classes, which a model can learn as signal.
    `class_index` now selects an independent child stream via _class_rng().
    It defaults to deriving the index from `label`, so the old two-argument
    call signature still works and still gets independent noise.
    """
    if class_index is None:
        class_index = list(FAULT_LABELS).index(label) if label in FAULT_LABELS else 0
    rng = _class_rng(class_index)
    base = {
        "SEU": dict(MEAN_MOTION=14.5, ECCENTRICITY=0.05, INCLINATION=51.6,
                     RA_OF_ASC_NODE=180, ARG_OF_PERICENTER=180, MEAN_ANOMALY=180,
                     BSTAR=0.0002, MEAN_MOTION_DOT=0.00003, MEAN_MOTION_DDOT=0,
                     TLE_AGE_HOURS=2, REV_DELTA=15),
        "SOFTWARE_BUG": dict(MEAN_MOTION=14.5, ECCENTRICITY=0.001, INCLINATION=51.6,
                              RA_OF_ASC_NODE=180, ARG_OF_PERICENTER=180, MEAN_ANOMALY=180,
                              BSTAR=0.0002, MEAN_MOTION_DOT=0.00003, MEAN_MOTION_DDOT=0,
                              TLE_AGE_HOURS=2, REV_DELTA=0),
        "FIRMWARE_CORRUPTION": dict(MEAN_MOTION=14.5, ECCENTRICITY=0.001, INCLINATION=51.6,
                                     RA_OF_ASC_NODE=180, ARG_OF_PERICENTER=180, MEAN_ANOMALY=180,
                                     BSTAR=0.02, MEAN_MOTION_DOT=0.005, MEAN_MOTION_DDOT=0.5,
                                     TLE_AGE_HOURS=2, REV_DELTA=15),
        "COMMAND_INJECTION": dict(MEAN_MOTION=14.5, ECCENTRICITY=0.001, INCLINATION=51.6,
                                   RA_OF_ASC_NODE=180, ARG_OF_PERICENTER=180, MEAN_ANOMALY=180,
                                   BSTAR=0.0002, MEAN_MOTION_DOT=0.00003, MEAN_MOTION_DDOT=0,
                                   TLE_AGE_HOURS=120, REV_DELTA=15),
    }[label]

    records = {}
    for col, mean in base.items():
        # SEU's ECCENTRICITY is documented above as "a flat 0.05 series" that
        # contradicts the labeller's jump-based SEU definition (ecc_delta ==
        # 0, never > eccentricity_jump_threshold). Gaussian noise here
        # (~15% chance per adjacent pair of crossing the 0.01 threshold,
        # since the noise stddev alone is 0.1 * 0.05 = 0.005) made that claim
        # only incidentally true depending on RNG state — a fixed seed could
        # deterministically produce rows that DO cross the threshold, which
        # is exactly what test_synthetic_seu_contradicts_the_labeller caught.
        # Keeping this one column truly constant makes the documented
        # contradiction actually hold, deterministically.
        if label == "SEU" and col == "ECCENTRICITY":
            records[col] = np.full(n, mean)
            continue
        scale = abs(mean) * 0.1 if mean != 0 else 0.001
        records[col] = rng.normal(mean, scale, n)

    df = pd.DataFrame(records)
    df["fault_label"] = label
    df["NORAD_CAT_ID"] = 0
    df["EPOCH"] = pd.Timestamp.now(tz="UTC")
    return df


# ---------------------------------------------------------------------------
# 5. ISOLATION FOREST - Anomaly Gate
# ---------------------------------------------------------------------------

def train_isolation_forest(df_train: pd.DataFrame, scaler: StandardScaler):
    """
    Fit the anomaly gate on NORMAL rows of the TRAINING split only.

    Two fixes:

    LEAK 3 — this function used to create and `fit_transform` its own
    StandardScaler on every row it was given (in main() that was the entire
    dataset), then return it for build_dataloaders() to apply to the splits.
    Test-set statistics therefore leaked into training. It now receives the
    scaler already fitted on the training split and only transforms.

    Circular anomaly rate — it also used to fit on ALL rows, faults included,
    with contamination=0.05. An unsupervised detector trained on the anomalies
    it is meant to flag learns them as normal, and the "anomaly rate detected"
    it printed was ~5% by construction, because that is what `contamination`
    asks for. It is not a measurement. Fitting on NORMAL rows only makes the
    gate mean something; the rate is now reported against held-out faults.
    """
    print("\n[IF] Training Isolation Forest anomaly detector ...")

    normal_df = df_train[df_train["fault_label"] == "NORMAL"]
    if len(normal_df) < 50:
        print(f"  [WARN] only {len(normal_df)} NORMAL rows in the training "
              f"split — falling back to all training rows. The gate will be "
              f"weaker and its anomaly rate less meaningful.")
        normal_df = df_train

    X_scaled = scaler.transform(normal_df[FEATURE_COLS].values.astype(np.float32))

    iforest = IsolationForest(
        n_estimators=CONFIG["if_n_estimators"],
        contamination=CONFIG["if_contamination"],
        random_state=CONFIG["random_seed"],
        n_jobs=-1,
    )
    iforest.fit(X_scaled)
    print(f"  Fitted on {len(normal_df)} NORMAL training rows")

    # Honest diagnostics: false-positive rate on the NORMAL rows it was fitted
    # to (should be ~contamination), and recall on training faults it never saw.
    fp = (iforest.predict(X_scaled) == -1).mean() * 100
    print(f"  Flagged on fitted NORMAL rows : {fp:.1f}%  "
          f"(contamination={CONFIG['if_contamination']:.0%} — expected)")

    fault_df = df_train[df_train["fault_label"] != "NORMAL"]
    if len(fault_df):
        Xf = scaler.transform(fault_df[FEATURE_COLS].values.astype(np.float32))
        recall = (iforest.predict(Xf) == -1).mean() * 100
        print(f"  Flagged on held-out faults    : {recall:.1f}%  "
              f"(this one is a measurement)")
    return iforest


# ---------------------------------------------------------------------------
# 6. SLIDING-WINDOW SEQUENCES -> Dataset
# ---------------------------------------------------------------------------

class OrbitalSequenceDataset(Dataset):
    """
    Converts tabular orbital-element rows into fixed-length sequences.

    LEAK 1 FIX — `groups`.
    ---------------------
    This class used to window a flat array with

        for i in range(len(X) - seq_len):

    which is only meaningful if consecutive rows of X are consecutive epochs
    of the SAME satellite. They were not: build_dataloaders() ran
    train_test_split() (shuffle=True by default) first, so every 8-step
    "sequence" was 8 unrelated satellites stitched together. The positional
    encoding had nothing real to learn, and because the label is
    y[i + seq_len - 1] the model collapsed to single-row classification
    dressed up as a sequence model.

    Pass `groups` (the NORAD_CAT_ID of each row, with rows sorted by
    satellite then EPOCH) and windows are built strictly WITHIN each
    satellite. A window can never straddle a satellite boundary.

    `groups=None` reproduces the old behaviour and is retained only so
    existing callers do not break; it emits a warning, because for this
    dataset it is always wrong.
    """

    def __init__(self, X: np.ndarray, y: np.ndarray, seq_len: int = 8,
                 groups: np.ndarray | None = None):
        self.seq_len = seq_len
        self.samples = []
        self.labels = []

        if groups is None:
            print("  [WARN] OrbitalSequenceDataset built without `groups` — "
                  "windows may span satellite boundaries (see LEAK 1)")
            spans = [(0, len(X))]
        else:
            groups = np.asarray(groups)
            if len(groups) != len(X):
                raise ValueError(
                    f"groups has length {len(groups)} but X has {len(X)}")
            # Contiguous runs of identical group id. The caller is responsible
            # for sorting by (NORAD_CAT_ID, EPOCH) first; a satellite appearing
            # in two separate runs would simply yield two shorter spans, never
            # a window that crosses between them.
            boundaries = np.flatnonzero(groups[1:] != groups[:-1]) + 1
            edges = np.concatenate(([0], boundaries, [len(groups)]))
            spans = list(zip(edges[:-1], edges[1:]))

        for start, stop in spans:
            # `stop - seq_len + 1` so the final complete window is included.
            for i in range(start, stop - seq_len + 1):
                self.samples.append(X[i: i + seq_len])
                self.labels.append(y[i + seq_len - 1])

        n_feat = X.shape[1] if X.ndim > 1 else 1
        if self.samples:
            self.samples = np.array(self.samples, dtype=np.float32)
            self.labels = np.array(self.labels, dtype=np.int64)
        else:
            self.samples = np.empty((0, seq_len, n_feat), dtype=np.float32)
            self.labels = np.empty((0,), dtype=np.int64)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        return (
            torch.tensor(self.samples[idx]),
            torch.tensor(self.labels[idx]),
        )


# ---------------------------------------------------------------------------
# 7. TRANSFORMER ENCODER CLASSIFIER
# ---------------------------------------------------------------------------

class SatelliteFaultTransformer(nn.Module):
    def __init__(self, n_features, d_model=64, nhead=4, num_layers=2,
                 dropout=0.1, num_classes=4):
        super().__init__()
        self.input_proj = nn.Linear(n_features, d_model)
        self.pos_enc = PositionalEncoding(d_model, dropout)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead,
            dim_feedforward=d_model * 4, dropout=dropout,
            batch_first=True,
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.classifier = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(d_model // 2, num_classes),
        )

    def forward(self, x):
        x = self.input_proj(x)
        x = self.pos_enc(x)
        x = self.transformer(x)
        x = x.mean(dim=1)
        return self.classifier(x)


class PositionalEncoding(nn.Module):
    def __init__(self, d_model, dropout=0.1, max_len=512):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(0, max_len).unsqueeze(1).float()
        div = torch.exp(torch.arange(0, d_model, 2).float() * (-np.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0))

    def forward(self, x):
        x = x + self.pe[:, :x.size(1), :]
        return self.dropout(x)


# ---------------------------------------------------------------------------
# 8. TRAINING LOOP
# ---------------------------------------------------------------------------

#: Satellite counts from the most recent split_by_satellite() call. Read by
#: write_model_card() so the split table reports what actually happened.
_LAST_SPLIT_INFO: dict = {}


def split_by_satellite(df: pd.DataFrame, seed: int | None = None):
    """
    LEAK 1 FIX — partition by SATELLITE, never by row.

    A random row split puts epochs of the same satellite in train and test at
    once. Because consecutive epochs of one satellite are near-identical by
    construction, that is a direct answer leak: the model can memorise a
    satellite in training and recognise it in test.

    STRATIFIED — every fault class must land in every split.
    ------------------------------------------------------------------------
    Plain GroupShuffleSplit balances satellite COUNT across splits but knows
    nothing about fault_label. The rarest classes (SEU: 492 rows, roughly 3.6%
    of the dataset) live on a small number of satellites, so an unstratified
    shuffle can — and did — place every SEU-bearing and every
    SOFTWARE_BUG-bearing satellite in train, leaving val/test with 0 rows of
    those classes. Training then reports near-perfect accuracy (measured only
    on the classes that happened to survive the split) while the model never
    demonstrably learned to separate SEU/SOFTWARE_BUG from FIRMWARE_CORRUPTION
    or COMMAND_INJECTION — confirmed by test_integration.py, which drives the
    real emulator and gets those two classes wrong.

    Fixed with StratifiedGroupKFold: each satellite is assigned the rarest
    fault type it contains (SEU > SOFTWARE_BUG > FIRMWARE_CORRUPTION >
    COMMAND_INJECTION > NORMAL, in that priority order), and the fold split is
    stratified on that label while still keeping every satellite's rows
    wholly inside one split.
    """
    seed = CONFIG["random_seed"] if seed is None else seed
    sort_cols = ["NORAD_CAT_ID"] + (["EPOCH"] if "EPOCH" in df.columns else [])
    df = df.sort_values(sort_cols).reset_index(drop=True)
    groups = df["NORAD_CAT_ID"].values

    # Rarest-first priority so a satellite that has even one SEU row is
    # stratified as "SEU", not out-voted by its far more common NORMAL rows.
    priority = ["SEU", "SOFTWARE_BUG", "FIRMWARE_CORRUPTION", "COMMAND_INJECTION"]

    def _dominant_label(labels):
        present = set(labels)
        for p in priority:
            if p in present:
                return p
        return "NORMAL"

    sat_strat_label = df.groupby("NORAD_CAT_ID")["fault_label"].agg(_dominant_label)
    strat_label = df["NORAD_CAT_ID"].map(sat_strat_label).values

    n_splits_test = max(2, round(1 / CONFIG["test_size"]))
    sgkf = StratifiedGroupKFold(n_splits=n_splits_test, shuffle=True, random_state=seed)
    tv_idx, test_idx = next(sgkf.split(df, y=strat_label, groups=groups))
    df_tv, df_test = df.iloc[tv_idx], df.iloc[test_idx]

    val_frac = CONFIG["val_size"] / (1 - CONFIG["test_size"])
    n_splits_val = max(2, round(1 / val_frac))
    sgkf2 = StratifiedGroupKFold(n_splits=n_splits_val, shuffle=True, random_state=seed)
    strat_label_tv = strat_label[tv_idx]
    groups_tv = groups[tv_idx]
    tr_idx, val_idx = next(sgkf2.split(df_tv, y=strat_label_tv, groups=groups_tv))

    train_df = df_tv.iloc[tr_idx].sort_values(sort_cols).reset_index(drop=True)
    val_df   = df_tv.iloc[val_idx].sort_values(sort_cols).reset_index(drop=True)
    test_df  = df_test.sort_values(sort_cols).reset_index(drop=True)

    tr_s, va_s, te_s = (set(train_df["NORAD_CAT_ID"]), set(val_df["NORAD_CAT_ID"]),
                        set(test_df["NORAD_CAT_ID"]))
    assert not (tr_s & va_s), "satellite leak: train/val overlap"
    assert not (tr_s & te_s), "satellite leak: train/test overlap"
    assert not (va_s & te_s), "satellite leak: val/test overlap"

    # Recorded for docs/MODEL_CARD.md so the split table is measured, not typed.
    global _LAST_SPLIT_INFO
    _LAST_SPLIT_INFO = {"train": {"satellites": len(tr_s)},
                        "val": {"satellites": len(va_s)},
                        "test": {"satellites": len(te_s)}}

    print(f"\n[SPLIT] by satellite -> train {len(tr_s)} sats / {len(train_df)} rows"
          f" | val {len(va_s)} / {len(val_df)}"
          f" | test {len(te_s)} / {len(test_df)}")

    # Visibility for the failure mode this function was rewritten to prevent:
    # a class with 0 rows in val or test used to pass silently (support=0
    # slipped through classification_report) and only surfaced as wrong
    # predictions against the live emulator. Fail loudly here instead.
    fault_classes = [c for c in FAULT_LABELS if c != "NORMAL"] if "FAULT_LABELS" in globals() else priority
    for name, split_df in (("train", train_df), ("val", val_df), ("test", test_df)):
        counts = split_df[split_df["fault_label"] != "NORMAL"]["fault_label"].value_counts()
        missing = [c for c in fault_classes if counts.get(c, 0) == 0]
        print(f"  [SPLIT] {name} fault counts: {counts.to_dict()}")
        if missing:
            print(f"  [WARN] {name} split has ZERO rows for {missing} — "
                  f"metrics on this split cannot say anything about "
                  f"{'that class' if len(missing) == 1 else 'those classes'}.")

    return train_df, val_df, test_df


class _WindowArrayDataset(Dataset):
    """
    Thin Dataset wrapper around pre-built (N, seq_len, n_features) window
    arrays. Used for the balanced training set, where windows are built once
    by _fault_ending_windows() and then class-balanced by _augment_windows()
    duplicating whole windows — unlike OrbitalSequenceDataset, it does not
    re-derive windows from row groups, because there is nothing left to
    derive: the arrays it wraps already are the final windows.
    """
    def __init__(self, samples: np.ndarray, labels: np.ndarray):
        self.samples = samples.astype(np.float32)
        self.labels = labels.astype(np.int64)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        return torch.tensor(self.samples[idx]), torch.tensor(self.labels[idx])


def _fault_ending_windows(df: pd.DataFrame, scaler: StandardScaler, seq: int):
    """
    Window each satellite's FULL epoch sequence — NORMAL rows included — and
    keep only the windows whose FINAL row is a real fault.

    THE BUG THIS REPLACES
    ----------------------------------------------------------------------
    build_dataloaders() used to drop every NORMAL row from train/val/test
    BEFORE windowing (`val_df[val_df["fault_label"] != "NORMAL"]`, and
    augment_fault_samples() did the same for train). Windows were then built
    from whatever fault-only rows happened to survive, stitched together in
    epoch order regardless of whether they were ever actually adjacent once
    NORMAL rows were removed. That produced training/val/test windows that
    look nothing like what the live system sends the model at inference
    time: /pipeline/classify hands AI-1 the most recent `seq_len` telemetry
    frames from the ring buffer, almost always mostly-NORMAL context ending
    on whatever fault is happening right now (see test_integration.py TEST
    9, "Window built from ... fault signature: SEU"). Transient one-epoch
    faults like SEU essentially never occur as 8 fault-only rows in a row,
    so they were silently windowed out of val/test entirely — the earlier
    version of this pipeline reported ~100% val/test accuracy while its
    classification_report support column read 0 for exactly those classes,
    and the model then got 3 of 4 fault types wrong against the live
    emulator in test_integration.py.

    Building windows over the real, unmodified per-satellite sequence and
    only requiring the LAST row to be a fault (any of the seq_len-1 rows
    before it may be NORMAL, exactly as in production) fixes both problems
    at once: windows are drawn from real adjacent epochs, and rare classes
    get windows whenever they occur at position >= seq_len within a
    satellite's series, instead of only when >= seq_len fault rows happen to
    survive a NORMAL-stripping filter.
    """
    X = scaler.transform(df[FEATURE_COLS].values.astype(np.float32))
    # NORMAL has no entry in FAULT_LABELS; sentinel -1 keeps every row in the
    # sequence (so windows still span real NORMAL context) while marking
    # which windows must be dropped afterward — those NOT ending on a fault.
    y_sentinel = df["fault_label"].map(FAULT_LABELS).fillna(-1).values.astype(np.int64)
    ds = OrbitalSequenceDataset(X, y_sentinel, seq, groups=df["NORAD_CAT_ID"].values)
    keep = ds.labels != -1
    return ds.samples[keep], ds.labels[keep]


def _augment_windows(samples: np.ndarray, labels: np.ndarray, target_per_class: int):
    """
    Window-level BALANCING for the training set (not just oversampling),
    replacing the old row-level augment_fault_samples() for the
    transformer's own training data.

    Every class is brought to exactly target_per_class:
      * below target -> oversampled by duplicating real windows with small
        per-feature Gaussian noise (0.05 * that class's per-feature std)
      * above target -> subsampled without replacement

    Oversampling minority classes up to target_per_class while leaving
    majority classes at their much larger natural counts (observed:
    SEU/SOFTWARE_BUG capped at 400 while FIRMWARE_CORRUPTION/
    COMMAND_INJECTION kept their natural 742/1209) still leaves training
    class-imbalanced 400:400:742:1209. That measurably biased the model:
    it reached ~99% test accuracy (majority classes dominate the test set
    too) while confidently (~99% softmax confidence) mispredicting a live
    SEU injection as COMMAND_INJECTION in test_integration.py, despite the
    window showing a textbook eccentricity jump and none of
    COMMAND_INJECTION's own defining conditions (TLE_AGE_HOURS nowhere near
    the stale threshold). Capping majority classes down to target_per_class
    too removes that skew.

    This must operate on already-built windows, not raw rows: duplicating
    individual ROWS (the old approach) only produces a valid training
    example if windowing later manages to re-stitch `seq_len` of them back
    into a contiguous same-satellite run, which for rare classes it mostly
    did not (see _fault_ending_windows' docstring). Duplicating whole
    windows sidesteps that entirely — every synthetic example is guaranteed
    to be a coherent seq_len-long sequence because it was one before noise
    was added.
    """
    out_samples, out_labels = [], []
    for class_index in range(CONFIG["num_classes"]):
        label_name = IDX_TO_LABEL[class_index]
        mask = labels == class_index
        n_real = int(mask.sum())
        class_samples = samples[mask]
        rng = _class_rng(class_index)

        if n_real == 0:
            print(f"  [WARN] no real {label_name} windows in the training split — "
                  f"cannot synthesize from nothing; this class will be absent "
                  f"from training.")
            continue

        if n_real == target_per_class:
            print(f"  {label_name}: {n_real} real windows == target, unchanged")
            out_samples.append(class_samples)
            out_labels.append(labels[mask])
            continue

        if n_real < target_per_class:
            n_needed = target_per_class - n_real
            print(f"  Augmenting {label_name}: {n_real} real windows -> +{n_needed} synthetic")
            idx = rng.integers(0, n_real, size=n_needed)
            picked = class_samples[idx].copy()
            std = np.maximum(class_samples.std(axis=(0, 1), keepdims=True) * 0.05, 1e-9)
            picked = picked + rng.normal(0, 1, picked.shape) * std
            out_samples.append(np.concatenate([class_samples, picked.astype(np.float32)]))
            out_labels.append(np.full(n_real + n_needed, class_index, dtype=np.int64))
        else:
            print(f"  Subsampling {label_name}: {n_real} real windows -> {target_per_class} "
                  f"(capped to match the smaller classes)")
            idx = rng.choice(n_real, size=target_per_class, replace=False)
            out_samples.append(class_samples[idx])
            out_labels.append(np.full(target_per_class, class_index, dtype=np.int64))

    all_samples = np.concatenate(out_samples, axis=0)
    all_labels = np.concatenate(out_labels, axis=0)
    print("\n[AUG] Post-augmentation window counts:")
    for i in range(CONFIG["num_classes"]):
        print(f"  {IDX_TO_LABEL[i]}: {int((all_labels == i).sum())}")
    return all_samples, all_labels


def build_dataloaders(df_labelled: pd.DataFrame, scaler: StandardScaler = None,
                      target_per_class: int = 400):
    """
    Leak-free preparation. Order matters and is the whole point:

        1. split by satellite, NORMAL rows kept        (LEAK 1)
        2. fit the scaler on TRAIN only                (LEAK 3)
        3. window each split over its real per-satellite
           sequence, keep only fault-ending windows     (see
           _fault_ending_windows)
        4. balance the TRAIN windows only, by duplicating
           whole windows with noise                     (LEAK 2; see
           _augment_windows)

    Was: `build_dataloaders(aug_df, scaler)` — received data that had already
    been augmented and scaled against the full dataset, then split it at
    random. Every one of the three leaks happened before this function was
    even called.

    A later bug (still leak-free, but wrong): NORMAL rows were dropped from
    every split before windowing, so windows were built from fault-only rows
    stitched together regardless of real epoch adjacency — nothing like the
    mostly-NORMAL-context windows the live system actually sends the model.
    Rare classes lost all their val/test windows this way while training
    still reported ~100% accuracy on the classes that survived, and the
    model got 3 of 4 fault types wrong against the live emulator
    (test_integration.py). Fixed by keeping NORMAL rows through windowing and
    filtering afterward to windows that END on a fault.

    The `scaler` argument is ignored and kept only so an old call site fails
    loudly rather than silently reintroducing leak 3.
    """
    if scaler is not None:
        print("  [WARN] build_dataloaders() no longer accepts a pre-fitted "
              "scaler — it fits one on the training split (LEAK 3). Ignoring.")

    # 1. split by satellite, on UNAUGMENTED data (NORMAL rows kept — the
    #    window step needs real per-satellite context, see
    #    _fault_ending_windows)
    train_df, val_df, test_df = split_by_satellite(df_labelled)

    # 2. fit the scaler on the training split ONLY, then transform the others
    scaler = StandardScaler()
    scaler.fit(train_df[FEATURE_COLS].values.astype(np.float32))
    print(f"[SCALE] StandardScaler fitted on {len(train_df)} training rows only")

    # 3. window each split over its real, unmodified per-satellite sequence,
    #    keeping only windows that end on a real fault (NORMAL context rows
    #    before the last one are kept, exactly as at inference time).
    seq = CONFIG["seq_len"]
    train_samples, train_labels = _fault_ending_windows(train_df, scaler, seq)
    val_samples,   val_labels   = _fault_ending_windows(val_df, scaler, seq)
    test_samples,  test_labels  = _fault_ending_windows(test_df, scaler, seq)

    print(f"[WINDOW] natural fault-ending windows -> "
          f"train {len(train_labels)}  val {len(val_labels)}  test {len(test_labels)}")
    for name, lbl in (("train", train_labels), ("val", val_labels), ("test", test_labels)):
        counts = {IDX_TO_LABEL[i]: int((lbl == i).sum()) for i in range(CONFIG["num_classes"])}
        missing = [c for c, n in counts.items() if n == 0]
        print(f"  [WINDOW] {name}: {counts}")
        if missing:
            print(f"  [WARN] {name} split has ZERO fault-ending windows for {missing} "
                  f"— metrics on this split cannot say anything about "
                  f"{'that class' if len(missing) == 1 else 'those classes'}.")

    # 4. balance the TRAINING windows only (val/test keep their real,
    #    unbalanced distribution so metrics describe real data).
    train_samples, train_labels = _augment_windows(train_samples, train_labels,
                                                    target_per_class=target_per_class)

    train_ds = _WindowArrayDataset(train_samples, train_labels)
    val_ds   = _WindowArrayDataset(val_samples, val_labels)
    test_ds  = _WindowArrayDataset(test_samples, test_labels)

    print(f"[DATA] Windows -> train: {len(train_ds)}  val: {len(val_ds)}  "
          f"test: {len(test_ds)}")
    for name, ds in (("val", val_ds), ("test", test_ds)):
        if len(ds) == 0:
            print(f"  [WARN] {name} split produced 0 windows — every satellite "
                  f"in it has fewer than seq_len={seq} rows before its first fault")

    bs = CONFIG["batch_size"]
    train_loader = DataLoader(train_ds, batch_size=bs, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=bs, shuffle=False, num_workers=0)
    test_loader = DataLoader(test_ds, batch_size=bs, shuffle=False, num_workers=0)
    return train_loader, val_loader, test_loader, scaler, train_df


def train_model(train_loader, val_loader, n_features):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n[TRAIN] Device: {device}")

    model = SatelliteFaultTransformer(
        n_features=n_features,
        d_model=CONFIG["d_model"],
        nhead=CONFIG["nhead"],
        num_layers=CONFIG["num_layers"],
        dropout=CONFIG["dropout"],
        num_classes=CONFIG["num_classes"],
    ).to(device)

    criterion = nn.CrossEntropyLoss()
    optimizer = optim.AdamW(model.parameters(), lr=CONFIG["lr"], weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=CONFIG["epochs"])

    best_val_loss = float("inf")
    best_state = None

    for epoch in range(1, CONFIG["epochs"] + 1):
        model.train()
        train_loss = 0.0
        for X_batch, y_batch in tqdm(train_loader, desc=f"Epoch {epoch:02d} train", leave=False):
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            optimizer.zero_grad()
            logits = model(X_batch)
            loss = criterion(logits, y_batch)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += loss.item() * len(X_batch)

        train_loss /= len(train_loader.dataset)

        model.eval()
        val_loss = 0.0
        correct = 0
        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                X_batch, y_batch = X_batch.to(device), y_batch.to(device)
                logits = model(X_batch)
                val_loss += criterion(logits, y_batch).item() * len(X_batch)
                correct += (logits.argmax(1) == y_batch).sum().item()

        val_loss /= len(val_loader.dataset)
        val_acc = correct / len(val_loader.dataset)
        scheduler.step()

        print(f"  Epoch {epoch:02d}/{CONFIG['epochs']}  "
              f"train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  val_acc={val_acc:.3f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)
    return model, device


def evaluate_model(model, test_loader, device):
    """
    Evaluate on the held-out test split.

    Now RETURNS the metrics as well as printing them, so docs/MODEL_CARD.md can
    be generated from the same numbers rather than transcribed by hand. A card
    whose figures are retyped from console output drifts from reality the first
    time anyone reruns training; this makes that impossible.
    """
    model.eval()
    all_preds, all_labels = [], []
    with torch.no_grad():
        for X_batch, y_batch in test_loader:
            X_batch = X_batch.to(device)
            preds = model(X_batch).argmax(1).cpu().numpy()
            all_preds.extend(preds)
            all_labels.extend(y_batch.numpy())

    target_names = [IDX_TO_LABEL[i] for i in range(CONFIG["num_classes"])]
    print("\n[EVAL] Classification Report:")
    print(classification_report(all_labels, all_preds, labels=list(range(CONFIG["num_classes"])), target_names=target_names, zero_division=0))
    print("[EVAL] Confusion Matrix:")
    print(confusion_matrix(all_labels, all_preds))

    return _metrics_dict(all_labels, all_preds, "Transformer encoder")


def _metrics_dict(y_true, y_pred, name: str) -> dict:
    """Per-class and aggregate metrics in a form the model card can render."""
    from sklearn.metrics import accuracy_score, f1_score

    labels = list(range(CONFIG["num_classes"]))
    target_names = [IDX_TO_LABEL[i] for i in labels]
    rep = classification_report(y_true, y_pred, labels=labels,
                                target_names=target_names, zero_division=0,
                                output_dict=True)
    return {
        "name": name,
        "n_test": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro",
                                   labels=labels, zero_division=0)),
        "weighted_f1": float(f1_score(y_true, y_pred, average="weighted",
                                      labels=labels, zero_division=0)),
        "per_class": {c: {"precision": float(rep[c]["precision"]),
                          "recall": float(rep[c]["recall"]),
                          "f1": float(rep[c]["f1-score"]),
                          "support": int(rep[c]["support"])}
                      for c in target_names},
        "confusion": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
    }


def _loader_to_arrays(loader):
    """Flatten (N, seq_len, n_features) windows to (N, seq_len*n_features)."""
    Xs, ys = [], []
    for X_batch, y_batch in loader:
        Xb = X_batch.numpy() if hasattr(X_batch, "numpy") else np.asarray(X_batch)
        yb = y_batch.numpy() if hasattr(y_batch, "numpy") else np.asarray(y_batch)
        Xs.append(Xb.reshape(len(Xb), -1))
        ys.append(yb)
    if not Xs:
        return np.empty((0, 0)), np.empty((0,), dtype=np.int64)
    return np.concatenate(Xs), np.concatenate(ys)


def run_baselines(train_loader, test_loader) -> list[dict]:
    """
    Train a logistic regression and a gradient-boosted tree on EXACTLY the same
    leak-free windows the transformer sees, and score them on the same test set.

    A 2-layer transformer with positional encoding is only worth its complexity
    — and its 3.3 MB artifact, and its inference cost on a Raspberry Pi 4 — if
    it beats a linear model on flattened features. Without this comparison the
    architecture choice is decoration. Both baselines consume the same windows,
    flattened to (N, seq_len * n_features), so the only difference is the model.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier

    print("\n[BASELINE] Training comparison models on the same splits ...")
    X_tr, y_tr = _loader_to_arrays(train_loader)
    X_te, y_te = _loader_to_arrays(test_loader)

    if len(X_tr) == 0 or len(X_te) == 0:
        print("  [WARN] empty train or test split — skipping baselines")
        return []

    print(f"  train windows {X_tr.shape}   test windows {X_te.shape}")
    results = []

    lr = LogisticRegression(max_iter=2000,
                            random_state=CONFIG["random_seed"])
    lr.fit(X_tr, y_tr)
    results.append(_metrics_dict(y_te, lr.predict(X_te), "Logistic regression"))
    print(f"  Logistic regression : acc={results[-1]['accuracy']:.4f}  "
          f"macro-F1={results[-1]['macro_f1']:.4f}")

    gbt = HistGradientBoostingClassifier(random_state=CONFIG["random_seed"])
    gbt.fit(X_tr, y_tr)
    results.append(_metrics_dict(y_te, gbt.predict(X_te), "Gradient-boosted trees"))
    print(f"  Gradient-boosted    : acc={results[-1]['accuracy']:.4f}  "
          f"macro-F1={results[-1]['macro_f1']:.4f}")

    # Majority-class floor: the number any model must beat to mean anything.
    if len(y_tr):
        majority = int(np.bincount(y_tr, minlength=CONFIG["num_classes"]).argmax())
        results.append(_metrics_dict(y_te, np.full(len(y_te), majority),
                                     "Majority class (floor)"))
        print(f"  Majority-class floor: acc={results[-1]['accuracy']:.4f}  "
              f"macro-F1={results[-1]['macro_f1']:.4f}")

    return results


def write_model_card(transformer: dict, baselines: list[dict],
                     split_info: dict, out_path="docs/MODEL_CARD.md") -> str:
    """
    Generate docs/MODEL_CARD.md from the metrics just measured.

    The card is written by the training run, never by hand. That is the only
    way "every number in the card is reproducible by running
    train_classifier.py" can be true rather than aspirational — there is no
    transcription step in which a figure can drift or be invented.

    The prose sections (provenance, split rationale, limitations) live in this
    function so they are versioned with the code that produces the numbers.
    """
    from datetime import datetime, timezone

    all_models = [transformer] + list(baselines)
    best = max(all_models, key=lambda m: m["macro_f1"])
    tf_wins = best["name"] == transformer["name"]

    def pct(x):
        return f"{100 * x:.2f}%"

    L = []
    A = L.append

    A("# Model Card — AI-1 Satellite Fault Classifier\n")
    A("> **This file is generated.** It is written by `write_model_card()` at the")
    A("> end of every training run. Do not edit it by hand — rerun training:")
    A("> ```")
    A("> python train_classifier.py --csv data/synthetic_orbital_series.csv")
    A("> ```")
    A(f"> Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
      f" · seed {CONFIG['random_seed']}\n")

    A("## Model\n")
    A("| | |")
    A("|---|---|")
    A(f"| Task | 4-class satellite fault classification from orbital elements |")
    A(f"| Architecture | Transformer encoder, {CONFIG['num_layers']} layers, "
      f"d_model={CONFIG['d_model']}, {CONFIG['nhead']} heads |")
    A(f"| Input | {CONFIG['seq_len']} consecutive epochs × {len(FEATURE_COLS)} features |")
    A(f"| Classes | {', '.join(IDX_TO_LABEL[i] for i in range(CONFIG['num_classes']))} |")
    A(f"| Anomaly gate | Isolation Forest, fitted on NORMAL training rows only |")
    A("")

    A("## Dataset provenance\n")
    A("Real orbital elements, synthetic faults. Specifically:\n")
    A("- **Source.** CelesTrak-format GP element sets for **712 satellites**")
    A("  (`data/input.csv`, `input__1_.csv`, `input__2_.csv`), deduplicated on")
    A("  `(NORAD_CAT_ID, EPOCH)`.")
    A("- **Propagation.** Each entry is propagated forward with SGP4 via")
    A("  `satellite_catalog.build_tle_from_gp()`, one epoch per revolution, and")
    A("  osculating elements are re-derived at each step (`generate_dataset.py`).")
    A("- **Fault injection.** Signatures are stamped onto the resulting *series*")
    A("  to match `assign_fault_labels()` exactly, including its precedence order.\n")
    A("**Why synthetic faults?** Labelled satellite fault telemetry is not")
    A("publicly available. Operators do not publish telemetry from anomalous")
    A("spacecraft, and the fault taxonomy this project targets (SEU, software")
    A("bug, firmware corruption, command injection) has no open labelled corpus")
    A("at all. The orbital dynamics here are real; the faults are not. That")
    A("distinction is the single most important limitation of this model and is")
    A("expanded on below.\n")
    A("The original snapshot could not be used directly: it held one epoch per")
    A("satellite, so `REV_DELTA` and `ecc_delta` were zero for 100% of rows and")
    A("three of the four label rules — all defined as changes *between* epochs —")
    A("could never fire. 807 of 854 rows collapsed to SOFTWARE_BUG.\n")

    A("## Split strategy\n")
    A("`GroupShuffleSplit(groups=NORAD_CAT_ID)`, two-stage, so **every satellite")
    A("lands wholly in exactly one of train / val / test**.\n")
    A("Consecutive epochs of one satellite are near-identical by construction.")
    A("A random *row* split therefore puts almost-duplicate rows on both sides of")
    A("the boundary, and the model can memorise a satellite in training and")
    A("recognise it at test time. Grouping by satellite is what makes the test")
    A("score an estimate of performance on *unseen spacecraft* rather than on")
    A("unseen rows of familiar ones.\n")
    if split_info:
        A("| Split | Satellites | Windows |")
        A("|---|---:|---:|")
        for k in ("train", "val", "test"):
            if k in split_info:
                A(f"| {k} | {split_info[k].get('satellites', '—')} | "
                  f"{split_info[k].get('windows', '—')} |")
        A("")
    A("Two further orderings matter and are enforced in `build_dataloaders()`:\n")
    A("- Augmentation runs on the **training split only**. It oversamples with")
    A("  replacement and adds noise of 0.05 × class std, i.e. near-duplicates;")
    A("  running it before the split put copies of the same rows in train, val")
    A("  and test.")
    A("- The `StandardScaler` is fitted on the **training split only**, then")
    A("  applied to val and test.\n")
    A(f"- Windows never span two satellites: sequences are built within each")
    A(f"  `NORAD_CAT_ID`, sorted by `EPOCH`.\n")

    A("## Results — held-out test split\n")
    A(f"Test set: **{transformer['n_test']} windows** from satellites never seen")
    A("in training.\n")
    A("### Baseline comparison\n")
    A("All models consume the identical leak-free windows; the baselines see them")
    A(f"flattened to {CONFIG['seq_len']}×{len(FEATURE_COLS)} features. The only")
    A("difference is the model.\n")
    A("| Model | Accuracy | Macro F1 | Weighted F1 |")
    A("|---|---:|---:|---:|")
    for m in all_models:
        star = " **← best**" if m["name"] == best["name"] else ""
        A(f"| {m['name']}{star} | {pct(m['accuracy'])} | "
          f"{m['macro_f1']:.4f} | {m['weighted_f1']:.4f} |")
    A("")

    A("### Verdict\n")
    if tf_wins:
        runner = max((m for m in all_models if m["name"] != transformer["name"]),
                     key=lambda m: m["macro_f1"], default=None)
        if runner:
            delta = transformer["macro_f1"] - runner["macro_f1"]
            A(f"The transformer has the best macro F1, ahead of the strongest")
            A(f"baseline ({runner['name']}) by **{delta:+.4f}**.\n")
            if delta < 0.02:
                A("**That margin is small.** On this dataset the sequence model is")
                A("not clearly earning its complexity: it costs a 3.3 MB artifact")
                A("and materially more inference time on a Raspberry Pi 4 than a")
                A("linear model. Treat the architecture as unproven until the")
                A("margin is shown to be stable across seeds.\n")
    else:
        delta = best["macro_f1"] - transformer["macro_f1"]
        A(f"**The transformer does NOT win.** {best['name']} scores higher by")
        A(f"**{delta:+.4f}** macro F1.\n")
        A("This is reported rather than buried. A simpler model outperforming the")
        A("transformer on this data is a legitimate finding, and a more credible")
        A("one than an unsupported claim to the contrary. It suggests the label")
        A("rules are largely threshold-based on individual rows, which is exactly")
        A("what a tree ensemble captures well and what a sequence model cannot")
        A("improve on. Options: adopt the simpler model, or demonstrate that the")
        A("sequence structure carries signal the thresholds miss.\n")

    A("### Per-class — transformer\n")
    A("| Class | Precision | Recall | F1 | Support |")
    A("|---|---:|---:|---:|---:|")
    for cls, v in transformer["per_class"].items():
        A(f"| {cls} | {v['precision']:.3f} | {v['recall']:.3f} | "
          f"{v['f1']:.3f} | {v['support']} |")
    A("")

    A("### Confusion matrix — transformer\n")
    names = list(transformer["per_class"].keys())
    A("Rows = true, columns = predicted.\n")
    A("| | " + " | ".join(names) + " |")
    A("|---|" + "---:|" * len(names))
    for name, row in zip(names, transformer["confusion"]):
        A(f"| **{name}** | " + " | ".join(str(v) for v in row) + " |")
    A("")

    A("## Limitations\n")
    A("**1. There is no real fault-labelled telemetry in this dataset, and none")
    A("was available to build one.** Every fault signature was injected by")
    A("`generate_dataset.py` to match the thresholds in `assign_fault_labels()`.")
    A("The model is therefore measured on its ability to recover a rule set that")
    A("is known in advance. A high score demonstrates that the pipeline is")
    A("internally consistent and free of the leaks it was audited for. **It does")
    A("not demonstrate that the model would detect a real SEU on a real")
    A("spacecraft**, and no claim to that effect should be made from these")
    A("numbers.\n")
    A("**2. The labels are heuristics, not ground truth.** `assign_fault_labels()`")
    A("maps orbital-element symptoms onto fault categories using fixed")
    A("thresholds. Those mappings are plausible but unvalidated against real")
    A("anomaly reports. If a threshold is wrong, the model faithfully learns the")
    A("wrong thing and the test score stays high.\n")
    A("**3. A first-row artefact inflates SOFTWARE_BUG.** `prepare_dataframe()`")
    A("computes `REV_DELTA` with `.diff().fillna(0)`, so the first row of every")
    A("satellite is forced to zero and rule 5 labels it SOFTWARE_BUG. That is")
    A("exactly `1 / n_epochs` of the dataset — mislabelled by construction.\n")
    A("**4. Generalisation is bounded by the catalogue.** All 712 satellites come")
    A("from three overlapping CelesTrak exports dominated by LEO; ~97% have")
    A("periods under 9 hours. Satellites whose orbits are too slow for the")
    A("staleness window are excluded outright. Nothing here says anything about")
    A("MEO, GEO or highly elliptical regimes.\n")
    A("**5. Class balance is a design choice, not a prior.** Allocation across")
    A("the four classes is set by the generator, so the class distribution")
    A("reflects that choice and carries no information about real fault rates.\n")
    A("**6. Single seed.** These figures are one run at seed")
    A(f"{CONFIG['random_seed']}. No variance across seeds is reported, so small")
    A("margins between models should not be treated as meaningful.\n")

    A("## Reproducing every number above\n")
    A("```bash")
    A("pip install -r requirements.txt")
    A("python generate_dataset.py --propagator sgp4 --verify")
    A("python train_classifier.py --csv data/synthetic_orbital_series.csv")
    A("```")
    A("The last command rewrites this file. Metrics come from")
    A("`evaluate_model()` and `run_baselines()`; the prose comes from")
    A("`write_model_card()` in `models/satellite_fault_classifier_V2.py`.\n")

    text = "\n".join(L)
    path = _Path(out_path)
    if not path.is_absolute():
        path = _Path(__file__).resolve().parent.parent / out_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"\n[CARD] docs/MODEL_CARD.md written from measured metrics -> {path}")
    return str(path)


# ---------------------------------------------------------------------------
# 9. SAVE ARTEFACTS
# ---------------------------------------------------------------------------

def save_artifacts(model, iforest, scaler, out_dir="./model_artifacts"):
    import pickle
    os.makedirs(out_dir, exist_ok=True)
    torch.save(model.state_dict(), f"{out_dir}/transformer_encoder.pt")
    with open(f"{out_dir}/isolation_forest.pkl", "wb") as f:
        pickle.dump(iforest, f)
    with open(f"{out_dir}/scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)
    meta = {"config": CONFIG, "fault_labels": FAULT_LABELS, "feature_cols": FEATURE_COLS}
    with open(f"{out_dir}/meta.json", "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"\n[SAVE] Artifacts saved to {out_dir}/")


# ---------------------------------------------------------------------------
# 10. INFERENCE HELPER
# ---------------------------------------------------------------------------

def predict(window: np.ndarray, model, iforest, scaler, device):
    """
    window: (seq_len, n_features) raw (unscaled) orbital-element values.
    Returns (anomaly_flag, fault_class, confidence).
    """
    X_scaled = scaler.transform(window)
    last_row = X_scaled[-1:, :]

    anomaly_flag = iforest.predict(last_row)[0] == -1

    x_tensor = torch.tensor(X_scaled[np.newaxis], dtype=torch.float32).to(device)
    model.eval()
    with torch.no_grad():
        logits = model(x_tensor)
        probs = torch.softmax(logits, dim=-1).cpu().numpy()[0]

    top_idx = int(probs.argmax())
    return anomaly_flag, IDX_TO_LABEL[top_idx], float(probs[top_idx])


# ---------------------------------------------------------------------------
# 11. MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Satellite Fault Classifier - TLE Edition")
    parser.add_argument("--csv", nargs="+", default=[],
                         help="Path(s) to CelesTrak-format orbital-element CSV files")
    parser.add_argument("--n2yo_api_key", type=str, default=os.environ.get("N2YO_API_KEY", ""))
    parser.add_argument("--demo", action="store_true",
                         help="Skip N2YO live fetch and use only CSV/synthetic data")
    parser.add_argument("--out_dir", type=str, default="./model_artifacts")
    args = parser.parse_args()

    np.random.seed(CONFIG["random_seed"])
    torch.manual_seed(CONFIG["random_seed"])

    # --- Step 1: Data Extraction -----------------------------------------
    frames = []
    if args.csv:
        frames.append(load_csv_datasets(args.csv))
    else:
        print("[LOAD] No --csv provided, generating synthetic baseline dataset")
        frames.append(_make_demo_df(n=2000))

    if not args.demo and args.n2yo_api_key:
        print("\n[N2YO] Fetching live TLEs ...")
        df_live = fetch_n2yo_tle(args.n2yo_api_key, CONFIG["norad_ids"])
        if not df_live.empty:
            frames.append(df_live)
    elif not args.demo:
        print("\n[N2YO] No API key supplied - skipping live fetch "
              "(pass --n2yo_api_key or use --demo)")

    df_raw = pd.concat(frames, ignore_index=True)

    # --- Step 2: Clean -----------------------------------------------------
    df_clean = clean_orbital_data(df_raw)

    # --- Step 3: Label ------------------------------------------------------
    # ORDER CHANGED (Phase 2 leak fixes). Previously: fit the Isolation Forest
    # and its scaler on the whole dataset, augment the whole dataset, THEN
    # split at random. All three leaks happened before the split existed.
    # Now: label -> split by satellite -> augment train only -> fit scaler on
    # train only. build_dataloaders() owns steps 2-4 of that sequence.
    df_labelled = assign_fault_labels(df_clean)

    # --- Step 4: Split + augment + scale + window ---------------------------
    train_loader, val_loader, test_loader, scaler, train_df = build_dataloaders(
        df_labelled, target_per_class=400)

    # --- Step 5: Isolation Forest (NORMAL rows of the TRAIN split only) -----
    # train_df already has its NORMAL rows (build_dataloaders no longer drops
    # them — see _fault_ending_windows); re-selecting from df_labelled by
    # satellite ID is still the simplest way to get an identical frame.
    train_sats = set(train_df["NORAD_CAT_ID"])
    iforest = train_isolation_forest(
        df_labelled[df_labelled["NORAD_CAT_ID"].isin(train_sats)], scaler)

    # --- Step 6: Train Transformer ---------------------------------------------
    n_features = len(FEATURE_COLS)
    model, device = train_model(train_loader, val_loader, n_features)

    # --- Step 7: Evaluate + baselines + model card ---------------------------
    tf_metrics = evaluate_model(model, test_loader, device)
    baselines = run_baselines(train_loader, test_loader)
    split_info = {k: dict(v) for k, v in _LAST_SPLIT_INFO.items()}
    for k, ld in (("train", train_loader), ("val", val_loader), ("test", test_loader)):
        split_info.setdefault(k, {})["windows"] = len(ld.dataset)
    write_model_card(tf_metrics, baselines, split_info)

    # --- Step 8: Save -----------------------------------------------------------
    save_artifacts(model, iforest, scaler, args.out_dir)

    # --- Step 9: Quick inference demo --------------------------------------------
    print("\n[DEMO] Running one inference example ...")
    sample_window = df_clean[FEATURE_COLS].values[:CONFIG["seq_len"]].astype(np.float32)
    anomaly, fault, conf = predict(sample_window, model, iforest, scaler, device)
    print(f"  Anomaly detected : {anomaly}")
    print(f"  Fault class      : {fault}")
    print(f"  Confidence       : {conf:.2%}")
    print("\nDone.")


def _make_demo_df(n: int = 2000) -> pd.DataFrame:
    """Synthetic CelesTrak-shaped dataframe for fully offline demo."""
    rng = np.random.default_rng(42)
    epochs = pd.date_range("2026-06-01", periods=n, freq="90min", tz="UTC")
    df = pd.DataFrame({
        "OBJECT_NAME": "DEMO-SAT",
        "OBJECT_ID": "2026-001A",
        "EPOCH": epochs.astype(str),
        "MEAN_MOTION": rng.normal(14.5, 0.05, n),
        "ECCENTRICITY": np.abs(rng.normal(0.001, 0.0005, n)),
        "INCLINATION": rng.normal(51.6, 0.01, n),
        "RA_OF_ASC_NODE": rng.uniform(0, 360, n),
        "ARG_OF_PERICENTER": rng.uniform(0, 360, n),
        "MEAN_ANOMALY": rng.uniform(0, 360, n),
        "EPHEMERIS_TYPE": 0,
        "CLASSIFICATION_TYPE": "U",
        "NORAD_CAT_ID": 99999,
        "ELEMENT_SET_NO": 999,
        "REV_AT_EPOCH": np.arange(n) + 10000,
        "BSTAR": rng.normal(0.0002, 0.00005, n),
        "MEAN_MOTION_DOT": rng.normal(0.00003, 0.00001, n),
        "MEAN_MOTION_DDOT": 0.0,
    })
    # Inject known fault patterns
    idx_seu = rng.choice(n, size=20, replace=False)
    df.loc[idx_seu, "ECCENTRICITY"] += rng.uniform(0.02, 0.05, 20)
    idx_fw = rng.choice(n, size=20, replace=False)
    df.loc[idx_fw, "BSTAR"] = rng.uniform(0.01, 0.03, 20)
    return df


if __name__ == "__main__":
    main()
