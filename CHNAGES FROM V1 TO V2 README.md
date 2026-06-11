# Satellite Fault Classifier — TLE / Orbital-Element Edition

## What changed from the first version (and why)

The first script was built assuming **SatNOGS telemetry frames**
(temperature, voltage, current, RSSI, ECC memory errors, etc.).

The CSVs you actually uploaded (`input.csv`, `input__1_.csv`,
`input__2_.csv`) are **orbital element sets** in CelesTrak/TLE format —
659 + 92 + 98 satellites, with columns:

```
OBJECT_NAME, OBJECT_ID, EPOCH, MEAN_MOTION, ECCENTRICITY, INCLINATION,
RA_OF_ASC_NODE, ARG_OF_PERICENTER, MEAN_ANOMALY, EPHEMERIS_TYPE,
CLASSIFICATION_TYPE, NORAD_CAT_ID, ELEMENT_SET_NO, REV_AT_EPOCH,
BSTAR, MEAN_MOTION_DOT, MEAN_MOTION_DDOT
```

This is **orbit propagation data**, not on-board telemetry. So the
feature set, the data source, and the fault-detection logic were
rebuilt around it.

### Key parameter / concept changes

| Old concept (telemetry) | New concept (orbital elements) | Notes |
|---|---|---|
| `temperature`, `voltage`, `current`, `rssi`, `cpu_load` | **removed** | Not present in TLE data |
| `ecc_errors` (ECC = Error-Correcting-Code memory faults) | `ECCENTRICITY` (orbital eccentricity, 0–1) | **"ECC" now means orbital eccentricity**, a completely different quantity. Tracked via `ecc_delta` (epoch-to-epoch jump) instead of a memory-error counter. |
| `uptime`, `reset_count` | `REV_AT_EPOCH`, `REV_DELTA` | Revolution counter from the TLE; a stuck/negative delta is the new "software bug" signal. |
| (none) | `TLE_AGE_HOURS` | New derived feature = hours since `EPOCH`. A stale TLE (>72h old by default) is the new proxy for a comms/ground-segment ("command injection") issue. |
| (none) | `BSTAR`, `MEAN_MOTION_DOT`, `MEAN_MOTION_DDOT` | Drag/decay terms. Abnormal values (z-score > 3 or beyond fixed thresholds) now indicate **firmware corruption** (corrupted decay coefficients in the TLE/firmware). |
| SatNOGS DB API | **N2YO API** (`api.n2yo.com/rest/v1/satellite/tle/{NORAD_ID}`) | Live TLE refresh. The script parses the raw two-line element directly (no extra `sgp4` dependency). |
| `seq_len = 16` | `seq_len = 8` | Orbital element sets update roughly every few hours to a day, so windows are shorter than per-minute telemetry. |

### Fault label logic (rebuilt)

| Fault class | New trigger condition |
|---|---|
| **SEU** | Sudden jump in `ECCENTRICITY` between consecutive epochs (`ecc_delta > 0.01`) — a one-off bit-flip in the on-board state vector that gets corrected next epoch. |
| **SOFTWARE_BUG** | `REV_DELTA <= 0` — the revolution counter is stuck or rolled back while the orbit itself looks normal → propagation/software bug, not a physical event. |
| **FIRMWARE_CORRUPTION** | `BSTAR` or `MEAN_MOTION_DOT` outside fixed thresholds **or** > 3σ from that satellite's own history → corrupted drag/decay coefficients. |
| **COMMAND_INJECTION** | `TLE_AGE_HOURS > 72` — stale ephemeris, consistent with a disrupted uplink/ground-segment anomaly. |
| **NORMAL** | None of the above — used only to train the Isolation Forest. |

All thresholds live at the top of the script in `CONFIG` and are easy
to tune for your own satellites.

---

## Files produced

- `satellite_fault_classifier_tle.py` — the full pipeline
- `model_artifacts/transformer_encoder.pt` — trained Transformer weights
- `model_artifacts/isolation_forest.pkl` — anomaly-gate model
- `model_artifacts/scaler.pkl` — StandardScaler for the 11 features
- `model_artifacts/meta.json` — config + label map + feature list

---

## How to run

### 1. Install dependencies
```bash
pip install requests pandas numpy scikit-learn torch transformers tqdm
```

### 2. (Optional) Get an N2YO API key for live TLE refresh
- Go to https://www.n2yo.com → Login → Profile → generate **API Key**
- Each API call must be authorized with this license key, generated from your N2YO profile page after registering an account.

### 3. Run with your CSVs (real + augmented data, this is what was tested)
```bash
python satellite_fault_classifier_tle.py \
    --csv input.csv input__1_.csv input__2_.csv \
    --n2yo_api_key YOUR_N2YO_KEY
```

### 4. Fully offline demo (no key, no internet)
```bash
python satellite_fault_classifier_tle.py \
    --csv input.csv input__1_.csv input__2_.csv \
    --demo
```

This was run during development on your three CSVs (849 combined rows
→ cleaned → augmented to ~1860 sequences) and reached **~99–100%
validation/test accuracy** across all four fault classes — note this
high accuracy reflects the rule-based labels used to bootstrap
training; for production use, replace `assign_fault_labels()` with
ground-truth fault logs from mission operations.

---

## N2YO live data flow

```
N2YO /tle/{NORAD_ID} endpoint
   → raw 2-line TLE string
   → parse_tle_lines() converts to the same 17-column schema as your CSVs
   → merged with CSV data → cleaned → fed into the same pipeline
```

Default NORAD IDs tracked (edit `CONFIG["norad_ids"]`):
ISS (25544), AMSAT-OSCAR 7 (7530), CUTE-1/CO-55 (27844),
AO-10 (14129), NOAA-19 (33591) — chosen because they appear in
your uploaded CSVs.

---

## Inference

```python
import torch, pickle
from satellite_fault_classifier_tle import (
    SatelliteFaultTransformer, predict, FEATURE_COLS, CONFIG
)

scaler  = pickle.load(open("model_artifacts/scaler.pkl", "rb"))
iforest = pickle.load(open("model_artifacts/isolation_forest.pkl", "rb"))

model = SatelliteFaultTransformer(n_features=len(FEATURE_COLS),
                                   d_model=CONFIG["d_model"],
                                   nhead=CONFIG["nhead"],
                                   num_layers=CONFIG["num_layers"],
                                   num_classes=CONFIG["num_classes"])
model.load_state_dict(torch.load("model_artifacts/transformer_encoder.pt"))

# window: numpy array, shape (8, 11), raw (unscaled) FEATURE_COLS values
anomaly, fault_class, confidence = predict(window, model, iforest, scaler,
                                            torch.device("cpu"))
print(anomaly, fault_class, confidence)
```
