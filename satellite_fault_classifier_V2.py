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
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import IsolationForest
from sklearn.metrics import classification_report, confusion_matrix
from tqdm import tqdm


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
CONFIG = {
    # N2YO live refresh targets (NORAD IDs). Add/remove as needed.
    "norad_ids": [25544, 7530, 27844, 14129, 33591],
    "n2yo_base": "https://api.n2yo.com/rest/v1/satellite",

    # --- Fault thresholds (tuned for orbital-element data) ---------------
    "tle_age_stale_hours": 72.0,       # TLE older than this -> COMMS / GROUND SEGMENT issue
    "eccentricity_jump_threshold": 0.01,   # sudden change in orbital eccentricity
    "bstar_anomaly_threshold": 0.005,      # abnormal drag term (decay/attitude fault)
    "mean_motion_dot_threshold": 0.001,    # abnormal orbital decay rate
    "rev_gap_threshold": 50,               # missing revolutions between epochs

    # --- Model -------------------------------------------------------------
    "seq_len": 8,                       # time-steps (epochs) per sample window
    "d_model": 64,
    "nhead": 4,
    "num_layers": 2,
    "dropout": 0.1,
    "num_classes": 4,                   # SEU / SW_BUG / FW_CORRUPT / CMD_INJECT

    # --- Training ------------------------------------------------------------
    "batch_size": 32,
    "epochs": 30,
    "lr": 1e-3,
    "test_size": 0.2,
    "val_size": 0.1,
    "random_seed": 42,

    # --- Isolation Forest ----------------------------------------------------
    "if_contamination": 0.05,
    "if_n_estimators": 100,
}

FAULT_LABELS = {
    "SEU": 0,
    "SOFTWARE_BUG": 1,
    "FIRMWARE_CORRUPTION": 2,
    "COMMAND_INJECTION": 3,
}
IDX_TO_LABEL = {v: k for k, v in FAULT_LABELS.items()}

# Feature set derived from orbital elements (replaces voltage/current/RSSI/etc.)
FEATURE_COLS = [
    "MEAN_MOTION",          # revs/day - orbital speed
    "ECCENTRICITY",         # orbit shape (0 = circular)
    "INCLINATION",          # orbital plane tilt (deg)
    "RA_OF_ASC_NODE",       # right ascension of ascending node (deg)
    "ARG_OF_PERICENTER",    # argument of perigee (deg)
    "MEAN_ANOMALY",         # position in orbit (deg)
    "BSTAR",                # drag term
    "MEAN_MOTION_DOT",      # 1st derivative of mean motion (decay rate)
    "MEAN_MOTION_DDOT",     # 2nd derivative of mean motion
    "TLE_AGE_HOURS",        # derived: hours since EPOCH (data-staleness proxy)
    "REV_DELTA",            # derived: change in REV_AT_EPOCH between consecutive rows
]


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

def augment_fault_samples(df: pd.DataFrame, target_per_class: int = 300) -> pd.DataFrame:
    """Gaussian-noise augmentation around real fault samples to balance classes."""
    fault_df = df[df["fault_label"] != "NORMAL"].copy()
    augmented_rows = []

    for label in FAULT_LABELS:
        class_df = fault_df[fault_df["fault_label"] == label]
        n_needed = max(0, target_per_class - len(class_df))
        if n_needed == 0:
            continue

        print(f"  Augmenting {label}: {len(class_df)} real -> +{n_needed} synthetic")
        if len(class_df) == 0:
            class_df = _generate_synthetic_class(label, n=target_per_class)

        samples = class_df.sample(n=n_needed, replace=True, random_state=CONFIG["random_seed"])
        for col in FEATURE_COLS:
            std = max(class_df[col].std() * 0.05, 1e-9)
            samples[col] = samples[col] + np.random.normal(0, std, n_needed)
        augmented_rows.append(samples)

    if augmented_rows:
        aug_df = pd.concat([fault_df] + augmented_rows, ignore_index=True)
    else:
        aug_df = fault_df

    print("\n[AUG] Post-augmentation fault counts:")
    print(aug_df["fault_label"].value_counts().to_string())
    return aug_df


def _generate_synthetic_class(label: str, n: int) -> pd.DataFrame:
    """Fallback synthetic generator if a fault class has zero real examples."""
    rng = np.random.default_rng(CONFIG["random_seed"])
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

def train_isolation_forest(df_clean: pd.DataFrame):
    print("\n[IF] Training Isolation Forest anomaly detector ...")
    X = df_clean[FEATURE_COLS].values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    iforest = IsolationForest(
        n_estimators=CONFIG["if_n_estimators"],
        contamination=CONFIG["if_contamination"],
        random_state=CONFIG["random_seed"],
        n_jobs=-1,
    )
    iforest.fit(X_scaled)
    anomaly_pct = (iforest.predict(X_scaled) == -1).mean() * 100
    print(f"  Anomaly rate detected: {anomaly_pct:.1f}%")
    return iforest, scaler


# ---------------------------------------------------------------------------
# 6. SLIDING-WINDOW SEQUENCES -> Dataset
# ---------------------------------------------------------------------------

class OrbitalSequenceDataset(Dataset):
    """Converts tabular orbital-element rows into fixed-length sequences."""

    def __init__(self, X: np.ndarray, y: np.ndarray, seq_len: int = 8):
        self.seq_len = seq_len
        self.samples = []
        self.labels = []

        for i in range(len(X) - seq_len):
            self.samples.append(X[i: i + seq_len])
            self.labels.append(y[i + seq_len - 1])

        self.samples = np.array(self.samples, dtype=np.float32)
        self.labels = np.array(self.labels, dtype=np.int64)

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

def build_dataloaders(aug_df: pd.DataFrame, scaler: StandardScaler):
    X_raw = aug_df[FEATURE_COLS].values.astype(np.float32)
    y_raw = aug_df["fault_label"].map(FAULT_LABELS).values.astype(np.int64)

    X_scaled = scaler.transform(X_raw)

    X_tv, X_test, y_tv, y_test = train_test_split(
        X_scaled, y_raw, test_size=CONFIG["test_size"],
        stratify=y_raw, random_state=CONFIG["random_seed"],
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_tv, y_tv, test_size=CONFIG["val_size"] / (1 - CONFIG["test_size"]),
        stratify=y_tv, random_state=CONFIG["random_seed"],
    )

    seq = CONFIG["seq_len"]
    train_ds = OrbitalSequenceDataset(X_train, y_train, seq)
    val_ds = OrbitalSequenceDataset(X_val, y_val, seq)
    test_ds = OrbitalSequenceDataset(X_test, y_test, seq)

    print(f"\n[DATA] Split sizes -> train: {len(train_ds)}  val: {len(val_ds)}  test: {len(test_ds)}")

    bs = CONFIG["batch_size"]
    train_loader = DataLoader(train_ds, batch_size=bs, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=bs, shuffle=False, num_workers=0)
    test_loader = DataLoader(test_ds, batch_size=bs, shuffle=False, num_workers=0)
    return train_loader, val_loader, test_loader


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
    print(classification_report(all_labels, all_preds, target_names=target_names, zero_division=0))
    print("[EVAL] Confusion Matrix:")
    print(confusion_matrix(all_labels, all_preds))


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

    # --- Step 3: Isolation Forest ------------------------------------------
    iforest, scaler = train_isolation_forest(df_clean)

    # --- Step 4: Label + Augment --------------------------------------------
    df_labelled = assign_fault_labels(df_clean)
    df_faults = augment_fault_samples(df_labelled, target_per_class=400)

    # --- Step 5: DataLoaders --------------------------------------------------
    train_loader, val_loader, test_loader = build_dataloaders(df_faults, scaler)

    # --- Step 6: Train Transformer ---------------------------------------------
    n_features = len(FEATURE_COLS)
    model, device = train_model(train_loader, val_loader, n_features)

    # --- Step 7: Evaluate -----------------------------------------------------
    evaluate_model(model, test_loader, device)

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
