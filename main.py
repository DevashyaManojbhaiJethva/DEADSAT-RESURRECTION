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
  POST /rf/ingest          — Pi #2 RF frame ingestion (canonical RF endpoint)
  GET  /rf/status          — RF ground station status (proxy to Pi #2)
  GET  /rf/spectrum        — RF spectrum data (proxy to Pi #2)

WebSocket Endpoints (FIX 4 & 5):
  WS   /ws/telemetry       — FE-1 live charts: pushes TM frame every 1s
  WS   /ws/events          — FE-2 recovery status: pushes agent events in real time
  WS   /ws/rf              — RF data streaming: pushes live RF frames from Pi #2
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
import httpx          # WIRING: used by the RF bridge to reach Pi #2
from concurrent.futures import ThreadPoolExecutor
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from history_privacy import Requester, current_context, filter_event, filter_history, audit_log, VALID_INTENTS
from jwt_auth import JWTValidationError, authenticate_jwt, issue_jwt, verify_scrypt_password
from privacy_audit import PrivacyAuditStore

sys.path.append(str(Path(__file__).parent / "emulator"))
sys.path.append(str(Path(__file__).parent / "agent"))
sys.path.append(str(Path(__file__).parent / "agents"))   # correct folder name
sys.path.append(str(Path(__file__).parent / "crypto"))   # CY-1 hybrid crypto layer
sys.path.append(str(Path(__file__).parent / "rf"))       # RF acquisition models

import os
from satellite_emulator import SatelliteEmulator, FaultType, seed_from_real_data
from real_data_fetcher import RealDataFetcher, NOAA_18_ID
from rf.models import RFFrame, RFIngestRequest, RFIngestResponse, RFHealthStatus
from rf.intelligence import process_rf_frame_for_intelligence

# SECURITY / WIRING: the real hybrid Ed25519 + ML-DSA-65 implementation.
#
# This tree previously hand-rolled /crypto/* with handlers that either proxied
# to a separate CY-1 process or returned fabricated signatures, and NEVER
# imported crypto/sign.py, verify.py, ledger.py, nonce.py or rogue_detector.py.
# backend/main.py mounted this router; root main.py did not. So the two trees
# had materially different security behaviour, and the tree that actually boots
# was the mock one — every post-quantum claim in the README described code that
# could not execute.
#
# Mounting the router makes Pi #1 the crypto authority in-process. CY1_BASE
# remains configurable for a split deployment, but recovery no longer depends
# on a second process being up.
from crypto_routes import router as crypto_router, startup_crypto, rotate_keypair  # noqa: E402

# N2YO API key — set via env var or hardcode after registering at n2yo.com
N2YO_API_KEY  = os.environ.get("N2YO_API_KEY", "")
TARGET_NORAD  = int(os.environ.get("TARGET_NORAD", str(NOAA_18_ID)))


# ──────────────────────────────────────────────
# WebSocket Connection Manager
# ──────────────────────────────────────────────

class ConnectionManager:
    """Manages all active WebSocket connections per channel."""

    def __init__(self):
        self.telemetry_clients: list[tuple[WebSocket, Requester, str]] = []
        self.events_clients:    list[tuple[WebSocket, Requester]] = []
        self.rf_clients:        list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect_telemetry(self, ws: WebSocket, requester: Requester, intent: str):
        # NOTE: the socket is accepted by _ws_authenticate() before this is
        # called — accepting twice raises. Auth must happen before a client is
        # registered for broadcasts, or an unauthenticated socket would receive
        # frames in the window before it was closed.
        async with self._lock:
            self.telemetry_clients.append((ws, requester, intent))
        print(f"[WS] Telemetry client connected. Total: {len(self.telemetry_clients)}")

    async def connect_events(self, ws: WebSocket, requester: Requester):
        async with self._lock:
            self.events_clients.append((ws, requester))
        print(f"[WS] Events client connected. Total: {len(self.events_clients)}")

    async def connect_rf(self, ws: WebSocket):
        async with self._lock:
            self.rf_clients.append(ws)
        print(f"[WS] RF client connected. Total: {len(self.rf_clients)}")

    async def disconnect_telemetry(self, ws: WebSocket):
        async with self._lock:
            self.telemetry_clients = [client for client in self.telemetry_clients if client[0] is not ws]
        print(f"[WS] Telemetry client disconnected. Remaining: {len(self.telemetry_clients)}")

    async def disconnect_events(self, ws: WebSocket):
        async with self._lock:
            self.events_clients = [client for client in self.events_clients if client[0] is not ws]
        print(f"[WS] Events client disconnected. Remaining: {len(self.events_clients)}")

    async def disconnect_rf(self, ws: WebSocket):
        async with self._lock:
            if ws in self.rf_clients:
                self.rf_clients.remove(ws)
        print(f"[WS] RF client disconnected. Remaining: {len(self.rf_clients)}")

    async def broadcast_telemetry(self, data: dict):
        """Push latest TM frame to all FE-1 chart clients."""
        if not self.telemetry_clients:
            return
        dead = []
        context = current_context(data)
        for ws, requester, intent in list(self.telemetry_clients):
            try:
                permitted = filter_event(data, requester, intent, context, audit=False)
                if permitted is not None:
                    await ws.send_text(json.dumps(permitted))
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
        for ws, _requester in list(self.events_clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect_events(ws)

    async def broadcast_rf(self, frame: RFFrame):
        """Push RF frame to all RF dashboard clients."""
        if not self.rf_clients:
            return
        msg = frame.model_dump_json()
        dead = []
        for ws in list(self.rf_clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect_rf(ws)


ws_manager = ConnectionManager()

# ──────────────────────────────────────────────
# Globals
# ──────────────────────────────────────────────

# WIRING: deployment settings (hosts, ports, CORS, security toggles) come
# from config.py so the same code runs on a laptop and on the two-Pi split.
import config as cfg

# Metadata-only audit records persist independently of the emulator's real
# telemetry ring buffer.  No historical frame payload is stored here.
audit_log.configure(PrivacyAuditStore(cfg.PRIVACY_AUDIT_DB))

emulator = SatelliteEmulator(tick_interval=cfg.EMULATOR_TICK_S,
                             norad_id=cfg.DEFAULT_NORAD_ID)
_fetcher: Optional[RealDataFetcher] = None
_fetcher_lock = threading.Lock()

# RF frame storage for latest frame from Pi #2
_latest_rf_frame: Optional[RFFrame] = None
_rf_frame_lock = threading.Lock()


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

    # Initialise the crypto layer up front: generate the hybrid keypair, open
    # the ledger, start its watchdog and run the sign/verify self-test. Doing
    # it here rather than lazily on first request means a broken or mocked
    # crypto backend is visible at startup instead of mid-recovery.
    try:
        startup_crypto()
    except Exception as exc:
        print(f"[API] WARNING: crypto layer failed to initialise: {exc}")
        print("[API]          /crypto/* will error; recovery uplinks will abort")

    # Start background WebSocket telemetry broadcaster
    task = asyncio.create_task(_telemetry_broadcaster())
    print("[API] DeadSat FastAPI server started")
    print("[API] Emulator streaming telemetry...")
    # WIRING: print the active deployment wiring so a misconfigured two-Pi
    # setup is obvious at startup rather than failing silently mid-demo.
    cfg.print_banner()
    yield
    task.cancel()
    emulator.stop()
    print("[API] Server shutting down")


async def _telemetry_broadcaster():
    """
    Background task: push TM frame to all WS /ws/telemetry clients every 1s.

    WIRING: wrapped in try/except. Previously a single raise here (e.g. a
    client vanishing mid-send) killed the task permanently and telemetry
    silently stopped for every connected client until the process restarted.
    """
    while True:
        try:
            await asyncio.sleep(1.0)
            frame = emulator.get_latest_frame()
            if not frame:
                continue                      # nothing ticked yet
            frame["overall_health"] = emulator.get_overall_health()
            await ws_manager.broadcast_telemetry(frame)
        except asyncio.CancelledError:
            print("[API] Telemetry broadcaster stopped")
            raise
        except Exception as e:
            print(f"[API] Telemetry broadcast error (continuing): {e}")


# ──────────────────────────────────────────────
# App
# ──────────────────────────────────────────────

app = FastAPI(
    title="DeadSat Resurrection API",
    description="Satellite emulator + recovery agent integration layer",
    version="1.0.0",
    lifespan=lifespan,
)

# WIRING: origins now come from config.py (DEADSAT_CORS_ORIGINS) instead of
# a blanket "*". The API exposes mutating routes — fault injection, recovery,
# reset — so any site being able to call them was a real exposure once this
# runs on a LAN rather than loopback.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the real crypto layer. Serves /crypto/sign, /verify, /ledger, /alerts,
# /check-rogue, /health and /metrics from crypto/crypto_routes.py — the same
# module CY-1 runs standalone on :8001. Registered BEFORE the app-level
# /crypto/* handlers below so the genuine implementation always wins a path
# collision rather than losing one silently.
app.include_router(crypto_router)


# ──────────────────────────────────────────────
# Optional API-key auth for mutating routes
# ──────────────────────────────────────────────

def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """
    WIRING: shared-secret guard for state-changing endpoints.

    Disabled when DEADSAT_API_KEY is unset, so a bench setup keeps working
    unchanged. Set the variable on Pi #1 to lock down the LAN deployment.
    """
    if not cfg.API_KEY:
        return
    if x_api_key != cfg.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


def _requester_from_api_key(supplied_key: Optional[str]) -> Requester:
    """Bind history access to the existing credential, never a caller-supplied role."""
    if cfg.API_KEY:
        if supplied_key != cfg.API_KEY:
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
        fingerprint = hashlib.sha256(cfg.API_KEY.encode("utf-8")).hexdigest()[:16]
        return Requester(f"api-key:{fingerprint}", True, frozenset({"history:read"}),
                         role="legacy_api_key", authentication="api_key")
    # An open bench has no authenticatable person. Sensitive frames remain
    # redacted; deployments set DEADSAT_API_KEY for attributable access.
    return Requester("unauthenticated-bench", False, frozenset(), authentication="none")


def _requester_from_authorization(authorization: Optional[str], x_api_key: Optional[str]) -> Requester:
    """Prefer a verified JWT identity; retain the API key only as legacy fallback."""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Invalid authorization header")
        try:
            return authenticate_jwt(token, secret=cfg.JWT_SECRET, issuer=cfg.JWT_ISSUER,
                                    audience=cfg.JWT_AUDIENCE)
        except JWTValidationError as exc:
            raise HTTPException(status_code=401, detail="Invalid or expired access token") from exc
    return _requester_from_api_key(x_api_key)


def _bearer_requester(authorization: Optional[str]) -> Requester:
    """JWT-only identity for login-derived security operations."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Bearer access token is required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    try:
        return authenticate_jwt(token, secret=cfg.JWT_SECRET, issuer=cfg.JWT_ISSUER,
                                audience=cfg.JWT_AUDIENCE)
    except JWTValidationError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired access token") from exc


def _history_intent(intent: Optional[str]) -> str:
    selected = (intent or "monitoring").strip().lower()
    if selected not in VALID_INTENTS:
        raise HTTPException(status_code=422, detail="Unsupported history intent")
    return selected


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


# ──────────────────────────────────────────────
# Request Models
# ──────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


@app.post("/auth/login")
async def login(request: LoginRequest):
    """Authenticate a deployment-configured operator and issue a signed JWT."""
    user = cfg.AUTH_USERS.get(request.username)
    password_hash = user.get("password_hash") if user else None
    if not isinstance(password_hash, str) or not verify_scrypt_password(request.password, password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    subject, role = user.get("sub", request.username), user.get("role", "operator")
    permissions = user.get("permissions", [])
    if (not cfg.JWT_SECRET or not isinstance(subject, str) or not isinstance(role, str) or
            not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions)):
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    token = issue_jwt(subject=subject, role=role, permissions=permissions, secret=cfg.JWT_SECRET,
                      expires_in=cfg.JWT_TTL_S, issuer=cfg.JWT_ISSUER, audience=cfg.JWT_AUDIENCE)
    return {"access_token": token, "token_type": "bearer"}


@app.post("/auth/ws-token")
async def websocket_token(authorization: Optional[str] = Header(default=None)):
    """Exchange a bearer session for a short-lived, WebSocket-only JWT."""
    requester = _bearer_requester(authorization)
    token = issue_jwt(subject=requester.identifier, role=requester.role,
                      permissions=requester.permissions, secret=cfg.JWT_SECRET,
                      expires_in=cfg.JWT_WS_TTL_S, token_type="websocket",
                      issuer=cfg.JWT_ISSUER, audience=cfg.JWT_AUDIENCE)
    return {"connection_token": token, "token_type": "websocket", "expires_in": cfg.JWT_WS_TTL_S}

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


async def _ws_history_requester(websocket: WebSocket, *, allow_legacy: bool = True) -> Optional[Requester]:
    """Authenticate a socket with a short-lived, purpose-bound connection JWT."""
    token = websocket.query_params.get("connection_token")
    if token:
        await websocket.accept()
        try:
            return authenticate_jwt(token, secret=cfg.JWT_SECRET, issuer=cfg.JWT_ISSUER,
                                    audience=cfg.JWT_AUDIENCE,
                                    expected_token_type="websocket")
        except JWTValidationError:
            await websocket.close(code=1008, reason="Invalid or expired access token")
            return None
    if not allow_legacy:
        await websocket.accept()
        await websocket.close(code=1008, reason="Authenticated connection token is required")
        return None
    if not await _ws_authenticate(websocket):
        return None
    return _requester_from_api_key(cfg.API_KEY if cfg.API_KEY else None)


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
async def get_telemetry_history(n: int = 60, intent: str = "monitoring",
                                x_api_key: Optional[str] = Header(default=None),
                                authorization: Optional[str] = Header(default=None)):
    """Return real ring-buffer frames after per-event privacy filtering."""
    requester = _requester_from_authorization(authorization, x_api_key)
    if "history:read" not in requester.permissions:
        raise HTTPException(status_code=403, detail="History access is not permitted")
    intent = _history_intent(intent)
    history = emulator.get_frame_history(last_n=n)
    context = current_context(emulator.get_latest_frame())
    frames = filter_history(history, requester, intent, context)
    return {"frames": frames, "count": len(frames), "intent": intent, "context": context.state}


@app.get("/privacy/audit")
async def get_privacy_audit(limit: int = 100, authorization: Optional[str] = Header(default=None)):
    """Read real persisted privacy decisions; never exposes telemetry payloads."""
    requester = _requester_from_authorization(authorization, None)
    if "privacy:audit:read" not in requester.permissions:
        raise HTTPException(status_code=403, detail="Privacy audit access is not permitted")
    return {"records": audit_log.records(limit), "count": len(audit_log.records(limit))}


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
async def inject_fault(req: FaultInjectRequest, _auth: None = Depends(require_api_key)):
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
async def trigger_recovery(req: RecoveryTriggerRequest, _auth: None = Depends(require_api_key)):
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
async def start_demo(_auth: None = Depends(require_api_key)):
    """Lock /seed during live demo to prevent mid-demo emulator mutations."""
    global _demo_active
    _demo_active = True
    return {"status": "demo_active", "seed_locked": True}

@app.post("/demo/end")
async def end_demo(_auth: None = Depends(require_api_key)):
    global _demo_active
    _demo_active = False
    return {"status": "demo_ended", "seed_locked": False}

@app.post("/seed")
async def seed_from_satnogs(_auth: None = Depends(require_api_key)):
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
async def reset_satellite(_auth: None = Depends(require_api_key)):
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


# ──────────────────────────────────────────────────────────────────────
# REMOVED: the hand-rolled /crypto/sign.
#
# It caught any CY-1 connection failure and returned a FABRICATED signature —
#     "ml_dsa_sig":  f"MOCK_ML_DSA_{sha256(...)}"
#     "mock": True
# — which the agent carried onto the command, the verification gate then
# rejected with MOCK_SIGNATURE, and the operator saw as "RECOVERY FAILED".
# With no runnable CY-1 there was no path where recovery could succeed, and the
# reported cause pointed at the wrong thing.
#
# A signature that is manufactured and then refused downstream is worse than an
# honest failure: it burns a recovery attempt and produces a misleading log.
# /crypto/sign is now served by crypto_router with the real hybrid primitives,
# and refuses with 503 SIGNING_UNAVAILABLE when the crypto backend is mocked
# (unless DEADSAT_ALLOW_MOCK_SIGNING=1). See crypto/crypto_routes.py:sign().
# ──────────────────────────────────────────────────────────────────────

@app.post("/crypto/check-command")
async def check_command(req: CommandCheckRequest):
    """
    Legacy pre-uplink sanity check. NOT a signature verification.

    SECURITY: this endpoint used to rubber-stamp. Its verdict was

        is_valid = req.signed and len(req.signature) > 0

    i.e. any non-empty string, with `signed` set by the caller, was reported
    `valid: true` and "Valid signature" — no cryptography involved. That is
    precisely the failure mode the verification gate exists to prevent, sitting
    on an endpoint named check-command.

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


# ──────────────────────────────────────────────────────────────────────
# REMOVED: hand-rolled /crypto/verify, /crypto/ledger, /crypto/health and
# /crypto/alerts.
#
# All four are now served by crypto_router from crypto/crypto_routes.py, using
# the real hybrid implementation (verify.py checks BOTH Ed25519 and ML-DSA-65
# and the TTL; ledger.py maintains the SHA-256 hash chain; rogue_detector.py
# raises the alerts). The versions here proxied to a separate CY-1 process and
# returned empty lists or `valid: false` whenever it was unreachable, so on
# this tree the ledger was always empty and no signature was ever genuinely
# checked.
#
# The agent's verification gate calls /crypto/verify with the router's schema
# (command_hex / ml_dsa_sig_hex / ed25519_sig_hex / valid_until) — see
# agents/recovery_agent.py:_verify_command().
# ──────────────────────────────────────────────────────────────────────

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
# WebSocket Endpoints (FIX 4 & 5)
# ──────────────────────────────────────────────

@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket):
    """
    FIX 4: WebSocket for FE-1 live charts.
    Pushes TM frame every 1s via background broadcaster.
    On connect: sends last 60 frames immediately so charts fill instantly.

    Requires the shared secret when DEADSAT_API_KEY is set — see
    _ws_authenticate(). Live telemetry is exactly what the key is meant to
    protect, and this socket used to hand it to anyone who asked.
    """
    requester = await _ws_history_requester(websocket)
    if requester is None:
        return
    if "history:read" not in requester.permissions:
        await websocket.close(code=1008, reason="History access is not permitted")
        return
    try:
        intent = _history_intent(websocket.query_params.get("intent"))
    except HTTPException:
        await websocket.close(code=1008, reason="Unsupported history intent")
        return
    await ws_manager.connect_telemetry(websocket, requester, intent)
    try:
        # Send history immediately on connect so FE-1 charts aren't empty
        context = current_context(emulator.get_latest_frame())
        history = filter_history(emulator.get_frame_history(60), requester, intent, context)
        await websocket.send_text(json.dumps({
            "type":   "history",
            "frames": history,
            "count":  len(history),
            "intent": intent,
            "context": context.state,
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

    Requires a verified, short-lived WebSocket JWT. A legacy shared key cannot
    access this recovery/security event stream.
    """
    requester = await _ws_history_requester(websocket, allow_legacy=False)
    if requester is None:
        return
    if "history:read" not in requester.permissions:
        await websocket.close(code=1008, reason="Event stream access is not permitted")
        return
    await ws_manager.connect_events(websocket, requester)
    try:
        while True:
            await websocket.receive_text()   # heartbeat
    except WebSocketDisconnect:
        await ws_manager.disconnect_events(websocket)
    except Exception:
        await ws_manager.disconnect_events(websocket)


@app.websocket("/ws/rf")
async def ws_rf(websocket: WebSocket):
    """
    RF data streaming WebSocket for live dashboard visualization.
    
    Pushes RF frames from Pi #2 as they are ingested via /rf/ingest.
    Provides real-time RF telemetry without polling overhead.
    
    Requires the shared secret when DEADSAT_API_KEY is set — see
    _ws_authenticate().
    """
    if not await _ws_authenticate(websocket):
        return
    await ws_manager.connect_rf(websocket)
    
    # Send latest frame immediately if available
    with _rf_frame_lock:
        if _latest_rf_frame:
            try:
                await websocket.send_text(_latest_rf_frame.model_dump_json())
            except Exception:
                pass
    
    try:
        while True:
            await websocket.receive_text()   # heartbeat
    except WebSocketDisconnect:
        await ws_manager.disconnect_rf(websocket)
    except Exception:
        await ws_manager.disconnect_rf(websocket)




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

sys.path.append(str(Path(__file__).parent / "models"))

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


@app.get("/rf/intelligence")
async def rf_intelligence():
    """
    Get RF intelligence analysis.
    
    Returns processed RF features, anomaly detection, and alerts
    from the RF intelligence pipeline.
    """
    from rf.intelligence import get_rf_intelligence
    
    intelligence = get_rf_intelligence()
    summary = intelligence.get_summary()
    
    return summary


@app.post("/rf/ingest")
async def rf_ingest(request: RFIngestRequest, _auth: None = Depends(require_api_key)):
    """
    RF data ingestion endpoint from Pi #2.
    
    This is the canonical endpoint where Pi #2 sends RF frames.
    It validates the frame, stores the latest RF state, and makes it
    available to the RF intelligence pipeline and WebSocket clients.
    
    Security: Requires API key authentication when DEADSAT_API_KEY is set.
    """
    global _latest_rf_frame
    
    frame = request.frame
    
    # Validate frame structure
    try:
        # Check timestamp freshness
        frame_time = datetime.fromisoformat(frame.timestamp.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - frame_time).total_seconds()
        if age > 10.0:
            return RFIngestResponse(
                accepted=False,
                sequence=frame.sequence,
                message=f"Stale frame: {age:.1f}s old",
                warnings=["Frame timestamp too old"]
            )
        
        # Check frequency range
        if not (1e6 <= frame.frequency_hz <= 30e9):
            return RFIngestResponse(
                accepted=False,
                sequence=frame.sequence,
                message=f"Invalid frequency: {frame.frequency_hz} Hz",
                warnings=["Frequency out of valid range"]
            )
        
        # Check signal strength range
        if not (-150 <= frame.signal_dbm <= -10):
            return RFIngestResponse(
                accepted=False,
                sequence=frame.sequence,
                message=f"Invalid signal strength: {frame.signal_dbm} dBm",
                warnings=["Signal strength out of valid range"]
            )
        
        # Check sequence monotonicity
        if _latest_rf_frame and frame.sequence <= _latest_rf_frame.sequence:
            return RFIngestResponse(
                accepted=False,
                sequence=frame.sequence,
                message=f"Non-increasing sequence: {frame.sequence} <= {_latest_rf_frame.sequence}",
                warnings=["Sequence number not monotonically increasing"]
            )
        
    except ValueError as e:
        return RFIngestResponse(
            accepted=False,
            sequence=frame.sequence if hasattr(frame, 'sequence') else 0,
            message=f"Invalid frame format: {e}",
            warnings=["Frame validation failed"]
        )
    
    # Store latest RF frame (thread-safe)
    with _rf_frame_lock:
        _latest_rf_frame = frame
    
    # Process RF frame for intelligence
    try:
        rf_intelligence = process_rf_frame_for_intelligence(frame)
        print(f"[RF INTELLIGENCE] Frame {frame.sequence} processed: "
              f"signal_trend={rf_intelligence.get('signal_trend')}, "
              f"alerts={rf_intelligence.get('active_alerts')}")
    except Exception as e:
        print(f"[RF INTELLIGENCE] Processing error: {e}")
    
    # Broadcast to RF WebSocket clients
    await ws_manager.broadcast_rf(frame)
    
    # Log successful ingestion
    print(f"[RF INGEST] Frame {frame.sequence} accepted: signal={frame.signal_dbm:.1f} dBm "
          f"SNR={frame.snr_db:.1f} dB freq={frame.frequency_hz/1e6:.3f} MHz")
    
    return RFIngestResponse(
        accepted=True,
        sequence=frame.sequence,
        message="RF frame accepted and processed",
        warnings=[]
    )


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

    Deliberately NOT guarded by require_api_key: its whole purpose is to
    diagnose a broken deployment, including a wrong key. It reports whether the
    supplied key was accepted, never the key itself.
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

    # Crypto layer — probe CY-1 directly rather than having the API call
    # itself over HTTP (a self-request that needlessly ties up a worker).
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{cfg.CY1_BASE}/health")
            links["crypto"] = {
                "connected": r.status_code == 200,
                "detail": f"CY-1 at {cfg.CY1_BASE} HTTP {r.status_code}",
            }
    except Exception as e:
        links["crypto"] = {
            "connected": False,
            "detail": f"CY-1 at {cfg.CY1_BASE} unreachable: {e}",
        }

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
async def pipeline_classify(req: PipelineClassifyRequest, _auth: None = Depends(require_api_key)):
    """
    AI-1 only. Classifies an orbital-element window and returns the
    fault_report that would be handed to the recovery agent.
    """
    try:
        import numpy as np
        from classifier_inference import ArtifactsNotFoundError, get_classifier
        from pipeline import _emulator_frame_to_orbital_window
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
async def pipeline_run(req: PipelineRunRequest, _auth: None = Depends(require_api_key)):
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
        from pipeline import run_pipeline
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
