"""
DEPRECATED: This file is NO LONGER the canonical backend.

The authoritative FastAPI backend is now at the repository root: main.py

This file is retained for reference only. It contains an older implementation
with known issues:
- Wildcard CORS (*) instead of controlled origins
- Missing WebSocket authentication 
- Older security model

Migration path:
- Use root main.py for all deployments
- Update any imports from 'backend.main' to 'main'
- Update any references to backend/main.py in documentation

This file will be removed in a future release.
"""

from dotenv import load_dotenv
load_dotenv()  # loads .env file automatically

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
import threading
import asyncio
import httpx          # WIRING: RF bridge to Pi #2 + crypto proxying
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
# WIRING: deployment configuration (hosts, ports, CORS, security toggles).
import config as cfg
from satellite_emulator import SatelliteEmulator, FaultType, seed_from_real_data
from real_data_fetcher import RealDataFetcher, NOAA_18_ID
from crypto_routes import router as crypto_router, startup_crypto, limiter, rotate_keypair


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """
    WIRING: shared-secret guard for state-changing endpoints.
    Disabled when DEADSAT_API_KEY is unset so bench setups keep working.
    """
    if not cfg.API_KEY:
        return
    if x_api_key != cfg.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


async def _ws_authenticate(websocket: WebSocket) -> bool:
    """
    Shared-secret guard for WebSocket endpoints.

    SECURITY: require_api_key() is a FastAPI dependency and therefore only ever
    ran on REST routes. /ws/telemetry and /ws/events accepted ANY connection,
    so with DEADSAT_API_KEY set anyone on the LAN could still stream live
    telemetry and watch every recovery event — the confidentiality half of the
    key was doing nothing.

    It also made a key mismatch invisible in exactly the wrong way: telemetry
    kept flowing, so the dashboard showed LIVE TM and looked healthy, while
    every control silently returned 401. The operator saw a working system that
    refused to do anything.

    The key may be supplied either as a query parameter

        ws://host:8000/ws/telemetry?api_key=SECRET

    or as the first message on the socket, which avoids putting the secret in
    server access logs:

        {"api_key": "SECRET"}

    Returns True if the socket is authenticated and already accepted. On
    failure the socket is closed with 1008 (policy violation) and False is
    returned. When DEADSAT_API_KEY is unset the check is skipped entirely, so
    a bench setup behaves exactly as before.
    """
    # Accept first so we can read the opening frame and send a close reason; a
    # WebSocket rejected before the handshake gives the client no detail.
    # This is the ONLY place a socket is accepted — ConnectionManager.connect_*
    # no longer calls accept(), which would raise on a second call.
    await websocket.accept()

    if not cfg.API_KEY:
        return True

    supplied = websocket.query_params.get("api_key")

    if not supplied:
        try:
            first = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            supplied = json.loads(first).get("api_key")
        except asyncio.TimeoutError:
            print("[WS] Auth timeout — no key within 5s")
        except Exception:
            pass

    if supplied != cfg.API_KEY:
        print("[WS] REJECTED — invalid or missing api_key")
        await websocket.close(code=1008, reason="Invalid or missing api_key")
        return False
    return True

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
        # NOTE: the socket is accepted by _ws_authenticate() before this is
        # called — accepting twice raises. Auth must happen before a client is
        # registered for broadcasts, or an unauthenticated socket would receive
        # frames in the window before it was closed.
        async with self._lock:
            self.telemetry_clients.append(ws)
        print(f"[WS] Telemetry client connected. Total: {len(self.telemetry_clients)}")

    async def connect_events(self, ws: WebSocket):
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
    elif ft == "battery_failure":
        emulator.inject_battery_failure()
    elif ft == "adcs_failure":
        emulator.inject_adcs_failure()
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


# SignCommandRequest removed with the hand-rolled /crypto/sign handler.
# crypto_routes.SignRequest is the schema now — see crypto/crypto_routes.py.

@app.post("/crypto/check-command")
async def check_command(req: CommandCheckRequest):
    """
    Legacy pre-uplink sanity check. NOT a signature verification.

    SECURITY: this endpoint used to rubber-stamp. Its verdict was

        is_valid = req.signed and len(req.signature) > 0

    i.e. any non-empty string, with `signed` set by the caller, was reported
    `valid: true` and "Valid signature verified" — no cryptography involved.
    That is precisely the failure mode the verification gate exists to prevent,
    sitting on an endpoint named check-command.

    It cannot do better with what it is given: CommandCheckRequest carries a
    single opaque `signature` field and no nonce or TTL, so the hybrid
    verifier (which needs BOTH signatures plus valid_until) cannot be called.
    Rather than guess, it now reports honestly and points at the real endpoint.

    Real verification: POST /crypto/verify (crypto_router), which checks the
    Ed25519 signature, the ML-DSA-65 signature and the TTL, and refuses to
    certify anything produced by the development shim.
    """
    return CommandCheckResponse(
        valid       = False,
        command     = req.command,
        signature   = req.signature,
        verified_by = "none",
        message     = (
            "NOT VERIFIED. /crypto/check-command performs no cryptographic "
            "check — it has neither the second signature nor the TTL. Use "
            "POST /crypto/verify with {command_hex, ml_dsa_sig_hex, "
            "ed25519_sig_hex, valid_until} for real hybrid verification."
        ),
    )


@app.get("/crypto/status")
async def crypto_status():
    """
    State of the crypto layer, which now runs IN-PROCESS via crypto_router.

    WIRING: this used to probe cfg.CY1_BASE over HTTP and report
    `mode: mock_signing` whenever that separate process was down — which was
    always, because nothing in the repository could start it. The frontend's
    Security Console read that and told the operator signing was mocked even
    when the real primitives were available.

    It now reports what is actually true of this process: whether the hybrid
    implementation is loaded, and whether it is running on real liboqs/PyNaCl
    or the development shim. Key names are unchanged so SecurityConsole.tsx
    keeps working.
    """
    try:
        import mock_oqs_nacl
        mocked = mock_oqs_nacl.is_mock_active()
        backend = mock_oqs_nacl.mock_detail()
    except Exception as exc:            # crypto layer failed to import at all
        return {
            "cy1_online": False,
            "mode": "unavailable",
            "endpoint": cfg.API_BASE + "/crypto",
            "in_process": False,
            "mock_crypto": None,
            "message": f"crypto layer not loaded: {exc}",
        }

    allow_mock = cfg.ALLOW_MOCK_SIGNING
    return {
        # "online" here means: can this API produce a verifiable signature?
        "cy1_online":  not mocked,
        "mode":        "mock_signing" if mocked else "dilithium_pqc",
        "endpoint":    cfg.API_BASE + "/crypto",
        "in_process":  True,
        "mock_crypto": mocked,
        "crypto_backend": backend,
        "allow_mock_signing": allow_mock,
        "message": (
            f"MOCK CRYPTO ACTIVE — {backend}. Signing is "
            + ("PERMITTED via DEADSAT_ALLOW_MOCK_SIGNING=1 and is NOT "
               "cryptographically valid." if allow_mock
               else "REFUSED (503). Install liboqs and PyNaCl.")
            if mocked else
            f"Hybrid Ed25519 + ML-DSA-65 active ({backend})."
        ),
        # Retained for a split deployment where CY-1 runs on its own host.
        "cy1_base": cfg.CY1_BASE,
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
    if not await _ws_authenticate(websocket):
        return
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
    if not await _ws_authenticate(websocket):
        return
    await ws_manager.connect_events(websocket)
    try:
        while True:
            await websocket.receive_text()   # heartbeat
    except WebSocketDisconnect:
        await ws_manager.disconnect_events(websocket)
    except Exception:
        await ws_manager.disconnect_events(websocket)




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
    """
    Search catalog by satellite name (partial match).

    WIRING: this was the only catalog endpoint that reached into the private
    `cat._catalog` / `cat._loaded` internals and called load() by hand. It now
    uses SatelliteCatalog.search_by_name(), like every other catalog route
    uses the public API.
    """
    from satellite_catalog import get_catalog
    results = get_catalog().search_by_name(name, limit)
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


# ══════════════════════════════════════════════════════════════════════
# END-TO-END PIPELINE  (AI-1 classifier -> AI-2 recovery agent)
#
#   /pipeline/status    — are the trained artifacts present?
#   /pipeline/classify  — AI-1 only: telemetry window -> fault report
#   /pipeline/run       — full cycle: inject -> classify -> recover
#
# These share the app's live `emulator`, so the frontend telemetry stream
# and WebSocket event feed reflect everything the pipeline does.
# ══════════════════════════════════════════════════════════════════════

sys.path.append(str(Path(__file__).parent / "pipeline"))

# Faults the pipeline can inject. The first four are also AI-1's classes; the
# last two are injectable and recoverable but invisible to AI-1, which reads
# orbital elements only — run_pipeline() forces skip_classifier for those.
# See pipeline.CLASSIFIER_BLIND_FAULTS.
PIPELINE_FAULT_TYPES = [
    "SEU", "software_bug", "firmware_corruption", "command_injection",
    "battery_failure", "adcs_failure",
]

#: AI-1's actual output classes — a strict subset of the above.
CLASSIFIER_FAULT_TYPES = ["SEU", "software_bug", "firmware_corruption", "command_injection"]


class PipelineRunRequest(BaseModel):
    fault_type: str = Field(..., description="One of: " + ", ".join(PIPELINE_FAULT_TYPES))
    skip_classifier: bool = Field(
        False, description="Bypass AI-1 and hand the injected fault straight to AI-2"
    )
    norad_id: int = Field(28654, description="NORAD catalogue ID (default NOAA 18)")


class PipelineClassifyRequest(BaseModel):
    norad_id: int = Field(28654, description="NORAD catalogue ID")
    telemetry_window: Optional[list] = Field(
        None,
        description="Optional (seq_len, 11) orbital-element window. "
                    "If omitted, one is built from the live emulator state.",
    )


# ══════════════════════════════════════════════════════════════════════
# RF GROUND STATION BRIDGE  (Pi #1 -> Pi #2)
#
# The RF station and RTL-SDR live on the second Raspberry Pi. Proxying it
# through Pi #1 gives the frontend a single origin to talk to, so the
# browser never needs CORS access to Pi #2 and the operator only configures
# one base URL.
# ══════════════════════════════════════════════════════════════════════

@app.get("/rf/status")
async def rf_status():
    """Proxy the RF ground station on Pi #2. Degrades instead of hanging."""
    url = f"{cfg.RF_BASE}/rf/status"
    try:
        async with httpx.AsyncClient(timeout=cfg.RF_TIMEOUT_S) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return {"online": True, "source": url, "data": resp.json()}
    except Exception as e:
        # Pi #2 powered down or unreachable is an expected state, not a 500 —
        # the dashboard shows an offline badge rather than erroring out.
        return {
            "online": False,
            "source": url,
            "error":  str(e),
            "hint":   "Set DEADSAT_RF_BASE to Pi #2's address, e.g. http://192.168.1.51:8002",
        }


@app.get("/rf/spectrum")
async def rf_spectrum():
    """Proxy the RTL-SDR spectrum snapshot from Pi #2."""
    url = f"{cfg.RF_BASE}/rf/spectrum"
    try:
        async with httpx.AsyncClient(timeout=cfg.RF_TIMEOUT_S) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return {"online": True, "source": url, "data": resp.json()}
    except Exception as e:
        return {"online": False, "source": url, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════
# SYSTEM WIRING STATUS
# ══════════════════════════════════════════════════════════════════════

@app.get("/system/config")
async def system_config():
    """
    Non-secret view of the active deployment wiring. The frontend calls this
    on boot to discover what is actually connected.
    """
    return cfg.summary()


@app.get("/system/links")
async def system_links(x_api_key: Optional[str] = Header(default=None)):
    """
    Live check of every inter-component link, so a misconfigured two-Pi
    setup is visible at a glance instead of failing silently mid-demo.
    """
    links: dict = {}

    # Emulator (in-process)
    frame = emulator.get_latest_frame()
    links["emulator"] = {
        "connected": bool(frame),
        "detail": f"frame_id={frame.get('frame_id')}" if frame else "no frames yet",
    }

    # AI-1 classifier artifacts
    try:
        from classifier_inference import FaultClassifierInference

        bridge = FaultClassifierInference()
        links["ai1_classifier"] = {
            "connected": bridge.artifacts_available(),
            "detail": "artifacts loaded" if bridge.artifacts_available()
                      else f"missing: {', '.join(bridge.missing_artifacts())}",
        }
    except Exception as e:
        links["ai1_classifier"] = {"connected": False, "detail": str(e)}

    # AI-2 recovery agent
    try:
        import langgraph  # noqa: F401

        links["ai2_agent"] = {"connected": True, "detail": "langgraph available"}
    except ImportError:
        links["ai2_agent"] = {"connected": False, "detail": "langgraph not installed"}

    # Crypto layer
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{cfg.API_BASE}/crypto/health")
            links["crypto"] = {"connected": r.status_code == 200,
                               "detail": f"HTTP {r.status_code}"}
    except Exception as e:
        links["crypto"] = {"connected": False, "detail": str(e)}

    # RF station on Pi #2
    try:
        async with httpx.AsyncClient(timeout=cfg.RF_TIMEOUT_S) as client:
            r = await client.get(f"{cfg.RF_BASE}/rf/status")
            links["rf_station"] = {"connected": r.status_code == 200,
                                   "detail": f"{cfg.RF_BASE} HTTP {r.status_code}"}
    except Exception as e:
        links["rf_station"] = {"connected": False,
                               "detail": f"{cfg.RF_BASE} unreachable: {e}"}

    links["websocket_clients"] = {
        "connected": True,
        "detail": f"telemetry={len(ws_manager.telemetry_clients)} "
                  f"events={len(ws_manager.events_clients)}",
    }

    # BUG C: authentication state, so a key mismatch is diagnosable from the
    # dashboard instead of presenting as "everything works except the buttons".
    #
    # This endpoint is itself unauthenticated, so it deliberately reports only
    # whether a key is REQUIRED and whether THIS request carried a valid one —
    # never the key, and never a hint about it.
    if not cfg.API_KEY:
        links["auth"] = {
            "connected": True,
            "detail": "no DEADSAT_API_KEY set — API is open, WebSockets unguarded",
        }
    else:
        ok = (x_api_key == cfg.API_KEY)
        links["auth"] = {
            "connected": ok,
            "detail": ("X-API-Key valid — controls and WebSockets will work"
                       if ok else
                       "DEADSAT_API_KEY is set but this request sent "
                       + ("no X-API-Key" if not x_api_key else "a WRONG X-API-Key")
                       + " — mutating routes will 401 and WebSockets will be "
                         "closed with 1008. Set VITE_API_KEY in frontend/.env "
                         "to the same value."),
        }


    all_ok = all(v.get("connected") for v in links.values())
    return {"all_connected": all_ok, "links": links}


@app.get("/pipeline/status")
async def pipeline_status():
    """Report whether the AI-1 artifacts are trained and loadable."""
    try:
        from classifier_inference import FaultClassifierInference
        from feature_spec import CONFIG, FEATURE_COLS
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"Pipeline modules unavailable: {e}")

    bridge = FaultClassifierInference()
    available = bridge.artifacts_available()
    return {
        "artifacts_dir":     str(bridge.artifacts_dir),
        "artifacts_ready":   available,
        "missing_artifacts": bridge.missing_artifacts(),
        # What the pipeline accepts vs what AI-1 can actually name. Reported
        # separately so a client cannot assume the classifier covers all six.
        "fault_types":       PIPELINE_FAULT_TYPES,
        "classifier_fault_types": CLASSIFIER_FAULT_TYPES,
        "classifier_blind_faults": [f for f in PIPELINE_FAULT_TYPES
                                    if f not in CLASSIFIER_FAULT_TYPES],
        "seq_len":           CONFIG["seq_len"],
        "feature_cols":      FEATURE_COLS,
        # The dataset step is part of the instruction: training on the raw
        # snapshot CSVs produces artifacts that reproduce the degenerate label
        # distribution Phase 1 exists to fix.
        "hint": None if available else (
            "Not trained. Run: python generate_dataset.py --propagator sgp4 "
            "--verify  &&  python train_classifier.py"),
    }


@app.post("/pipeline/classify")
async def pipeline_classify(req: PipelineClassifyRequest):
    """
    AI-1 only. Classifies an orbital-element window and returns the
    fault_report that would be handed to the recovery agent.
    """
    try:
        import numpy as np
        from classifier_inference import ArtifactsNotFoundError, get_classifier
        from run_pipeline import _emulator_frame_to_orbital_window
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"Classifier unavailable: {e}")

    if req.telemetry_window is not None:
        window = np.asarray(req.telemetry_window, dtype=np.float32)
    else:
        window = _emulator_frame_to_orbital_window(emulator, norad_id=req.norad_id)

    try:
        bridge = get_classifier()
        return bridge.classify(window, norad_id=req.norad_id)
    except (ArtifactsNotFoundError, ImportError) as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/pipeline/run")
async def pipeline_run(req: PipelineRunRequest):
    """
    Full end-to-end cycle on the live emulator:
        inject fault -> AI-1 classify -> AI-2 recover

    Runs in the background; progress is broadcast on /ws/events.
    """
    if req.fault_type not in PIPELINE_FAULT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid fault_type '{req.fault_type}'. Valid: {PIPELINE_FAULT_TYPES}",
        )

    try:
        from run_pipeline import run_pipeline
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"Pipeline unavailable: {e}")

    async def _run():
        await ws_manager.broadcast_event(
            "pipeline_started",
            {"fault_type": req.fault_type, "skip_classifier": req.skip_classifier},
        )
        loop = asyncio.get_event_loop()

        def _sync():
            # Reuse the app's live emulator so the UI sees the whole cycle.
            return run_pipeline(
                fault_type=req.fault_type,
                skip_classifier=req.skip_classifier,
                norad_id=req.norad_id,
                emulator=emulator,
            )

        try:
            result = await loop.run_in_executor(None, _sync)
            await ws_manager.broadcast_event("pipeline_complete", result)
            print(f"[API] Pipeline complete: {result}")
        except Exception as e:
            await ws_manager.broadcast_event("pipeline_failed", {"error": str(e)})
            print(f"[API] Pipeline FAILED: {e}")

    asyncio.create_task(_run())

    return {
        "status":          "pipeline_started",
        "fault_type":      req.fault_type,
        "skip_classifier": req.skip_classifier,
        "norad_id":        req.norad_id,
        "message":         "Full AI-1 -> AI-2 cycle running — watch /ws/events",
    }


@app.post("/crypto/rotate")
async def crypto_rotate(_auth: None = Depends(require_api_key)):
    """
    Rotate the crypto layer's signing keypair.

    WIRING: this used to unconditionally proxy to CY1_BASE (a standalone
    CY-1 service on its own host/port) and fail with 503 CY-1 UNREACHABLE
    whenever nothing answered there — which is EVERY deployment this
    codebase actually runs, single-machine or Docker included, because
    crypto is mounted IN-PROCESS via crypto_router (see the comment block
    above, "Mounting the router makes Pi #1 the crypto authority
    in-process"). No standalone CY-1 service exists anywhere in this repo.
    The Security Console's "Rotate Keys" button was therefore permanently
    broken for every documented setup.

    Now: still try the external CY1_BASE first, for the split two-Pi
    deployment this endpoint was originally written for if anyone ever
    stands one up, then fall back to rotating the in-process keypair
    directly — which is what every actual deployment of this project needs.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(f"{cfg.CY1_BASE}/rotate")
            resp.raise_for_status()
            return {"rotated": True, "detail": resp.json(), "via": "external_cy1"}
    except Exception:
        pass  # no standalone CY-1 reachable — fall through to in-process rotation

    try:
        detail = rotate_keypair()
        return {"rotated": True, "detail": detail, "via": "in_process"}
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Key rotation failed — in-process rotation raised: {e}",
        )

# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────
#
# WIRING: this block sat in the MIDDLE of the file, with ~370 more lines of
# route definitions after it. It worked only by accident: `python main.py`
# executes this file as __main__, reaches uvicorn.run("main:app"), and uvicorn
# then IMPORTS the same file again under the name "main". The second execution
# is what registers the routes below — the __main__ pass never reached them.
#
# The cost was a duplicated module: two SatelliteEmulator instances constructed,
# every module-level side effect run twice, and two sets of globals with only
# one of them serving traffic. Moving it to the end makes the file read in
# execution order and removes the trap for the next person.
#
# uvicorn still re-imports by design (that is how the import string works), but
# now nothing is skipped in either pass.

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=cfg.API_HOST,
        port=cfg.API_PORT,
        reload=False,
    )
