# DeadSat Resurrection — AI-2 Module

## Files owned by AI-2

```
deadsat/
├── emulator/
│   ├── satellite_emulator.py     # Satellite state machine + fault injection
│   └── contact_calculator.py     # TLE-based ground contact (sgp4 + CelesTrak)
├── agent/
│   ├── recovery_agent.py         # LangGraph 9-step recovery pipeline
│   └── procedure_library.json    # All 4 fault → recovery procedure mappings
├── main.py                       # FastAPI server — all endpoints
└── requirements.txt
```

---

## Quick start

```bash
pip install -r requirements.txt
python main.py                     # starts on :8000
```

---

## Endpoints (FE-2 polls these)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/telemetry` | Latest TM frame (all subsystems) |
| GET | `/telemetry/history?n=60` | Sliding window for AI-1 classifier |
| GET | `/contact` | Next ground contact window over Ahmedabad |
| GET | `/health` | Quick health summary for dashboard badges |
| POST | `/fault/inject` | Inject fault from React dashboard |
| POST | `/recovery/trigger` | AI-1 calls this when fault is classified |
| POST | `/reset` | Reset satellite to nominal between demos |

---

## Fault injection (demo script)

```python
# SEU
POST /fault/inject
{ "fault_type": "SEU", "register": "0x3F" }

# Software bug
{ "fault_type": "software_bug" }

# Firmware corruption
{ "fault_type": "firmware_corruption" }

# Command injection
{ "fault_type": "command_injection", "payload": "ROGUE_CMD_0xDEAD" }
```

---

## AI-1 integration

AI-1 sends a POST to `/recovery/trigger`:

```json
{
  "fault_type": "SEU",
  "fault_detail": { "register": "0x3F", "bit_flipped": 3 },
  "telemetry_frame": { ... }
}
```

Agent runs in background thread. Recovery completes in < 90s.

---

## CY-1 signing integration

Recovery agent POSTs to `http://localhost:8001/sign`.
If CY-1 is not yet running, mock signing is used automatically (dev mode).

```json
POST /sign
{
  "commands": [ ... ],
  "procedure_name": "ADCS_MEMORY_SCRUB_v2",
  "fault_type": "SEU"
}
```

Expected response:
```json
{ "signed_commands": [ { "cmd": "...", "signed": true, "signature": "..." } ] }
```

---

## TM Frame schema (canonical — share with all team members)

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

## 90-second demo flow

1. Dashboard loads → `/telemetry` polling starts (green status)
2. Judge presses "Inject SEU" → `POST /fault/inject`
3. ADCS rate spikes on dashboard — Transformer detects anomaly (AI-1)
4. AI-1 classifies fault → `POST /recovery/trigger`
5. LangGraph agent starts: load → select → generate → sign → schedule → uplink
6. Emulator receives `apply_recovery("ADCS_MEMORY_SCRUB_v2")`
7. Dashboard goes green — recovery confirmed
8. Total time: < 90 seconds
