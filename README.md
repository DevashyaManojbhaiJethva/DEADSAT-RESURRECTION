# 🛰️ DeadSat Resurrection

> **FAR AWAY 2026 Hackathon** — Space & Aerospace + Agentic Systems + Cybersecurity tracks

An autonomous cyber-forensic satellite recovery system. ISRO takes **48–96 hours** to manually recover a bricked satellite. DeadSat Resurrection does it in **under 90 seconds**.

---

## The Problem

Satellites fail. SEU bit flips, firmware corruption, software crashes, rogue command injections — any of these can brick a satellite mid-orbit. Current recovery is entirely manual: engineers analyse telemetry, write recovery commands, wait for a ground contact window, uplink, and hope. It takes days.

## The Solution

A fully autonomous 4-stage pipeline:

```
Anomaly Detection → Fault Classification → Recovery Generation → Signed Uplink
     (AI-1)              (AI-1)                 (AI-2)              (CY-1 + AI-2)
```

1. **Isolation Forest + Transformer** monitors live telemetry for anomalies
2. **Transformer Encoder** classifies the fault type (SEU / software bug / firmware corruption / command injection)
3. **LangGraph agentic pipeline** selects the correct recovery procedure, generates a command sequence, and automatically falls back to the next procedure if the first fails
4. **CRYSTALS-Dilithium** (NIST 2024 PQC standard) signs every command before uplink — verified by the satellite before execution, logged in a tamper-evident hash-chain ledger

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Raspberry Pi 4 #1                  │
│                                                     │
│  ┌──────────┐    ┌──────────┐    ┌───────────────┐  │
│  │ Satellite│───▶│ FastAPI  │───▶│  LangGraph    │  │
│  │ Emulator │    │  :8000   │    │  Recovery     │  │
│  │ (AI-2)   │◀───│          │◀───│  Agent (AI-2) │  │
│  └──────────┘    └──────────┘    └───────────────┘  │
│       │               │                  │          │
│       │          ┌────▼────┐    ┌────────▼──────┐   │
│       │          │Anomaly  │    │  Dilithium    │   │
│       │          │Detector │    │  Signing      │   │
│       │          │+ Class. │    │  Service CY-1 │   │
│       │          │ (AI-1)  │    │   :8001       │   │
│       │          └─────────┘    └───────────────┘   │
│       │                                             │
│  ┌────▼──────────────────────────────────────────┐  │
│  │         React Dashboard (FE-1)  :3000         │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│                  Raspberry Pi 4 #2                  │
│         RTL-SDR — live RF on 137 MHz NOAA band      │
└─────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
DEADSAT-RESURRECTION/
├── satellite_emulator.py     # AI-2 — Satellite state machine (OBC/ADCS/Power/Comms)
├── contact_calculator.py     # AI-2 — TLE-based ground contact over Ahmedabad (sgp4)
├── recovery_agent.py         # AI-2 — LangGraph 9-node recovery pipeline
├── procedure_library.json    # AI-2 — All 4 fault → recovery procedure mappings
├── main.py                   # AI-2 — FastAPI server (all integration endpoints)
├── requirements.txt
└── README.md
```

---

## Quick Start

```bash
pip install -r requirements.txt
python main.py                # FastAPI server starts on :8000
```

API docs available at `http://localhost:8000/docs`

---

## API Endpoints

| Method | Path                      | Who calls it          | Description                               |
| ------ | ------------------------- | --------------------- | ----------------------------------------- |
| `GET`  | `/telemetry`              | FE-2 (polls every 1s) | Latest TM frame — all subsystems          |
| `GET`  | `/telemetry/history?n=60` | AI-1                  | Sliding window for classifier             |
| `GET`  | `/contact`                | FE-1                  | Next ground contact window over Ahmedabad |
| `GET`  | `/health`                 | FE-1                  | Quick health summary for dashboard badges |
| `POST` | `/fault/inject`           | FE-1 dashboard        | Inject fault for demo                     |
| `POST` | `/recovery/trigger`       | AI-1                  | Kick off LangGraph recovery agent         |
| `POST` | `/reset`                  | FE-1                  | Reset satellite to nominal between demos  |

---

## Fault Types

| Fault                 | Subsystem Affected                   | Primary Recovery Procedure |
| --------------------- | ------------------------------------ | -------------------------- |
| `SEU`                 | ADCS (bit flip in attitude register) | `ADCS_MEMORY_SCRUB_v2`     |
| `software_bug`        | OBC (crash loop, CPU runaway)        | `OBC_SOFT_REBOOT_v1`       |
| `firmware_corruption` | All subsystems degrading             | `FIRMWARE_ROLLBACK_v1`     |
| `command_injection`   | Comms (rogue unsigned command)       | `LOCKDOWN_REGEN_v1`        |

Each fault has a primary procedure and a fallback — the LangGraph agent tries them in order automatically.

---

## Telemetry Frame Schema

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

---

## LangGraph Recovery Pipeline

```
START
  │
  ▼
load_procedures ──▶ select_procedure ──▶ generate_commands ──▶ request_signing
                          ▲                                           │
                          │                                    ┌──────▼──────┐
                          │                                    │ CY-1 signs  │
                          │                                    │ (or mock)   │
                          │                                    └──────┬──────┘
                          │                                           │
                       fallback ◀── monitor_recovery ◀── uplink_commands ◀── schedule_uplink
                          │               │
                          │        ┌──────▼──────┐
                          │        │   SUCCESS   │──▶ report_success ──▶ END
                          │        └─────────────┘
                          │
                    (next priority)
                          │
                    (exhausted) ──▶ report_failure ──▶ END
```

---

## 90-Second Demo Flow

1. Dashboard loads → `/telemetry` polling starts — all green
2. Judge clicks **Inject SEU** → `POST /fault/inject {"fault_type": "SEU"}`
3. ADCS rate spikes on dashboard — Isolation Forest flags anomaly
4. Transformer classifies → `SEU` confirmed
5. AI-1 calls `POST /recovery/trigger`
6. LangGraph agent: load → select `ADCS_MEMORY_SCRUB_v2` → generate 5 commands → Dilithium sign → check contact window → uplink
7. Emulator applies recovery → ADCS nominal
8. Dashboard goes green ✓
9. **Total elapsed: < 90 seconds**

---

## Team

| Role     | Responsibilities                                                                 |
| -------- | -------------------------------------------------------------------------------- |
| AI-1     | Anomaly detection (Isolation Forest), fault classification (Transformer Encoder) |
| **AI-2** | **Satellite emulator, LangGraph recovery agent, FastAPI server**                 |
| FE-1     | React dashboard, real-time telemetry visualisation                               |
| FE-2     | Frontend integration, API wiring                                                 |
| CY-1     | CRYSTALS-Dilithium signing service, hash-chain ledger                            |

---

## Hardware

- **Pi 4 #1** — FastAPI + emulator + classifier + LangGraph agent + signing service (4GB RAM)
- **Pi 4 #2** — RTL-SDR receiver, live satellite signals on 137 MHz NOAA band
- **Demo screens** — React dashboard on projector | terminal logs on Pi #1 | RF spectrum on Pi #2
