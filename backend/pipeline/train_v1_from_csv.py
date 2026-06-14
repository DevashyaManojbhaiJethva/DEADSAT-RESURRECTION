"""
=============================================================================
 AI-1 | Satellite Fault Classifier v1 — CSV-based Training
 Loads a pre-labelled HK-telemetry CSV (e.g. fake_telemetry_v1.csv, produced
 by generate_fake_telemetry.py) and trains:
     - Isolation Forest (real-time anomaly gate)
     - Transformer Encoder (SEU / SOFTWARE_BUG / FIRMWARE_CORRUPTION /
       COMMAND_INJECTION classifier)

 This is the CSV-driven counterpart to satellite_fault_classifier.py
 (which fetches live SatNOGS telemetry). Use this version when you already
 have a labelled telemetry CSV on disk.

 Expected CSV columns:
     norad_cat_id, object_name, timestamp,
     temperature, voltage, current, rssi, uptime,
     reset_count, ecc_errors, cpu_load,
     fault_label   (SEU | SOFTWARE_BUG | FIRMWARE_CORRUPTION |
                     COMMAND_INJECTION | NORMAL)
=============================================================================

QUICK START (Windows path example)
-----------------------------------
python train_v1_from_csv.py ^
    --csv "C:\\Users\\satkb\\OneDrive\\Desktop\\projectssss\\fake_telemetry_v1.csv" ^
    --out_dir "C:\\Users\\satkb\\OneDrive\\Desktop\\projectssss\\model_artifacts_v1"

(Linux/macOS path example)
---------------------------
python train_v1_from_csv.py \
    --csv ./fake_telemetry_v1.csv \
    --out_dir ./model_artifacts_v1
"""

import os
import json
import argparse
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
# CONFIG
# ---------------------------------------------------------------------------
CONFIG = {
    "seq_len": 16,
    "d_model": 64,
    "nhead": 4,
    "num_layers": 2,
    "dropout": 0.1,
    "num_classes": 4,

    "batch_size": 32,
    "epochs": 30,
    "lr": 1e-3,
    "test_size": 0.2,
    "val_size": 0.1,
    "random_seed": 42,

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

TELEMETRY_COLS = [
    "temperature", "voltage", "current",
    "rssi", "uptime", "reset_count", "ecc_errors", "cpu_load",
]


# ---------------------------------------------------------------------------
# 1. LOAD + CLEAN
# ---------------------------------------------------------------------------

def load_and_clean(csv_path: str) -> pd.DataFrame:
    print(f"[LOAD] Reading {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"  Raw shape: {df.shape}")

    required = TELEMETRY_COLS + ["fault_label", "norad_cat_id", "timestamp"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    # Parse timestamp & sort per satellite
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values(["norad_cat_id", "timestamp"]).reset_index(drop=True)

    # Coerce numeric columns
    for col in TELEMETRY_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Physical clipping (same ranges as original v1 pipeline)
    clip_rules = {
        "temperature": (-100.0, 150.0),
        "voltage":     (0.0,    60.0),
        "current":     (-5.0,   20.0),
        "rssi":        (-160.0, -30.0),
        "uptime":      (0.0,    1e9),
        "reset_count": (0.0,    1e6),
        "ecc_errors":  (0.0,    1e5),
        "cpu_load":    (0.0,    100.0),
    }
    for col, (lo, hi) in clip_rules.items():
        df[col] = df[col].clip(lo, hi)

    # Fill residual NaNs per-satellite rolling median, then global median
    for col in TELEMETRY_COLS:
        df[col] = (
            df.groupby("norad_cat_id")[col]
              .transform(lambda s: s.fillna(s.rolling(10, min_periods=1).median()))
        )
        med = df[col].median()
        df[col] = df[col].fillna(med if not np.isnan(med) else 0.0)

    # Drop duplicates
    before = len(df)
    df = df.drop_duplicates(subset=["norad_cat_id", "timestamp"])
    print(f"  Dropped {before - len(df)} duplicate rows")

    # Keep only known fault labels (drop anything unexpected)
    valid_labels = set(FAULT_LABELS.keys()) | {"NORMAL"}
    bad = ~df["fault_label"].isin(valid_labels)
    if bad.sum() > 0:
        print(f"  Dropping {bad.sum()} rows with unrecognized fault_label values")
        df = df[~bad]

    print(f"  Clean shape: {df.shape}")
    print("\n[LABEL] Distribution:")
    print(df["fault_label"].value_counts().to_string())
    return df


# ---------------------------------------------------------------------------
# 2. AUGMENT FAULT CLASSES (balance for the Transformer)
# ---------------------------------------------------------------------------

def augment_fault_samples(df: pd.DataFrame, target_per_class: int = 400) -> pd.DataFrame:
    fault_df = df[df["fault_label"] != "NORMAL"].copy()
    augmented_rows = []

    for label in FAULT_LABELS:
        class_df = fault_df[fault_df["fault_label"] == label]
        n_needed = max(0, target_per_class - len(class_df))
        if n_needed == 0 or len(class_df) == 0:
            if len(class_df) == 0:
                print(f"  WARNING: no real samples for {label}, skipping augmentation for this class")
            continue

        print(f"  Augmenting {label}: {len(class_df)} real -> +{n_needed} synthetic")
        samples = class_df.sample(n=n_needed, replace=True, random_state=CONFIG["random_seed"])
        for col in TELEMETRY_COLS:
            std = max(class_df[col].std() * 0.05, 1e-6)
            samples[col] = samples[col] + np.random.normal(0, std, n_needed)
        augmented_rows.append(samples)

    if augmented_rows:
        aug_df = pd.concat([fault_df] + augmented_rows, ignore_index=True)
    else:
        aug_df = fault_df

    print("\n[AUG] Post-augmentation fault counts:")
    print(aug_df["fault_label"].value_counts().to_string())
    return aug_df


# ---------------------------------------------------------------------------
# 3. ISOLATION FOREST
# ---------------------------------------------------------------------------

def train_isolation_forest(df_clean: pd.DataFrame):
    print("\n[IF] Training Isolation Forest anomaly detector ...")
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
    anomaly_pct = (iforest.predict(X_scaled) == -1).mean() * 100
    print(f"  Anomaly rate detected: {anomaly_pct:.1f}%")
    return iforest, scaler


# ---------------------------------------------------------------------------
# 4. SEQUENCE DATASET
# ---------------------------------------------------------------------------

class TelemetrySequenceDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray, seq_len: int = 16):
        self.samples, self.labels = [], []
        for i in range(len(X) - seq_len):
            self.samples.append(X[i:i + seq_len])
            self.labels.append(y[i + seq_len - 1])
        self.samples = np.array(self.samples, dtype=np.float32)
        self.labels = np.array(self.labels, dtype=np.int64)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        return torch.tensor(self.samples[idx]), torch.tensor(self.labels[idx])


# ---------------------------------------------------------------------------
# 5. TRANSFORMER ENCODER
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
# 6. DATALOADERS + TRAIN + EVAL
# ---------------------------------------------------------------------------

def build_dataloaders(aug_df: pd.DataFrame, scaler: StandardScaler):
    X_raw = aug_df[TELEMETRY_COLS].values.astype(np.float32)
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
    train_ds = TelemetrySequenceDataset(X_train, y_train, seq)
    val_ds = TelemetrySequenceDataset(X_val, y_val, seq)
    test_ds = TelemetrySequenceDataset(X_test, y_test, seq)

    print(f"\n[DATA] Split sizes -> train: {len(train_ds)}  val: {len(val_ds)}  test: {len(test_ds)}")

    bs = CONFIG["batch_size"]
    return (
        DataLoader(train_ds, batch_size=bs, shuffle=True),
        DataLoader(val_ds, batch_size=bs, shuffle=False),
        DataLoader(test_ds, batch_size=bs, shuffle=False),
    )


def train_model(train_loader, val_loader, n_features):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\n[TRAIN] Device: {device}")

    model = SatelliteFaultTransformer(
        n_features=n_features,
        d_model=CONFIG["d_model"], nhead=CONFIG["nhead"],
        num_layers=CONFIG["num_layers"], dropout=CONFIG["dropout"],
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
        val_loss, correct = 0.0, 0
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
            preds = model(X_batch.to(device)).argmax(1).cpu().numpy()
            all_preds.extend(preds)
            all_labels.extend(y_batch.numpy())

    target_names = [IDX_TO_LABEL[i] for i in range(CONFIG["num_classes"])]
    print("\n[EVAL] Classification Report:")
    print(classification_report(all_labels, all_preds, target_names=target_names, zero_division=0))
    print("[EVAL] Confusion Matrix:")
    print(confusion_matrix(all_labels, all_preds))


# ---------------------------------------------------------------------------
# 7. SAVE
# ---------------------------------------------------------------------------

def save_artifacts(model, iforest, scaler, out_dir):
    import pickle
    os.makedirs(out_dir, exist_ok=True)
    torch.save(model.state_dict(), os.path.join(out_dir, "transformer_encoder.pt"))
    with open(os.path.join(out_dir, "isolation_forest.pkl"), "wb") as f:
        pickle.dump(iforest, f)
    with open(os.path.join(out_dir, "scaler.pkl"), "wb") as f:
        pickle.dump(scaler, f)
    meta = {"config": CONFIG, "fault_labels": FAULT_LABELS, "telemetry_cols": TELEMETRY_COLS}
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\n[SAVE] Artifacts saved to {out_dir}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Train v1 fault classifier from a labelled CSV")
    parser.add_argument("--csv", type=str, required=True,
                         help=r'Path to fake_telemetry_v1.csv, e.g. '
                              r'"C:\Users\satkb\OneDrive\Desktop\projectssss\fake_telemetry_v1.csv"')
    parser.add_argument("--out_dir", type=str, default="./model_artifacts_v1",
                         help="Where to save trained model artifacts")
    args = parser.parse_args()

    np.random.seed(CONFIG["random_seed"])
    torch.manual_seed(CONFIG["random_seed"])

    df_clean = load_and_clean(args.csv)
    iforest, scaler = train_isolation_forest(df_clean)
    df_aug = augment_fault_samples(df_clean, target_per_class=400)

    train_loader, val_loader, test_loader = build_dataloaders(df_aug, scaler)
    model, device = train_model(train_loader, val_loader, n_features=len(TELEMETRY_COLS))
    evaluate_model(model, test_loader, device)
    save_artifacts(model, iforest, scaler, args.out_dir)
    print("\nDone.")


if __name__ == "__main__":
    main()
