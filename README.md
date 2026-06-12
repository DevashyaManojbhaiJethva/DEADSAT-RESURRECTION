## Repository Structure

```text
DEADSAT-RESURRECTION/
│
├── agents/
│   ├── recovery_agent.py
│   └── procedure_library.json
│
├── emulator/
│   ├── satellite_emulator.py
│   └── contact_calculator.py
│
├── models/
│   ├── satellite_fault_classifier.py
│   ├── satellite_fault_classifier_V2.py
│   └── satellite_fault_classifier_tle.py
│
├── data/
│   ├── training_baselines.csv
│   ├── input.csv
│   ├── input_1.csv
│   └── input_2.csv
│
├── docs/
│   ├── deadsat_postman_collection.json
│   ├── Satellite_Fault_Recovery_Design.docx
│   └── CHANGES_V1_TO_V2.md
│
├── model_artifacts/
│   ├── transformer_encoder.pt
│   ├── isolation_forest.pkl
│   ├── scaler.pkl
│   └── meta.json
│
├── main.py
├── real_data_fetcher.py
├── requirements.txt
├── pyrightconfig.json
├── .env.example
├── .gitignore
└── README.md
```

---

# Satellite Fault Classifier — TLE / Orbital Element Edition

## Why Version 2 Was Built

The original classifier was designed for SatNOGS telemetry streams containing:

- Temperature
- Voltage
- Current
- RSSI
- CPU load
- ECC memory errors

The real datasets used during development were CelesTrak/NORAD orbital element datasets:

```text
OBJECT_NAME
OBJECT_ID
EPOCH
MEAN_MOTION
ECCENTRICITY
INCLINATION
RA_OF_ASC_NODE
ARG_OF_PERICENTER
MEAN_ANOMALY
EPHEMERIS_TYPE
CLASSIFICATION_TYPE
NORAD_CAT_ID
ELEMENT_SET_NO
REV_AT_EPOCH
BSTAR
MEAN_MOTION_DOT
MEAN_MOTION_DDOT
```

Because these datasets contain orbital parameters rather than onboard telemetry, the fault-classification pipeline was redesigned around orbital dynamics.

---

## Feature Engineering

| Previous Telemetry Feature | New Orbital Feature |
| -------------------------- | ------------------- |
| temperature                | removed             |
| voltage                    | removed             |
| current                    | removed             |
| rssi                       | removed             |
| ecc_errors                 | ECCENTRICITY        |
| uptime                     | REV_AT_EPOCH        |
| reset_count                | REV_DELTA           |
| —                          | TLE_AGE_HOURS       |
| —                          | BSTAR               |
| —                          | MEAN_MOTION_DOT     |
| —                          | MEAN_MOTION_DDOT    |

Derived features:

- ECC_DELTA
- REV_DELTA
- TLE_AGE_HOURS
- BSTAR anomaly score
- MEAN_MOTION anomaly score

---

## Fault Classification Logic

| Fault Type          | Trigger                                             |
| ------------------- | --------------------------------------------------- |
| SEU                 | ECCENTRICITY jump between epochs (ECC_DELTA > 0.01) |
| SOFTWARE_BUG        | REV_DELTA <= 0                                      |
| FIRMWARE_CORRUPTION | BSTAR or MEAN_MOTION_DOT exceeds thresholds or >3σ  |
| COMMAND_INJECTION   | TLE_AGE_HOURS > 72                                  |
| NORMAL              | No anomaly detected                                 |

---

## AI Architecture

### Stage 1 — Isolation Forest

Used as an anomaly gate.

```text
Input Window
      │
      ▼
Isolation Forest
      │
      ├── Normal
      └── Suspicious
```

### Stage 2 — Transformer Encoder

```text
8-step orbital sequence
          │
          ▼
Transformer Encoder
          │
          ▼
Fault Class
```

Classes:

- NORMAL
- SEU
- SOFTWARE_BUG
- FIRMWARE_CORRUPTION
- COMMAND_INJECTION

---

## Model Artifacts

```text
model_artifacts/
├── transformer_encoder.pt
├── isolation_forest.pkl
├── scaler.pkl
└── meta.json
```

---

## Live Data Sources

### Offline Mode

Uses:

```text
input.csv
input_1.csv
input_2.csv
```

### Live Mode

Uses N2YO TLE API:

```text
https://api.n2yo.com/rest/v1/satellite/tle/{NORAD_ID}
```

Tracked satellites:

- ISS (25544)
- AMSAT OSCAR-7 (7530)
- CUTE-1 / CO-55 (27844)
- AO-10 (14129)
- NOAA-19 (33591)

---

## Performance

Training dataset:

```text
849 original orbital records
≈1860 augmented sequences
```

Observed validation performance:

```text
Accuracy: 99–100%
```

Note:

This accuracy reflects rule-based bootstrapped labels used during development. Production deployment should use mission-verified fault logs and real anomaly events.

---

## Inference Output

Example:

```python
anomaly, fault_class, confidence = predict(...)
```

Output:

```text
True
SEU
0.98
```

Meaning:

```text
Anomaly Detected: YES
Fault Type: SEU
Confidence: 98%
```
