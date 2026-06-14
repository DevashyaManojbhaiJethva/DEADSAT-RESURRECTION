<div align="center">

# 🛰️ DeadSat Resurrection

### Autonomous Satellite Fault Detection, Classification, Recovery & Secure Command Uplink Platform

<img src="https://img.shields.io/badge/STATUS-ALL%20SYSTEMS%20NOMINAL-success?style=for-the-badge&logo=satellite" alt="status"/>

<h3>⚡ Recovering Satellites in Seconds, Not Days. ⚡</h3>

<p><em>Space & Aerospace × Agentic Systems × Cybersecurity</em> — Built for <strong>FAR AWAY 2026 Hackathon</strong></p>

<p>
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/LangGraph-1.2.4-1C3C3C?style=flat-square" alt="LangGraph"/>
  <img src="https://img.shields.io/badge/React-Dashboard-61DAFB?style=flat-square&logo=react&logoColor=black" alt="React"/>
  <img src="https://img.shields.io/badge/PyTorch-EE4C2C?style=flat-square&logo=pytorch&logoColor=white" alt="PyTorch"/>
  <img src="https://img.shields.io/badge/Security-CRYSTALS--Dilithium-critical?style=flat-square&logo=shieldsdotio&logoColor=white" alt="PQC"/>
  <img src="https://img.shields.io/badge/Raspberry%20Pi-4-A22846?style=flat-square&logo=raspberrypi&logoColor=white" alt="RPi"/>
</p>

<p>
  <img src="https://img.shields.io/badge/Recovery%20Time-%3C90s-blueviolet?style=flat-square" alt="recovery time"/>
  <img src="https://img.shields.io/badge/API%20Tests-72%2F78%20passing-brightgreen?style=flat-square" alt="tests"/>
  <img src="https://img.shields.io/badge/Satellite%20Catalog-712%20objects-orange?style=flat-square" alt="catalog"/>
  <img src="https://img.shields.io/badge/PRs-welcome-ff69b4?style=flat-square" alt="PRs welcome"/>
</p>

</div>

<br>

```
   ╔══════════════════════════════════════════════════════════════════════╗
   ║   Live Telemetry → Anomaly Detection → Fault Classification →        ║
   ║   Recovery Planning → Secure Uplink → ✅ Verified Nominal            ║
   ╚══════════════════════════════════════════════════════════════════════╝
```

<br>

## 📚 Table of Contents

- [📡 The Problem](#-the-problem)
- [🚀 The Solution](#-the-solution)
- [✨ Key Features](#-key-features)
- [🏗️ System Architecture](#️-system-architecture)
- [📁 Repository Structure](#-repository-structure)
- [⚡ Quick Start](#-quick-start)
- [🔌 API Endpoints](#-api-endpoints)
- [🛠️ Fault Types & Recovery Procedures](#️-fault-types--recovery-procedures)
- [🧠 LangGraph Recovery Pipeline](#-langgraph-recovery-pipeline)
- [🌐 Real Data Integration](#-real-data-integration)
- [📦 Telemetry Frame Schema](#-telemetry-frame-schema)
- [✅ API Test Results](#-api-test-results)
- [🎬 90-Second Demo Flow](#-90-second-demo-flow)
- [🛰️ Satellite Catalog](#️-satellite-catalog)
- [🤖 AI-1: Fault Detection & Classification](#-ai-1-fault-detection--classification)
- [🔧 Hardware](#-hardware)
- [🧰 Technology Stack](#-technology-stack)
- [👥 Team](#-team)
- [🔭 Future Work](#-future-work)

---

## 📡 The Problem

> ISRO and other space agencies take **48–96 hours** to manually recover a bricked satellite. Engineers must analyse telemetry, identify faults, prepare recovery procedures, wait for ground contact windows, and then uplink commands — all by hand.

## 🚀 The Solution

<div align="center">

### DeadSat Resurrection reduces satellite recovery to **under 90 seconds** — fully autonomously.

</div>

---

## ✨ Key Features

<div align="center">

| | | |
|:---:|:---:|:---:|
| 🧠 **AI-Powered Diagnosis**<br>Isolation Forest + Transformer classify faults from live telemetry & TLE drift | 🤖 **Autonomous Recovery**<br>9-node LangGraph agent plans, signs, schedules & executes recovery — no human in the loop | 🔐 **Post-Quantum Security**<br>Every uplink command signed with CRYSTALS-Dilithium (NIST PQC 2024) |
| 🌍 **Real Orbital Data**<br>Live feeds from N2YO, SatNOGS & CelesTrak with a 712-satellite local catalog fallback | 📡 **Live Dashboard**<br>React + WebSocket telemetry streaming and recovery event visualization | ⏱️ **90-Second Resolution**<br>From fault injection to verified-nominal in under 30 demo seconds |

</div>

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Raspberry Pi 4 #1                  │
│                                                     │
│  ┌──────────┐    ┌──────────┐    ┌───────────────┐  │
│  │ Satellite│───▶│ FastAPI  │───▶│  LangGraph   │  │
│  │ Emulator │    │  :8000   │    │ Recovery Agent│  │
│  │ (AI-2)   │◀───│          │◀───│    (AI-2)    │  │
│  └──────────┘    └──────────┘    └───────────────┘  │
│       │               │                  │          │
│       │          ┌────▼────┐    ┌────────▼──────┐   │
│       │          │Isolation│    │ Dilithium PQC │   │
│       │          │Forest + │    │ Command Sign  │   │
│       │          │Transformer   │ Service (CY-1)│   │
│       │          │  (AI-1) │    │    :8001      │   │
│       │          └─────────┘    └───────────────┘   │
│  ┌────▼──────────────────────────────────────────┐  │
│  │         React Dashboard (FE-1)  :3000         │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│                  Raspberry Pi 4 #2                  │
│         RTL-SDR — Live RF on 137 MHz NOAA band      │
└─────────────────────────────────────────────────────┘
```

---

## 📁 Repository Structure

<details>
<summary><strong>Click to expand the full directory tree</strong> 🌳</summary>

```
DEADSAT-RESURRECTION/
├── agents/
│   ├── recovery_agent.py              # LangGraph 9-node recovery pipeline
│   └── procedure_library.json        # 4 fault types × 2 procedures + min_confidence
├── emulator/
│   ├── satellite_emulator.py         # OBC/ADCS/Power/Comms state machine
│   ├── contact_calculator.py         # sgp4 orbital mechanics, AOS/LOS over Ahmedabad
│   └── real_data_fetcher.py          # N2YO live API + SatNOGS + CelesTrak + fallback
├── models/
│   ├── satellite_fault_classifier.py
│   ├── satellite_fault_classifier_V2.py
│   └── satellite_fault_classifier_tle.py
├── data/
│   ├── input.csv                     # 663 satellites — general catalog
│   ├── input__1_.csv                 # 91 CubeSats
│   ├── input__2_.csv                 # 97 amateur radio satellites
│   └── training_baselines.csv        # 712-row AI-1 training export
├── docs/
│   ├── deadsat_postman_collection.json
│   ├── Satellite_Fault_Recovery_Design.docx
│   └── CHANGES_V1_TO_V2.md
├── main.py                           # FastAPI server — 13 REST + 2 WebSocket endpoints
├── real_data_fetcher.py
├── satellite_catalog.py              # 712-satellite GP catalog, TLE builder
├── requirements.txt
└── README.md
```

</details>

---

## ⚡ Quick Start

**1. Clone & install**

```bash
git clone https://github.com/DevashyaManojbhaiJethva/DEADSAT-RESURRECTION.git
cd DEADSAT-RESURRECTION
pip install -r requirements.txt
```

**2. Configure environment** — create a `.env` file:

```env
N2YO_API_KEY=your_key        # https://www.n2yo.com/login/
SATNOGS_TOKEN=your_token     # https://db.satnogs.org/accounts/login/
TARGET_NORAD=28654
```

**3. Launch the platform**

```bash
python main.py
```

> 🔍 Explore the live Swagger UI at **http://localhost:8000/docs**

---

## 🔌 API Endpoints

<div align="center">

| Method | Endpoint | Description |
|:---:|---|---|
| `GET` | `/health` | All 4 subsystem statuses |
| `GET` | `/telemetry` | Live TM frame (streamed every 1s) |
| `GET` | `/telemetry/history` | Sliding window — last N frames for AI-1 |
| `GET` | `/contact` | Ground contact window over Ahmedabad (N2YO live) |
| `POST` | `/fault/inject` | Inject fault for demo |
| `POST` | `/recovery/trigger` | Kick off LangGraph recovery agent |
| `POST` | `/reset` | Reset satellite to nominal |
| `GET` | `/catalog/satellite/{norad_id}` | Orbital elements + TLE + anomaly baselines |
| `GET` | `/catalog/search?name=ISS` | Search 712-satellite catalog |
| `GET` | `/catalog/stats` | Catalog summary |
| `GET` | `/catalog/baselines` | All 712 baselines for AI-1 training |
| `POST` | `/demo/start` | Lock seed endpoint during live demo |
| `POST` | `/demo/end` | Unlock after demo |
| `WS` | `/ws/telemetry` | Live TM push every 1s to FE-1 charts |
| `WS` | `/ws/events` | Recovery status events to FE-2 operator panel |

</div>

---

## 🛠️ Fault Types & Recovery Procedures

| Fault | Subsystem | Primary Procedure | Fallback | Min Confidence |
|:---|:---|:---|:---|:---:|
| 🟥 `SEU` | ADCS (bit flip) | `ADCS_MEMORY_SCRUB_v2` | `OBC_SOFT_REBOOT_v1` | 0.75 |
| 🟧 `software_bug` | OBC (crash loop) | `OBC_SOFT_REBOOT_v1` | `OBC_HARD_RESET_v1` | 0.70 |
| 🟪 `firmware_corruption` | All subsystems | `FIRMWARE_ROLLBACK_v1` | `SAFE_MODE_HOLD` | **0.90** |
| 🟦 `command_injection` | Comms (rogue cmd) | `LOCKDOWN_REGEN_v1` | `COMMS_HARD_RESET_v1` | 0.80 |

---

## 🧠 LangGraph Recovery Pipeline

```
START → Load Procedures → Select Procedure → Generate Commands
         → Request Signing (CY-1 Dilithium) → Schedule Uplink
         → Execute Recovery → Monitor Recovery
         → [SUCCESS → report_success → END]
         → [FAILURE → fallback → select next procedure → ...]
         → [Exhausted → report_failure → END]
```

| 🔹 | Detail |
|---|---|
| **Nodes** | 9, with conditional fallback edges |
| **Logging** | Every run persisted to `recovery_logs/` as JSON |
| **Context** | Catalog baselines injected into the reasoning trace |
| **Safety Gate** | `min_confidence` gates irreversible procedures (`FIRMWARE_ROLLBACK` requires ≥ 0.90) |

---

## 🌐 Real Data Integration

| Source | What | Auth |
|---|---|:---:|
| 🛰️ **N2YO API** | Live AzEl, radio pass predictions over Ahmedabad | Free API key |
| 📻 **SatNOGS DB** | Real decoded telemetry frames from ground stations | Free account token |
| 🌌 **CelesTrak** | TLE fallback | None |
| 📂 **Local CSV catalog** | 712 satellites, GP data → TLE builder, AI-1 baselines | None |

<div align="center">

**TLE priority chain:**  `N2YO` → `SatNOGS` → `CSV Catalog` → `CelesTrak` → `hardcoded fallback`

</div>

---

## 📦 Telemetry Frame Schema

<details>
<summary><strong>Click to expand the full TM frame JSON</strong> 📡</summary>

```json
{
  "timestamp": 1718000000,
  "frame_id": 42,
  "obc_register": "0x3F",
  "obc_temp_c": 47.2,
  "obc_error_count": 0,
  "obc_cpu_pct": 18.5,
  "obc_memory_pct": 34.2,
  "obc_status": "nominal",
  "adcs_rate_deg_s": 0.003,
  "adcs_quaternion": [0.1, 0.2, 0.3, 0.9],
  "adcs_wheel_rpm": 4800.0,
  "adcs_pointing_err_deg": 0.001,
  "adcs_status": "nominal",
  "power_w": 82.4,
  "battery_pct": 91.2,
  "bus_voltage_v": 28.1,
  "power_charging": true,
  "power_status": "nominal",
  "comms_uplink": true,
  "comms_downlink": true,
  "signal_strength_dbm": -78.3,
  "comms_status": "nominal",
  "fault_injected": null,
  "fault_detail": {}
}
```

</details>

---

## ✅ API Test Results

<div align="center">

### Postman Runner — 78 assertions across 13 endpoints

<img src="https://img.shields.io/badge/Pass%20Rate-92.3%25-brightgreen?style=for-the-badge" alt="pass rate"/>

`██████████████████████████████████████████░░░` **72 / 78 passed**

| ✅ Passed | ❌ Failed | ⏱️ Avg Response | 🕐 Total Duration |
|:---:|:---:|:---:|:---:|
| **72 / 78** | 6 / 78 | 224ms | 4.7s |

</div>

<details>
<summary><strong>Click to expand per-endpoint results</strong> 📋</summary>

| Endpoint | Status |
|---|:---:|
| `GET /health` | ✅ PASS |
| `GET /telemetry` | ✅ PASS |
| `GET /telemetry/history` | ✅ PASS |
| `GET /contact` (N2YO live) | ✅ PASS |
| `POST /fault/inject` (all 4 types) | ✅ PASS |
| `POST /recovery/trigger` | ✅ PASS |
| `GET /health` (post-recovery) | ✅ PASS |
| `POST /reset` | ✅ PASS |
| `GET /catalog/stats` (712 satellites) | ✅ PASS |
| `GET /catalog/satellite/28654` | ✅ PASS |
| `GET /catalog/search` | ✅ PASS |
| `POST /demo/start` | ✅ PASS |
| `POST /demo/end` | ✅ PASS |

</details>

---

## 🎬 90-Second Demo Flow

<div align="center">

```
T+00s ●─── Dashboard loads → /health all green, N2YO shows live position
T+05s ●─── Judge clicks "Inject SEU" → POST /fault/inject
T+10s ●─── ADCS rate spikes 0.003 → 7.04 deg/s, pointing error grows
T+15s ●─── AI-1 Isolation Forest flags anomaly → Transformer confirms SEU
T+20s ●─── AI-1 calls POST /recovery/trigger  (confidence = 0.95)
T+22s ●─── Node 1: load procedures + fetch NOAA-18 baselines
T+23s ●─── Node 2: select ADCS_MEMORY_SCRUB_v2  (0.95 ≥ 0.75 ✓)
T+24s ●─── Node 3: generate 5 commands [ADCS_SAFE_HOLD, OBC_SCRUB_REGISTER, ...]
T+25s ●─── Node 4: CRYSTALS-Dilithium signing by CY-1
T+26s ●─── Node 5: contact window checked (10s resolution)
T+27s ●─── Node 6: 5 signed commands uplinked to satellite
T+28s ●─── Node 7: recovery verified — overall_health = nominal
T+30s ●─── 🟢 Dashboard goes GREEN — log saved to recovery_logs/
```

</div>

---

## 🛰️ Satellite Catalog

<div align="center">

| 🛰️ 712 satellites | 📡 3 Space-Track GP datasets | 🧮 No network needed |
|:---:|:---:|:---:|
| Loaded into local catalog | TLE lines generated from raw GP elements | Fully offline TLE builder |

</div>

- `training_baselines.csv` — 712 rows exported for AI-1's Isolation Forest
- Catalog baselines included in **every** recovery log reasoning trace

---

## 🤖 AI-1: Fault Detection & Classification

### 🔍 Anomaly Detection
Isolation Forest continuously monitors telemetry and orbital data.

### 🧬 Transformer Fault Classifier V2 (TLE-Based)
Redesigned around orbital mechanics to match available datasets.

**Input features:** `MEAN_MOTION` · `ECCENTRICITY` · `INCLINATION` · `RA_OF_ASC_NODE` · `ARG_OF_PERICENTER` · `MEAN_ANOMALY` · `BSTAR` · `MEAN_MOTION_DOT` · `REV_AT_EPOCH`

**Derived features:** `ECC_DELTA` · `REV_DELTA` · `TLE_AGE_HOURS` · `BSTAR_ANOMALY` · `MEAN_MOTION_ANOMALY`

| Fault | Detection Logic |
|---|---|
| `SEU` | `ECC_DELTA > 0.01` |
| `SOFTWARE_BUG` | `REV_DELTA ≤ 0` |
| `FIRMWARE_CORRUPTION` | Abnormal `BSTAR` or `MEAN_MOTION_DOT` |
| `COMMAND_INJECTION` | TLE age > 72h |

---

## 🔧 Hardware

<div align="center">

| 🖥️ Pi 4 #1 | 📻 Pi 4 #2 | 🎥 Demo Setup |
|:---:|:---:|:---:|
| FastAPI + emulator + classifier + LangGraph agent + signing service (4GB RAM) | RTL-SDR receiver, live satellite signals on 137 MHz NOAA band | React dashboard on projector · terminal logs on Pi #1 · RF spectrum on Pi #2 |

</div>

---

## 🧰 Technology Stack

| Layer | Stack |
|---|---|
| 🐍 **Backend** | Python 3.11, FastAPI, uvicorn, LangGraph 1.2.4, langchain-core 1.4.2 |
| 🧠 **ML** | PyTorch, scikit-learn, Isolation Forest, Transformer Encoder |
| 🌍 **Orbital** | sgp4 2.23, N2YO API, SatNOGS DB, CelesTrak |
| 🔐 **Security** | CRYSTALS-Dilithium (liboqs), NIST PQC 2024 standard |
| 💻 **Frontend** | React, WebSocket, REST |
| 📊 **Data** | Space-Track GP format, 712 satellites, 1860+ training sequences |

---

## 👥 Team

<div align="center">

| Role | Responsibilities |
|:---:|---|
| 🧠 **AI-1** | Anomaly detection (Isolation Forest), fault classification (Transformer V2, TLE-based) |
| 🤖 **AI-2** | Satellite emulator, LangGraph recovery agent, FastAPI server, real data integration |
| 🎨 **FE-1** | React dashboard, real-time telemetry visualisation |
| 🔗 **FE-2** | Frontend integration, API wiring |
| 🔐 **CY-1** | CRYSTALS-Dilithium signing service, hash-chain ledger |

</div>

---

## 🔭 Future Work

- [ ] Real CubeSat deployment
- [ ] CCSDS packet support
- [ ] SDR telemetry decoding
- [ ] Multi-satellite constellation support
- [ ] Reinforcement-learning recovery optimisation
- [ ] Autonomous mission planning

---

<div align="center">

### 🛰️ FAR AWAY 2026 — Recovering Satellites in Seconds, Not Days. 🛰️

**Space × AI × Cybersecurity** 🚀🔐

<sub>Made with ❤️ and a lot of coffee ☕ by Team DeadSat</sub>

[⬆ Back to top](#️-deadsat-resurrection)

</div>
