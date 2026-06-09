"""
DeadSat Resurrection — FastAPI Integration Layer
AI-2 owned module (integration surface for FE-2 and AI-1)

Endpoints:
  GET  /telemetry          — FE-2 polls every 1s for latest TM frame
  GET  /telemetry/history  — AI-1 gets sliding window for classifier
  GET  /contact            — Next ground contact window
  GET  /health             — Overall satellite health summary
  POST /fault/inject       — Demo fault injection from dashboard
  POST /recovery/trigger   — AI-1 calls this when fault is classified
  POST /recovery/uplink    — Internal: agent notifies backend of uplink
  POST /reset              — Reset satellite to nominal
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
import threading
import sys
from pathlib import Path

# Add emulator + agent to path
sys.path.append(str(Path(__file__).parent / "emulator"))
sys.path.append(str(Path(__file__).parent / "agent"))

from satellite_emulator import SatelliteEmulator, FaultType
from contact_calculator import ContactCalculator


# ──────────────────────────────────────────────
# Lifespan (replaces deprecated @app.on_event)
# ──────────────────────────────────────────────

# Singleton emulator — created before lifespan so endpoints can reference it
emulator = SatelliteEmulator(tick_interval=1.0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    emulator.start()
    print("[API] DeadSat FastAPI server started")
    print("[API] Emulator streaming telemetry...")
    yield
    # Shutdown
    emulator.stop()
    print("[API] Emulator stopped — server shutting down")


# ──────────────────────────────────────────────
# App init
# ──────────────────────────────────────────────

app = FastAPI(
    title="DeadSat Resurrection API",
    description="Satellite emulator + recovery agent integration layer",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # React dashboard on any port
    allow_methods=["*"],
    allow_headers=["*"],
)

# Contact calculator (lazy loaded)
_calc: Optional[ContactCalculator] = None
_calc_lock = threading.Lock()


def get_calculator() -> ContactCalculator:
    global _calc
    with _calc_lock:
        if _calc is None:
            _calc = ContactCalculator()
            _calc.load_tle()
        return _calc


# ──────────────────────────────────────────────
# Request / Response models
# ──────────────────────────────────────────────

class FaultInjectRequest(BaseModel):
    fault_type:   str
    sat_register: Optional[str] = Field(default="0x3F", alias="register")
    payload:      Optional[str] = "ROGUE_CMD_0xDEAD"

    model_config = {"populate_by_name": True}


class RecoveryTriggerRequest(BaseModel):
    fault_type:      str
    fault_detail:    dict = {}
    telemetry_frame: dict = {}


class UplinkNotifyRequest(BaseModel):
    procedure_name: str
    commands:       list = []
    fault_type:     str  = ""
    ts:             str  = ""


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@app.get("/telemetry")
async def get_telemetry():
    """FE-2 polls this every 1s. Returns the latest TM frame."""
    frame = emulator.get_latest_frame()
    frame["overall_health"] = emulator.get_overall_health()
    return frame


@app.get("/telemetry/history")
async def get_telemetry_history(n: int = 60):
    """AI-1 classifier calls this for the sliding window (default 60 frames)."""
    history = emulator.get_frame_history(last_n=n)
    return {"frames": history, "count": len(history)}


@app.get("/contact")
async def get_contact():
    """Returns current AzEl position + next ground contact window over Ahmedabad."""
    try:
        calc    = get_calculator()
        summary = calc.get_contact_summary()
        return summary
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Contact calc error: {e}")


@app.get("/health")
async def get_health():
    """Quick health summary for status badges on FE-1 React dashboard."""
    frame = emulator.get_latest_frame()
    return {
        "overall":        emulator.get_overall_health(),
        "obc_status":     frame.get("obc_status"),
        "adcs_status":    frame.get("adcs_status"),
        "power_status":   frame.get("power_status"),
        "comms_status":   frame.get("comms_status"),
        "fault_injected": frame.get("fault_injected"),
        "battery_pct":    frame.get("battery_pct"),
        "frame_id":       frame.get("frame_id"),
    }


@app.post("/fault/inject")
async def inject_fault(req: FaultInjectRequest):
    """Demo endpoint — inject a fault from the React dashboard."""
    ft = req.fault_type.lower()
    if ft == "seu":
        emulator.inject_SEU(register=req.sat_register or "0x3F")
    elif ft == "software_bug":
        emulator.inject_software_bug()
    elif ft == "firmware_corruption":
        emulator.inject_firmware_corruption()
    elif ft == "command_injection":
        emulator.inject_command(payload=req.payload or "ROGUE_CMD_0xDEAD")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown fault type: {req.fault_type}")

    return {
        "status":        "injected",
        "fault_type":    req.fault_type,
        "current_frame": emulator.get_latest_frame()
    }


@app.post("/recovery/trigger")
async def trigger_recovery(req: RecoveryTriggerRequest):
    """
    AI-1 calls this once a fault is classified.
    Kicks off the LangGraph recovery agent in a background thread (non-blocking).
    """
    try:
        from recovery_agent import RecoveryAgent

        fault_report = {
            "fault_type":      req.fault_type,
            "fault_detail":    req.fault_detail,
            "telemetry_frame": req.telemetry_frame or emulator.get_latest_frame(),
        }

        def _run_agent():
            agent  = RecoveryAgent(emulator)
            result = agent.run(fault_report)
            print(f"[API] Recovery complete: {result}")

        threading.Thread(target=_run_agent, daemon=True).start()

        return {
            "status":     "recovery_started",
            "fault_type": req.fault_type,
            "message":    "LangGraph recovery agent running in background"
        }
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"Recovery agent not available: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recovery/uplink")
async def notify_uplink(req: UplinkNotifyRequest):
    """Internal — agent notifies backend that commands were uplinked."""
    return {
        "status":         "acknowledged",
        "procedure_name": req.procedure_name,
        "commands_count": len(req.commands),
        "ts":             req.ts,
    }


@app.post("/reset")
async def reset_satellite():
    """Reset satellite to nominal state (useful between demo runs)."""
    emulator.reset()
    return {
        "status": "reset",
        "frame":  emulator.get_latest_frame()
    }


# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)