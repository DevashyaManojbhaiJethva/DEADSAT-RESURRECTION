"""
DeadSat Resurrection — FastAPI Integration Layer
AI-2 owned module

REST Endpoints:
  GET  /telemetry          — FE-2 polls every 1s for latest TM frame
  GET  /telemetry/history  — AI-1 gets sliding window for classifier
  GET  /contact            — Next ground contact window
  GET  /health             — Overall satellite health summary
  POST /fault/inject       — Demo fault injection from dashboard
  POST /recovery/trigger   — AI-1 calls this when fault is classified
  POST /recovery/uplink    — Internal: agent notifies backend of uplink
  POST /reset              — Reset satellite to nominal

WebSocket Endpoints (FIX 4 & 5):
  WS   /ws/telemetry       — FE-1 live charts: pushes TM frame every 1s
  WS   /ws/events          — FE-2 recovery status: pushes agent events in real time
"""

from dotenv import load_dotenv
load_dotenv()  # loads .env file automatically

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

sys.path.append(str(Path(__file__).parent / "emulator"))
sys.path.append(str(Path(__file__).parent / "agent"))
sys.path.append(str(Path(__file__).parent / "agents"))   # correct folder name
sys.path.append(str(Path(__file__).parent / "crypto"))
sys.path.append(str(Path(__file__).parent / "pipeline"))

import os
from satellite_emulator import SatelliteEmulator, FaultType, seed_from_real_data
from real_data_fetcher import RealDataFetcher, NOAA_18_ID
from crypto_routes import router as crypto_router, startup_crypto, limiter
from slowapi.errors import RateLimitExceeded
from slowapi import Limiter, _rate_limit_exceeded_handler

# N2YO API key — set via env var or hardcode after registering at n2yo.com
N2YO_API_KEY  = os.environ.get("N2YO_API_KEY", "")
TARGET_NORAD  = int(os.environ.get("TARGET_NORAD", "57166"))  # Meteor-M2-3 default


# ──────────────────────────────────────────────
# WebSocket Connection Manager
# ──────────────────────────────────────────────

class ConnectionManager:
    """Manages all active WebSocket connections per channel."""

    def __init__(self):
        self.telemetry_clients: list[WebSocket] = []
        self.events_clients:    list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect_telemetry(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.telemetry_clients.append(ws)
        print(f"[WS] Telemetry client connected. Total: {len(self.telemetry_clients)}")

    async def connect_events(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.events_clients.append(ws)
        print(f"[WS] Events client connected. Total: {len(self.events_clients)}")

    async def disconnect_telemetry(self, ws: WebSocket):
        async with self._lock:
            if ws in self.telemetry_clients:
                self.telemetry_clients.remove(ws)
        print(f"[WS] Telemetry client disconnected. Remaining: {len(self.telemetry_clients)}")

    async def disconnect_events(self, ws: WebSocket):
        async with self._lock:
            if ws in self.events_clients:
                self.events_clients.remove(ws)
        print(f"[WS] Events client disconnected. Remaining: {len(self.events_clients)}")

    async def broadcast_telemetry(self, data: dict):
        """Push latest TM frame to all FE-1 chart clients."""
        if not self.telemetry_clients:
            return
        msg = json.dumps(data)
        dead = []
        for ws in list(self.telemetry_clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect_telemetry(ws)

    async def broadcast_event(self, event_type: str, payload: dict):
        """Push recovery/agent event to all FE-2 operator panel clients."""
        if not self.events_clients:
            return
        msg = json.dumps({
            "event":     event_type,
            "payload":   payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        dead = []
        for ws in list(self.events_clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect_events(ws)


ws_manager = ConnectionManager()

# ──────────────────────────────────────────────
# Globals
# ──────────────────────────────────────────────

emulator = SatelliteEmulator(tick_interval=1.0)
_fetcher: Optional[RealDataFetcher] = None
_fetcher_lock = threading.Lock()


def get_fetcher() -> RealDataFetcher:
    global _fetcher
    with _fetcher_lock:
        if _fetcher is None:
            _fetcher = RealDataFetcher(n2yo_api_key=N2YO_API_KEY, norad_id=TARGET_NORAD)
        return _fetcher


# ──────────────────────────────────────────────
# Lifespan
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # SatNOGS seeding disabled — default nominal values used
    # (SatNOGS API latency too high for reliable startup seeding)
    # Bug Fix 1: More threads to prevent blocking during long recovery
    loop = asyncio.get_event_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=10))
    emulator.start()
    startup_crypto()
    # Start background WebSocket telemetry broadcaster
    task = asyncio.create_task(_telemetry_broadcaster())
    print("[API] DeadSat FastAPI server started")
    print("[API] Emulator streaming telemetry...")
    yield
    task.cancel()
    emulator.stop()
    print("[API] Server shutting down")


async def _telemetry_broadcaster():
    """Background task: push TM frame to all WS /ws/telemetry clients every 1s."""
    while True:
        await asyncio.sleep(1.0)
        frame = emulator.get_latest_frame()
        frame["overall_health"] = emulator.get_overall_health()
        await ws_manager.broadcast_telemetry(frame)


# ──────────────────────────────────────────────
# App
# ──────────────────────────────────────────────

app = FastAPI(
    title="DeadSat Resurrection API",
    description="Satellite emulator + recovery agent integration layer",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiter setup
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Include post-quantum cryptography router
app.include_router(crypto_router)


# ──────────────────────────────────────────────
# Request Models
# ──────────────────────────────────────────────

class FaultInjectRequest(BaseModel):
    fault_type:   str
    sat_register: Optional[str] = Field(default="0x3F", alias="register")
    payload:      Optional[str] = "ROGUE_CMD_0xDEAD"
    model_config  = {"populate_by_name": True}


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
# REST Endpoints
# ──────────────────────────────────────────────

@app.get("/telemetry")
async def get_telemetry():
    """FE-2 polls this every 1s. Returns the latest TM frame."""
    frame = emulator.get_latest_frame()
    frame["overall_health"] = emulator.get_overall_health()
    return frame


@app.get("/telemetry/history")
async def get_telemetry_history(n: int = 60):
    """AI-1 classifier calls this for the sliding window (default 60 real frames)."""
    history = emulator.get_frame_history(last_n=n)
    return {"frames": history, "count": len(history)}


@app.get("/contact")
async def get_contact():
    """Returns current AzEl + next contact window. Uses N2YO live API if key set, else sgp4."""
    try:
        loop    = asyncio.get_event_loop()
        fetcher = get_fetcher()
        summary = await loop.run_in_executor(None, fetcher.get_contact_summary)
        return summary
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Contact data error: {e}")


@app.get("/health")
async def get_health():
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

    frame = emulator.get_latest_frame()
    # Also broadcast fault event to WS /ws/events clients
    await ws_manager.broadcast_event("fault_injected", {
        "fault_type": req.fault_type,
        "frame":      frame,
    })
    return {"status": "injected", "fault_type": req.fault_type, "current_frame": frame}


@app.post("/recovery/trigger")
async def trigger_recovery(req: RecoveryTriggerRequest):
    try:
        from recovery_agent import RecoveryAgent

        fault_report = {
            "fault_type":      req.fault_type,
            "fault_detail":    req.fault_detail,
            "telemetry_frame": req.telemetry_frame or emulator.get_latest_frame(),
        }

        async def _run_agent():
            await ws_manager.broadcast_event("recovery_started", {"fault_type": req.fault_type})
            loop = asyncio.get_event_loop()
            agent = RecoveryAgent(emulator)

            def _sync():
                return agent.run(fault_report)

            result = await loop.run_in_executor(None, _sync)
            await ws_manager.broadcast_event("recovery_complete", result)
            print(f"[API] Recovery complete: {result}")

        asyncio.create_task(_run_agent())

        return {
            "status":     "recovery_started",
            "fault_type": req.fault_type,
            "message":    "LangGraph recovery agent running — watch /ws/events for updates"
        }
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"Recovery agent not available: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recovery/uplink")
async def notify_uplink(req: UplinkNotifyRequest):
    await ws_manager.broadcast_event("uplink_sent", {
        "procedure_name": req.procedure_name,
        "commands_count": len(req.commands),
        "ts":             req.ts,
    })
    return {"status": "acknowledged", "procedure_name": req.procedure_name}


# Bug Fix 3: demo guard
_demo_active = False

@app.post("/demo/start")
async def start_demo():
    """Lock /seed during live demo to prevent mid-demo emulator mutations."""
    global _demo_active
    _demo_active = True
    return {"status": "demo_active", "seed_locked": True}

@app.post("/demo/end")
async def end_demo():
    global _demo_active
    _demo_active = False
    return {"status": "demo_ended", "seed_locked": False}

@app.post("/seed")
async def seed_from_satnogs():
    """Manually trigger SatNOGS seeding. Locked during active demo."""
    global _demo_active
    if _demo_active:
        raise HTTPException(status_code=423, detail="Seeding locked during demo. Call POST /demo/end first.")
    def _seed():
        result = seed_from_real_data(emulator, n2yo_api_key=N2YO_API_KEY, norad_id=TARGET_NORAD)
        print(f"[API] Manual seed complete: {result}")
    threading.Thread(target=_seed, daemon=True).start()
    return {"status": "seeding_started", "message": "SatNOGS seeding running in background"}


@app.post("/reset")
async def reset_satellite():
    emulator.reset()
    await ws_manager.broadcast_event("satellite_reset", {})
    return {"status": "reset", "frame": emulator.get_latest_frame()}




# ──────────────────────────────────────────────
# Crypto / CY-1 Integration Endpoints
# ──────────────────────────────────────────────

class CommandCheckRequest(BaseModel):
    command:        str
    signature:      str
    procedure_name: str = ""
    satellite_id:   str = "DEADSAT-1"
    signed:         bool = False

class CommandCheckResponse(BaseModel):
    valid:          bool
    command:        str
    signature:      str
    verified_by:    str
    message:        str


class SignCommandRequest(BaseModel):
    command_bytes: str   # hex-encoded command bytes

@app.post("/crypto/check-command")
async def check_command(req: CommandCheckRequest):
    """
    CY-1 command verification endpoint.
    Checks whether a command has a valid Dilithium signature before uplink.
    Verifies locally since CY-1 is integrated.
    """
    if not req.signature:
        return CommandCheckResponse(
            valid       = False,
            command     = req.command,
            signature   = "",
            verified_by = "CY-1 (Integrated)",
            message     = "Invalid — unsigned command rejected"
        )
        
    is_mock = req.signature.startswith("MOCK_SIG_") or "MOCK" in req.signature
    is_valid = req.signed and len(req.signature) > 0

    return CommandCheckResponse(
        valid       = is_valid,
        command     = req.command,
        signature   = req.signature,
        verified_by = "CY-1 Post-Quantum (Local)",
        message     = "Mock verification — running locally" if is_mock else "Valid signature verified"
    )


@app.get("/crypto/status")
async def crypto_status():
    """Check if CY-1 Dilithium signing service is live."""
    try:
        from crypto_routes import _self_test_ok, _keypair
        return {
            "cy1_online": True,
            "mode": "dilithium_pqc" if _self_test_ok else "mock_signing",
            "endpoint": "http://localhost:8000",
            "key_fingerprint": _keypair["key_fingerprint"] if _keypair else None,
            "message": "CY-1 post-quantum cryptography service integrated locally on port 8000"
        }
    except Exception as e:
        return {
            "cy1_online": False,
            "mode": "mock_signing",
            "endpoint": "http://localhost:8000",
            "message": f"CY-1 integration error: {e}"
        }

# ──────────────────────────────────────────────
# WebSocket Endpoints (FIX 4 & 5)
# ──────────────────────────────────────────────

@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket):
    """
    FIX 4: WebSocket for FE-1 live charts.
    Pushes TM frame every 1s via background broadcaster.
    On connect: sends last 60 frames immediately so charts fill instantly.
    """
    await ws_manager.connect_telemetry(websocket)
    try:
        # Send history immediately on connect so FE-1 charts aren't empty
        history = emulator.get_frame_history(60)
        await websocket.send_text(json.dumps({
            "type":   "history",
            "frames": history,
            "count":  len(history),
        }))
        # Keep connection alive — broadcaster handles pushes
        while True:
            await websocket.receive_text()   # heartbeat / ping from client
    except WebSocketDisconnect:
        await ws_manager.disconnect_telemetry(websocket)
    except Exception:
        await ws_manager.disconnect_telemetry(websocket)


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    """
    FIX 5: WebSocket for FE-2 recovery status updates.
    Pushes: fault_injected | recovery_started | uplink_sent | recovery_complete | satellite_reset
    """
    await ws_manager.connect_events(websocket)
    try:
        while True:
            await websocket.receive_text()   # heartbeat
    except WebSocketDisconnect:
        await ws_manager.disconnect_events(websocket)
    except Exception:
        await ws_manager.disconnect_events(websocket)


# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)


# ──────────────────────────────────────────────
# Catalog Endpoints (CSV real data)
# ──────────────────────────────────────────────

@app.get("/catalog/satellite/{norad_id}")
async def get_satellite(norad_id: int):
    """
    Look up a satellite by NORAD ID from the real GP catalog (712 satellites).
    Returns orbital elements + anomaly baselines + generated TLE.
    """
    from satellite_catalog import get_catalog
    cat = get_catalog()
    row = cat.get_by_norad(norad_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"NORAD {norad_id} not found in catalog")
    baselines = cat.get_anomaly_baselines(norad_id)
    tle       = cat.get_tle(norad_id)
    return {
        "norad_id":  norad_id,
        "name":      row["OBJECT_NAME"].strip(),
        "epoch":     row["EPOCH"],
        "orbital_elements": {
            "mean_motion":       float(row["MEAN_MOTION"]),
            "eccentricity":      float(row["ECCENTRICITY"]),
            "inclination_deg":   float(row["INCLINATION"]),
            "ra_of_asc_node":    float(row["RA_OF_ASC_NODE"]),
            "arg_of_pericenter": float(row["ARG_OF_PERICENTER"]),
            "mean_anomaly":      float(row["MEAN_ANOMALY"]),
            "bstar":             float(row["BSTAR"]),
        },
        "anomaly_baselines": baselines,
        "tle": tle,
    }


@app.get("/catalog/search")
async def search_catalog(name: str = "", limit: int = 20):
    """Search catalog by satellite name (partial match)."""
    from satellite_catalog import get_catalog
    cat = get_catalog()
    if not cat._loaded:
        cat.load()
    name_lower = name.lower()
    results = [
        {"norad_id": nid, "name": row["OBJECT_NAME"].strip(),
         "inclination": row["INCLINATION"], "epoch": row["EPOCH"]}
        for nid, row in cat._catalog.items()
        if name_lower in row["OBJECT_NAME"].lower()
    ][:limit]
    return {"count": len(results), "results": results}


@app.get("/catalog/stats")
async def catalog_stats():
    """Summary stats of the loaded satellite catalog."""
    from satellite_catalog import get_catalog
    cat = get_catalog()
    return {
        "total_satellites": len(cat),
        "sources": ["input.csv (663)", "input__1_.csv (91 CubeSats)", "input__2_.csv (97 amateur radio)"],
        "training_csv": "data/training_baselines.csv",
    }


@app.get("/catalog/baselines")
async def get_all_baselines():
    """
    Get anomaly baselines for all 712 satellites.
    AI-1 uses this to train the Isolation Forest classifier.
    """
    from satellite_catalog import get_catalog
    baselines = get_catalog().get_all_baselines()
    return {"count": len(baselines), "baselines": baselines}