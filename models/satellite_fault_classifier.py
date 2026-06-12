"""
=============================================================================
 AI-1 | Satellite Fault Classifier — Full Training Pipeline
 Fault classes: SEU | Software Bug | Firmware Corruption | Command Injection
 Data source  : SatNOGS db.satnogs.org REST API + synthetic augmentation
 Architecture : Isolation Forest (anomaly gate) → Transformer Encoder (classifier)
=============================================================================

QUICK START
-----------
1. Install deps:
   pip install requests pandas numpy scikit-learn torch transformers tqdm

2. Get a free SatNOGS API key:
   → https://db.satnogs.org  → avatar → Settings / API Key

3. Run:
   python satellite_fault_classifier.py --api_key YOUR_KEY_HERE

   or set env var:  SATNOGS_API_KEY=YOUR_KEY python satellite_fault_classifier.py
"""

# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
CONFIG = {
    # SatNOGS targets: ISS (25544), LightSail-2 (44420), NOAA-19 (33591)
    # FUNCUBE-1 (39444), Fox-1A (40967)
    "norad_ids": [25544, 44420, 33591, 39444, 40967],
    "api_base": "https://db.satnogs.org/api",
    "telemetry_per_sat": 500,          # frames per satellite (max 1000 free)

    # Fault label thresholds (tunable per satellite bus)
    "seu_ecc_threshold": 3,            # ECC error count → SEU
    "temp_spike_threshold": 70.0,      # °C spike → hardware anomaly
    "voltage_drop_threshold": 0.15,    # >15 % voltage sag → power fault
    "rssi_floor_dbm": -130,            # below this → comms fault

    # Model
    "seq_len": 16,                     # time-steps per sample window
    "d_model": 64,
    "nhead": 4,
    "num_layers": 2,
    "dropout": 0.1,
    "num_classes": 4,                  # SEU / SW_BUG / FW_CORRUPT / CMD_INJECT

    # Training
    "batch_size": 32,
    "epochs": 30,
    "lr": 1e-3,
    "test_size": 0.2,
    "val_size": 0.1,
    "random_seed": 42,

    # Isolation Forest
    "if_contamination": 0.05,          # expected anomaly rate
    "if_n_estimators": 100,
}

FAULT_LABELS = {
    "SEU": 0,
    "SOFTWARE_BUG": 1,
    "FIRMWARE_CORRUPTION": 2,
    "COMMAND_INJECTION": 3,
}
IDX_TO_LABEL = {v: k for k, v in FAULT_LABELS.items()}


# ─────────────────────────────────────────────────────────────────────────────
# 1.  DATA EXTRACTION — SatNOGS REST API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_satnogs_telemetry(api_key: str, norad_ids: list, limit: int = 500) -> pd.DataFrame:
    """
    Pull raw telemetry frames from db.satnogs.org.

    SatNOGS telemetry frame schema (relevant fields):
        norad_cat_id  : int    — NORAD catalog number
        transmitter   : str    — UUID of the transmitter config
        app_source    : str    — decoder that produced this frame
        timestamp     : str    — ISO-8601 observation time
        frame         : str    — hex-encoded raw frame bytes
        decoded       : dict   — key/value telemetry (satellite-specific)

    The 'decoded' dict is the telemetry payload; structure varies per satellite
    but commonly contains: temperature, voltage, current, rssi, uptime, resets.
    """
    headers = {"Authorization": f"Token {api_key}"}
    all_records = []

    for norad in norad_ids:
        print(f"  Fetching telemetry for NORAD {norad} …")
        url = f"{CONFIG['api_base']}/telemetry/"
        params = {"norad_cat_id": norad, "format": "json", "limit": limit}
        page = 0

        while url and len(all_records) < limit * len(norad_ids):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except requests.exceptions.HTTPError as e:
                print(f"    HTTP error {e}. Skipping NORAD {norad}.")
                break
            except Exception as e:
                print(f"    Error: {e}. Skipping NORAD {norad}.")
                break

            results = data.get("results", data) if isinstance(data, dict) else data
            for item in results:
                decoded = item.get("decoded", {}) or {}
                record = {
                    "norad_cat_id": item.get("norad_cat_id", norad),
                    "timestamp": item.get("timestamp", ""),
                    "app_source": item.get("app_source", ""),
                    # --- telemetry channels (use .get with defaults) ---
                    "temperature":  _safe_float(decoded.get("temperature") or
                                                decoded.get("temp") or
                                                decoded.get("eps_temp", np.nan)),
                    "voltage":      _safe_float(decoded.get("voltage") or
                                                decoded.get("bat_voltage") or
                                                decoded.get("vbat", np.nan)),
                    "current":      _safe_float(decoded.get("current") or
                                                decoded.get("bat_current") or
                                                decoded.get("ibat", np.nan)),
                    "rssi":         _safe_float(decoded.get("rssi") or
                                                decoded.get("signal_rssi", np.nan)),
                    "uptime":       _safe_float(decoded.get("uptime") or
                                                decoded.get("up_time", np.nan)),
                    "reset_count":  _safe_float(decoded.get("reset_count") or
                                                decoded.get("resets", np.nan)),
                    "ecc_errors":   _safe_float(decoded.get("ecc_errors") or
                                                decoded.get("memory_errors", np.nan)),
                    "cpu_load":     _safe_float(decoded.get("cpu_load") or
                                                decoded.get("load", np.nan)),
                }
                all_records.append(record)

            # Pagination
            next_url = data.get("next") if isinstance(data, dict) else None
            url = next_url
            params = {}   # params already encoded in next_url
            page += 1
            if page >= 5:  # cap at 5 pages per satellite
                break
            time.sleep(0.3)  # be polite to the API

        print(f"    Collected {len(all_records)} records so far.")

    df = pd.DataFrame(all_records)
    print(f"\n  Total raw records fetched: {len(df)}")
    return df


def _safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return np.nan


# ─────────────────────────────────────────────────────────────────────────────
# 2.  DATA CLEANING  (pandas)
# ─────────────────────────────────────────────────────────────────────────────

TELEMETRY_COLS = [
    "temperature", "voltage", "current",
    "rssi", "uptime", "reset_count", "ecc_errors", "cpu_load",
]

def clean_telemetry(df: pd.DataFrame) -> pd.DataFrame:
    """
    Full pandas cleaning pipeline for raw SatNOGS telemetry.

    Steps:
        1. Parse & sort timestamps
        2. Drop rows where ALL telemetry channels are NaN
        3. Clip physical outliers (sensor saturation / bad decodes)
        4. Fill remaining NaNs with per-satellite rolling median
        5. Normalise uptime (satellite-relative, in hours)
        6. Remove duplicate (norad, timestamp) pairs
    """
    print("\n[CLEAN] Starting data cleaning …")
    print(f"  Input shape : {df.shape}")

    # 1. Timestamp
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values(["norad_cat_id", "timestamp"]).reset_index(drop=True)

    # 2. Drop rows where all numeric channels are missing
    before = len(df)
    df = df.dropna(subset=TELEMETRY_COLS, how="all")
    print(f"  Dropped {before - len(df)} all-NaN rows")

    # 3. Physical clipping (keeps data in realistic satellite ranges)
    clip_rules = {
        "temperature": (-100.0, 150.0),    # °C  (deep space to sun-facing)
        "voltage":     (0.0,    60.0),     # V
        "current":     (-5.0,   20.0),     # A
        "rssi":        (-160.0, -30.0),    # dBm
        "uptime":      (0.0,    1e9),      # seconds
        "reset_count": (0.0,    1e6),
        "ecc_errors":  (0.0,    1e5),
        "cpu_load":    (0.0,    100.0),    # percent
    }
    for col, (lo, hi) in clip_rules.items():
        if col in df.columns:
            df[col] = df[col].clip(lo, hi)

    # 4. Per-satellite rolling median fill (window = 10 frames)
    for col in TELEMETRY_COLS:
        df[col] = (
            df.groupby("norad_cat_id")[col]
              .transform(lambda s: s.fillna(s.rolling(10, min_periods=1).median()))
        )

    # 5. Residual NaN → global median per column
    for col in TELEMETRY_COLS:
        med = df[col].median()
        df[col] = df[col].fillna(med if not np.isnan(med) else 0.0)

    # 6. Deduplicate
    before = len(df)
    df = df.drop_duplicates(subset=["norad_cat_id", "timestamp"])
    print(f"  Dropped {before - len(df)} duplicate rows")
    print(f"  Output shape: {df.shape}")
    print(f"  NaN remaining:\n{df[TELEMETRY_COLS].isna().sum().to_string()}")

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 3.  SYNTHETIC FAULT LABELLING
#     (For real use, replace with ground-truth anomaly logs from ESA/NASA)
# ─────────────────────────────────────────────────────────────────────────────

def assign_fault_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rule-based heuristic labelling derived from the fault taxonomy
    in the uploaded document.  Rules:

        SEU              : ecc_errors > threshold  OR  sudden reset_count jump
        SOFTWARE_BUG     : cpu_load > 90 %  OR  uptime-gap (reboot) without ECC
        FIRMWARE_CORRUPT : ecc_errors moderate AND voltage stable AND no resets
        COMMAND_INJECT   : rssi above background AND reset spike (uplink anomaly)
        NORMAL           : none of the above (filtered out — only faults kept)

    For supervised training we keep only labelled (fault) rows.
    Unlabelled normal rows feed the Isolation Forest.
    """
    df = df.copy()

    # Derived features
    df["reset_delta"] = (
        df.groupby("norad_cat_id")["reset_count"].diff().fillna(0).clip(0)
    )
    df["uptime_delta"] = (
        df.groupby("norad_cat_id")["uptime"].diff().fillna(0)
    )
    # uptime going negative or very small after being large → reboot
    df["reboot_flag"] = (df["uptime_delta"] < -60).astype(int)

    def label_row(row):
        ecc  = row["ecc_errors"]
        cpu  = row["cpu_load"]
        rdelta = row["reset_delta"]
        reboot = row["reboot_flag"]
        rssi = row["rssi"]
        volt = row["voltage"]

        if ecc >= CONFIG["seu_ecc_threshold"] and rssi < -90:
            return "SEU"
        if (cpu > 90) or (reboot and ecc < CONFIG["seu_ecc_threshold"] and rdelta > 0):
            return "SOFTWARE_BUG"
        if (ecc >= 1) and (ecc < CONFIG["seu_ecc_threshold"]) and (volt > 10) and (rdelta == 0):
            return "FIRMWARE_CORRUPTION"
        if (rssi > CONFIG["rssi_floor_dbm"] + 20) and (rdelta > 0) and (reboot == 0):
            return "COMMAND_INJECTION"
        return "NORMAL"

    df["fault_label"] = df.apply(label_row, axis=1)

    counts = df["fault_label"].value_counts()
    print("\n[LABEL] Fault distribution (before augmentation):")
    print(counts.to_string())
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 4.  SYNTHETIC DATA AUGMENTATION
#     Generates realistic fault samples when real labelled data is sparse
# ─────────────────────────────────────────────────────────────────────────────

def augment_fault_samples(df: pd.DataFrame, target_per_class: int = 300) -> pd.DataFrame:
    """
    Gaussian-noise augmentation around each real fault sample.
    Keeps class balance for training.
    """
    fault_df = df[df["fault_label"] != "NORMAL"].copy()
    augmented_rows = []

    for label in FAULT_LABELS:
        class_df = fault_df[fault_df["fault_label"] == label]
        n_needed = max(0, target_per_class - len(class_df))
        if n_needed == 0:
            continue

        print(f"  Augmenting {label}: {len(class_df)} real → +{n_needed} synthetic")
        if len(class_df) == 0:
            # Generate fully synthetic if no real examples
            class_df = _generate_synthetic_class(label, n=target_per_class)
        
        # Sample with replacement and add Gaussian noise
        samples = class_df.sample(n=n_needed, replace=True, random_state=CONFIG["random_seed"])
        for col in TELEMETRY_COLS:
            std = max(class_df[col].std() * 0.05, 1e-6)
            samples[col] = samples[col] + np.random.normal(0, std, n_needed)
        augmented_rows.append(samples)

    if augmented_rows:
        aug_df = pd.concat([fault_df] + augmented_rows, ignore_index=True)
    else:
        aug_df = fault_df

    print(f"\n[AUG] Post-augmentation fault counts:")
    print(aug_df["fault_label"].value_counts().to_string())
    return aug_df


def _generate_synthetic_class(label: str, n: int) -> pd.DataFrame:
    """Fallback: generate plausible telemetry for a fault class from scratch."""
    rng = np.random.default_rng(CONFIG["random_seed"])
    base = {
        "SEU": dict(temperature=40, voltage=28, current=2.5, rssi=-110,
                    uptime=3600, reset_count=5, ecc_errors=7, cpu_load=45),
        "SOFTWARE_BUG": dict(temperature=55, voltage=27, current=3.0, rssi=-100,
                             uptime=200, reset_count=8, ecc_errors=0, cpu_load=95),
        "FIRMWARE_CORRUPTION": dict(temperature=38, voltage=28.5, current=2.2,
                                    rssi=-105, uptime=7200, reset_count=2,
                                    ecc_errors=2, cpu_load=50),
        "COMMAND_INJECTION": dict(temperature=42, voltage=28, current=2.6,
                                  rssi=-85, uptime=4000, reset_count=3,
                                  ecc_errors=0, cpu_load=60),
    }[label]

    records = {}
    for col, mean in base.items():
        records[col] = rng.normal(mean, mean * 0.05, n)

    df = pd.DataFrame(records)
    df["fault_label"] = label
    df["norad_cat_id"] = 0
    df["timestamp"] = pd.Timestamp.now(tz="UTC")
    df["reset_delta"] = 0
    df["reboot_flag"] = 0
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 5.  ISOLATION FOREST  — Anomaly Gate
# ─────────────────────────────────────────────────────────────────────────────

def train_isolation_forest(df_clean: pd.DataFrame) -> IsolationForest:
    """
    Train Isolation Forest on ALL telemetry (normal + fault).
    At inference time, use this as a fast pre-filter before the Transformer.
    """
    print("\n[IF] Training Isolation Forest anomaly detector …")
    X = df_clean[TELEMETRY_COLS].values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    iforest = IsolationForest(
        n_estimators=CONFIG["if_n_estimators"],
        contamination=CONFIG["if_contamination"],
        random_state=CONFIG["random_seed"],
        n_jobs=-1,
    )
    iforest.fit(X_scaled)
    scores = iforest.decision_function(X_scaled)
    anomaly_pct = (iforest.predict(X_scaled) == -1).mean() * 100
    print(f"  Anomaly rate detected: {anomaly_pct:.1f}%")
    return iforest, scaler


# ─────────────────────────────────────────────────────────────────────────────
# 6.  SLIDING-WINDOW SEQUENCES  →  Dataset
# ─────────────────────────────────────────────────────────────────────────────

class TelemetrySequenceDataset(Dataset):
    """
    Converts tabular telemetry into fixed-length sequences (seq_len × features)
    ready for a Transformer Encoder.

    Each sample:
        X : (seq_len, n_features)  float tensor
        y : int  fault class index
    """

    def __init__(self, X: np.ndarray, y: np.ndarray, seq_len: int = 16):
        self.seq_len = seq_len
        self.samples = []
        self.labels = []

        for i in range(len(X) - seq_len):
            self.samples.append(X[i : i + seq_len])
            self.labels.append(y[i + seq_len - 1])

        self.samples = np.array(self.samples, dtype=np.float32)
        self.labels  = np.array(self.labels,  dtype=np.int64)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        return (
            torch.tensor(self.samples[idx]),   # (seq_len, n_features)
            torch.tensor(self.labels[idx]),
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7.  TRANSFORMER ENCODER CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────

class SatelliteFaultTransformer(nn.Module):
    """
    Architecture:
        Input projection  : linear(n_features → d_model)
        Positional encoding: sinusoidal
        Transformer Encoder: num_layers × (multi-head self-attention + FFN)
        Classifier head   : mean-pool → linear(d_model → num_classes)

    Compatible with PyTorch ≥ 1.9 (no HuggingFace dependency needed for
    inference; if you prefer Trainer from transformers, swap the head).
    """

    def __init__(
        self,
        n_features: int,
        d_model: int   = 64,
        nhead: int     = 4,
        num_layers: int = 2,
        dropout: float  = 0.1,
        num_classes: int = 4,
    ):
        super().__init__()
        self.input_proj = nn.Linear(n_features, d_model)
        self.pos_enc    = PositionalEncoding(d_model, dropout)
        encoder_layer   = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead,
            dim_feedforward=d_model * 4, dropout=dropout,
            batch_first=True,
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.classifier  = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(d_model // 2, num_classes),
        )

    def forward(self, x):
        # x: (batch, seq_len, n_features)
        x = self.input_proj(x)            # → (batch, seq_len, d_model)
        x = self.pos_enc(x)
        x = self.transformer(x)           # → (batch, seq_len, d_model)
        x = x.mean(dim=1)                 # mean pool → (batch, d_model)
        return self.classifier(x)         # → (batch, num_classes)


class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, dropout: float = 0.1, max_len: int = 512):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(0, max_len).unsqueeze(1).float()
        div = torch.exp(torch.arange(0, d_model, 2).float() * (-np.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0))  # (1, max_len, d_model)

    def forward(self, x):
        x = x + self.pe[:, : x.size(1), :]
        return self.dropout(x)


# ─────────────────────────────────────────────────────────────────────────────
# 8.  TRAINING LOOP
# ─────────────────────────────────────────────────────────────────────────────

def build_dataloaders(aug_df: pd.DataFrame, scaler: StandardScaler):
    """Scale features and return train/val/test DataLoaders."""
    X_raw = aug_df[TELEMETRY_COLS].values.astype(np.float32)
    y_raw = aug_df["fault_label"].map(FAULT_LABELS).values.astype(np.int64)

    X_scaled = scaler.transform(X_raw)

    # Split: 70 % train / 10 % val / 20 % test
    X_tv, X_test, y_tv, y_test = train_test_split(
        X_scaled, y_raw, test_size=CONFIG["test_size"],
        stratify=y_raw, random_state=CONFIG["random_seed"],
    )
    X_train, X_val, y_train, y_val = train_test_split(
        X_tv, y_tv, test_size=CONFIG["val_size"] / (1 - CONFIG["test_size"]),
        stratify=y_tv, random_state=CONFIG["random_seed"],
    )

    seq = CONFIG["seq_len"]
    train_ds = TelemetrySequenceDataset(X_train, y_train, seq)
    val_ds   = TelemetrySequenceDataset(X_val,   y_val,   seq)
    test_ds  = TelemetrySequenceDataset(X_test,  y_test,  seq)

    print(f"\n[DATA] Split sizes  →  train: {len(train_ds)}  val: {len(val_ds)}  test: {len(test_ds)}")

    bs = CONFIG["batch_size"]
    train_loader = DataLoader(train_ds, batch_size=bs, shuffle=True,  num_workers=0)
    val_loader   = DataLoader(val_ds,   batch_size=bs, shuffle=False, num_workers=0)
    test_loader  = DataLoader(test_ds,  batch_size=bs, shuffle=False, num_workers=0)
    return train_loader, val_loader, test_loader


def train_model(train_loader, val_loader, n_features: int):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n[TRAIN] Device: {device}")

    model = SatelliteFaultTransformer(
        n_features  = n_features,
        d_model     = CONFIG["d_model"],
        nhead       = CONFIG["nhead"],
        num_layers  = CONFIG["num_layers"],
        dropout     = CONFIG["dropout"],
        num_classes = CONFIG["num_classes"],
    ).to(device)

    criterion = nn.CrossEntropyLoss()
    optimizer = optim.AdamW(model.parameters(), lr=CONFIG["lr"], weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=CONFIG["epochs"])

    best_val_loss = float("inf")
    best_state    = None

    for epoch in range(1, CONFIG["epochs"] + 1):
        # ── Train ──────────────────────────────────────────────────────────
        model.train()
        train_loss = 0.0
        for X_batch, y_batch in tqdm(train_loader, desc=f"Epoch {epoch:02d} train", leave=False):
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            optimizer.zero_grad()
            logits = model(X_batch)
            loss   = criterion(logits, y_batch)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += loss.item() * len(X_batch)

        train_loss /= len(train_loader.dataset)

        # ── Validate ───────────────────────────────────────────────────────
        model.eval()
        val_loss = 0.0
        correct  = 0
        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                X_batch, y_batch = X_batch.to(device), y_batch.to(device)
                logits  = model(X_batch)
                val_loss += criterion(logits, y_batch).item() * len(X_batch)
                correct  += (logits.argmax(1) == y_batch).sum().item()

        val_loss /= len(val_loader.dataset)
        val_acc   = correct / len(val_loader.dataset)
        scheduler.step()

        print(f"  Epoch {epoch:02d}/{CONFIG['epochs']}  "
              f"train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  val_acc={val_acc:.3f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state    = {k: v.cpu().clone() for k, v in model.state_dict().items()}

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


# ─────────────────────────────────────────────────────────────────────────────
# 9.  SAVE ARTEFACTS
# ─────────────────────────────────────────────────────────────────────────────

def save_artifacts(model, iforest, scaler, out_dir="./model_artifacts"):
    import pickle
    os.makedirs(out_dir, exist_ok=True)
    torch.save(model.state_dict(), f"{out_dir}/transformer_encoder.pt")
    with open(f"{out_dir}/isolation_forest.pkl", "wb") as f:
        pickle.dump(iforest, f)
    with open(f"{out_dir}/scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)
    meta = {"config": CONFIG, "fault_labels": FAULT_LABELS, "telemetry_cols": TELEMETRY_COLS}
    with open(f"{out_dir}/meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\n[SAVE] Artifacts saved to {out_dir}/")


# ─────────────────────────────────────────────────────────────────────────────
# 10. INFERENCE HELPER  (use after training)
# ─────────────────────────────────────────────────────────────────────────────

def predict(telemetry_window: np.ndarray,
            model: SatelliteFaultTransformer,
            iforest: IsolationForest,
            scaler: StandardScaler,
            device: torch.device):
    """
    End-to-end inference for one (seq_len × n_features) window.

    Returns:
        anomaly_flag : bool   — True if Isolation Forest flagged the window
        fault_class  : str    — Transformer classification
        confidence   : float  — softmax probability of top class
    """
    X_scaled = scaler.transform(telemetry_window)          # (seq_len, n_features)
    last_row  = X_scaled[-1:, :]                           # use last step for IF

    anomaly_flag = iforest.predict(last_row)[0] == -1

    x_tensor = torch.tensor(X_scaled[np.newaxis], dtype=torch.float32).to(device)
    model.eval()
    with torch.no_grad():
        logits = model(x_tensor)
        probs  = torch.softmax(logits, dim=-1).cpu().numpy()[0]

    top_idx    = int(probs.argmax())
    fault_class = IDX_TO_LABEL[top_idx]
    confidence  = float(probs[top_idx])

    return anomaly_flag, fault_class, confidence


# ─────────────────────────────────────────────────────────────────────────────
# 11. MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Satellite Fault Classifier Training Pipeline")
    parser.add_argument("--api_key",  type=str, default=os.environ.get("SATNOGS_API_KEY", ""))
    parser.add_argument("--demo",     action="store_true",
                        help="Run in demo mode with fully synthetic data (no API key needed)")
    parser.add_argument("--out_dir",  type=str, default="./model_artifacts")
    args = parser.parse_args()

    np.random.seed(CONFIG["random_seed"])
    torch.manual_seed(CONFIG["random_seed"])

    # ── Step 1: Data Extraction ───────────────────────────────────────────
    if args.demo or not args.api_key:
        print("=" * 60)
        print(" DEMO MODE — generating synthetic SatNOGS-shaped data")
        print(" Run with --api_key YOUR_TOKEN for real data")
        print("=" * 60)
        df_raw = _make_demo_df(n=2000)
    else:
        print("=" * 60)
        print(" Fetching real telemetry from db.satnogs.org …")
        print("=" * 60)
        df_raw = fetch_satnogs_telemetry(
            api_key  = args.api_key,
            norad_ids = CONFIG["norad_ids"],
            limit    = CONFIG["telemetry_per_sat"],
        )

    # ── Step 2: Clean ─────────────────────────────────────────────────────
    df_clean = clean_telemetry(df_raw)

    # ── Step 3: Isolation Forest (trained on cleaned normal+anomaly data) ─
    iforest, scaler = train_isolation_forest(df_clean)

    # ── Step 4: Label + Augment ───────────────────────────────────────────
    df_labelled = assign_fault_labels(df_clean)
    df_faults   = augment_fault_samples(df_labelled, target_per_class=400)

    # ── Step 5: DataLoaders ───────────────────────────────────────────────
    train_loader, val_loader, test_loader = build_dataloaders(df_faults, scaler)

    # ── Step 6: Train Transformer ─────────────────────────────────────────
    n_features = len(TELEMETRY_COLS)
    model, device = train_model(train_loader, val_loader, n_features)

    # ── Step 7: Evaluate ──────────────────────────────────────────────────
    evaluate_model(model, test_loader, device)

    # ── Step 8: Save ──────────────────────────────────────────────────────
    save_artifacts(model, iforest, scaler, args.out_dir)

    # ── Step 9: Quick inference demo ─────────────────────────────────────
    print("\n[DEMO] Running one inference example …")
    sample_window = scaler.inverse_transform(
        np.random.randn(CONFIG["seq_len"], n_features).astype(np.float32)
    )
    anomaly, fault, conf = predict(sample_window, model, iforest, scaler, device)
    print(f"  Anomaly detected : {anomaly}")
    print(f"  Fault class      : {fault}")
    print(f"  Confidence       : {conf:.2%}")
    print("\nDone.")


def _make_demo_df(n: int = 2000) -> pd.DataFrame:
    """Generate a synthetic dataframe that mimics SatNOGS telemetry structure."""
    rng = np.random.default_rng(42)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="60s", tz="UTC")
    df = pd.DataFrame({
        "norad_cat_id": rng.choice([25544, 44420, 33591], size=n),
        "timestamp":    timestamps,
        "app_source":   "demo_decoder",
        "temperature":  rng.normal(35, 8, n),
        "voltage":      rng.normal(28, 1, n),
        "current":      rng.normal(2.2, 0.4, n),
        "rssi":         rng.normal(-105, 8, n),
        "uptime":       np.cumsum(rng.integers(50, 70, n)),
        "reset_count":  np.clip(rng.poisson(0.01, n).cumsum(), 0, None),
        "ecc_errors":   rng.poisson(0.1, n),
        "cpu_load":     rng.normal(40, 15, n).clip(0, 100),
    })
    # Inject known fault patterns
    idx_seu = rng.choice(n, size=40, replace=False)
    df.loc[idx_seu, "ecc_errors"] = rng.integers(4, 15, 40)
    idx_sw  = rng.choice(n, size=40, replace=False)
    df.loc[idx_sw, "cpu_load"] = rng.uniform(91, 99, 40)
    return df


if __name__ == "__main__":
    main()
