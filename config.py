"""
config.py — DeadSat Resurrection · deployment configuration
============================================================
Single source of truth for every host, port and URL in the system.

Everything is environment-driven so the same code runs on a laptop (all
services on localhost) and on the two-Raspberry-Pi deployment:

  ┌──────────────────────────────────────────────────────────────┐
  │  Pi #1 — Raspberry Pi 4 Model B          (default :8000)      │
  │    • AI-1  fault classifier                                  │
  │    • AI-2  LangGraph recovery agent                          │
  │    • Crypto layer (ML-DSA-65 / Ed25519, Redis nonce store)   │
  │    • Satellite emulator + FastAPI + WebSockets               │
  └──────────────────────────────────────────────────────────────┘
                 ▲                                  │
                 │ /rf/status (proxied)             │ /ws/telemetry, /ws/events
                 │                                  ▼
  ┌──────────────────────────────┐    ┌──────────────────────────────┐
  │  Pi #2 — RF ground station   │    │  Operator browser (frontend) │
  │    • RTL-SDR reader          │    │    VITE_API_BASE -> Pi #1    │
  │    • Spectrum / Doppler      │    └──────────────────────────────┘
  │    (default :8002)           │
  └──────────────────────────────┘

Configuration
-------------
Copy `.env.example` to `.env` and set the values for your deployment.
On Pi #1:

    DEADSAT_API_HOST=0.0.0.0          # listen on all interfaces
    DEADSAT_API_PORT=8000
    DEADSAT_PUBLIC_HOST=192.168.1.50  # Pi #1's LAN address
    DEADSAT_RF_BASE=http://192.168.1.51:8002   # Pi #2
    DEADSAT_CORS_ORIGINS=http://192.168.1.60:3000

On Pi #2 the RF service only needs its own host/port.

Nothing here has side effects — importing this module is always safe.
"""

from __future__ import annotations

import os
import json
from pathlib import Path

# Load .env if python-dotenv is available (it is in requirements.txt).
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:  # pragma: no cover - dotenv is optional at runtime
    pass


def _env(key: str, default: str) -> str:
    """Read an env var, falling back to `default` when unset or blank."""
    val = os.environ.get(key, "").strip()
    return val or default


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


# ──────────────────────────────────────────────────────────────────────
# Pi #1 — main API (AI-1, AI-2, crypto, emulator)
# ──────────────────────────────────────────────────────────────────────

#: Interface the API binds to. 0.0.0.0 to accept LAN traffic from Pi #2
#: and the operator browser; 127.0.0.1 to stay loopback-only.
API_HOST: str = _env("DEADSAT_API_HOST", "0.0.0.0")
API_PORT: int = _env_int("DEADSAT_API_PORT", 8000)

#: Address other machines use to reach Pi #1. Must be the LAN IP or
#: hostname in a two-Pi setup — "localhost" only works single-box.
PUBLIC_HOST: str = _env("DEADSAT_PUBLIC_HOST", "localhost")

#: Base URL of the main API, as seen by clients.
API_BASE: str = _env("DEADSAT_API_BASE", f"http://{PUBLIC_HOST}:{API_PORT}")

#: Crypto signing / verification endpoints (same box as the agent on Pi #1,
#: but still routed through API_BASE so a split deployment stays possible).
CRYPTO_SIGN_URL: str = _env("DEADSAT_CRYPTO_SIGN_URL", f"{API_BASE}/crypto/sign")
CRYPTO_VERIFY_URL: str = _env("DEADSAT_CRYPTO_VERIFY_URL", f"{API_BASE}/crypto/verify")

#: CY-1 — the standalone Dilithium signing service the API proxies to.
#: Runs alongside the API on Pi #1 by default. Was hardcoded to
#: http://localhost:8001 in three separate places in main.py.
CY1_BASE: str = _env("DEADSAT_CY1_BASE", f"http://{PUBLIC_HOST}:8001")


# ──────────────────────────────────────────────────────────────────────
# Pi #2 — RF ground station (RTL-SDR)
# ──────────────────────────────────────────────────────────────────────

RF_HOST: str = _env("DEADSAT_RF_HOST", "0.0.0.0")
RF_PORT: int = _env_int("DEADSAT_RF_PORT", 8002)

#: Where Pi #1 reaches the RF service. Set this to Pi #2's LAN address.
RF_BASE: str = _env("DEADSAT_RF_BASE", f"http://{PUBLIC_HOST}:{RF_PORT}")

#: Seconds before the RF proxy gives up. Kept short so a missing or
#: powered-down Pi #2 degrades the dashboard instead of hanging it.
RF_TIMEOUT_S: float = float(_env("DEADSAT_RF_TIMEOUT_S", "3.0"))

#: RF acquisition parameters for Pi #2 service
RF_CENTER_FREQUENCY_HZ: float = float(_env("RF_CENTER_FREQUENCY_HZ", "137900000.0"))
RF_SAMPLE_RATE: int = _env_int("RF_SAMPLE_RATE", "2400000")
RF_GAIN: float = float(_env("RF_GAIN", "49.6"))
RF_STREAM_INTERVAL_S: float = float(_env("RF_STREAM_INTERVAL_S", "1.0"))
RF_MOCK_MODE: bool = _env_bool("RF_MOCK_MODE", False)

#: RF_LOCATION_* are explicit aliases; legacy GROUND_* remains supported.
RF_LOCATION_LAT: float = float(_env("RF_LOCATION_LAT", _env("GROUND_LAT", "23.03")))
RF_LOCATION_LON: float = float(_env("RF_LOCATION_LON", _env("GROUND_LON", "72.58")))
GROUND_LAT: float = RF_LOCATION_LAT
GROUND_LON: float = RF_LOCATION_LON
GROUND_ELEV_M: float = float(_env("GROUND_ELEV_M", "53"))

#: Default satellite for RF tracking
DEFAULT_NORAD_ID: int = _env_int("DEFAULT_NORAD_ID", "59051")  # Meteor-M2-4
RF_TARGET_NORAD_ID: int = _env_int("RF_TARGET_NORAD_ID", str(DEFAULT_NORAD_ID))


# ──────────────────────────────────────────────────────────────────────
# Frontend / CORS
# ──────────────────────────────────────────────────────────────────────

def _cors_origins() -> list[str]:
    """
    Comma-separated allowed origins.

    Defaults to the usual local dev ports rather than "*", because the API
    exposes mutating routes (fault injection, recovery, reset). Set
    DEADSAT_CORS_ORIGINS to the operator machine's origin in a LAN
    deployment. "*" is still accepted but warns at startup.
    """
    raw = _env(
        "DEADSAT_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173",
    )
    return [o.strip() for o in raw.split(",") if o.strip()]


CORS_ORIGINS: list[str] = _cors_origins()


def _is_loopback_origin(origin: str) -> bool:
    """
    True if `origin` can only ever be reached from the API host itself.

    Used by print_banner() to catch the LAN-bind + loopback-CORS combination,
    which is always a misconfiguration: the browser connects over the LAN,
    every WebSocket works (they are exempt from CORS), and every REST call is
    blocked — so the dashboard shows live telemetry while every panel that
    needs a fetch stays empty.
    """
    host = origin.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return host in ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0")


def cors_is_unreachable_from_lan() -> bool:
    """
    True when the API accepts LAN traffic but no CORS origin is a LAN address.

    Kept as a function rather than a constant so a test can call it after
    monkey-patching API_HOST / CORS_ORIGINS.
    """
    lan_facing = API_HOST not in ("127.0.0.1", "localhost", "::1")
    if not lan_facing or "*" in CORS_ORIGINS:
        return False
    return bool(CORS_ORIGINS) and all(_is_loopback_origin(o) for o in CORS_ORIGINS)


# ──────────────────────────────────────────────────────────────────────
# Security toggles
# ──────────────────────────────────────────────────────────────────────

#: Shared secret required by mutating endpoints when set. Empty = disabled
#: (fine for a closed bench network, not for anything routable).
API_KEY: str = _env("DEADSAT_API_KEY", "")

# JWTs are issued by the deployment's existing identity provider.  The API
# verifies HS256 signatures only; it never seeds or manages production users.
JWT_SECRET: str = _env("DEADSAT_JWT_SECRET", "")
JWT_ISSUER: str = _env("DEADSAT_JWT_ISSUER", "")
JWT_AUDIENCE: str = _env("DEADSAT_JWT_AUDIENCE", "")
JWT_TTL_S: int = _env_int("DEADSAT_JWT_TTL_S", 900)
JWT_WS_TTL_S: int = _env_int("DEADSAT_JWT_WS_TTL_S", 60)

def _auth_users() -> dict[str, dict[str, object]]:
    """Load deployment-managed users; malformed configuration disables login."""
    raw = _env("DEADSAT_AUTH_USERS_JSON", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(name): user for name, user in value.items() if isinstance(user, dict)}

AUTH_USERS: dict[str, dict[str, object]] = _auth_users()
PRIVACY_AUDIT_DB: str = _env("DEADSAT_PRIVACY_AUDIT_DB", str(Path(__file__).resolve().parent / "data" / "privacy_audit.sqlite3"))

#: Require a successful /crypto/verify before the emulator applies a
#: recovery procedure. This is the security gate the project is built
#: around; leave it on unless the crypto service genuinely is unavailable.
REQUIRE_COMMAND_VERIFICATION: bool = _env_bool(
    "DEADSAT_REQUIRE_VERIFICATION", True
)

#: Permit the agent's mock-signing fallback when the crypto service is
#: unreachable. OFF by default: previously any signing error silently
#: produced a fake signature that was still marked as signed.
ALLOW_MOCK_SIGNING: bool = _env_bool("DEADSAT_ALLOW_MOCK_SIGNING", False)

#: Allow uplink even with no ground-contact window (bench/demo mode).
ALLOW_UPLINK_WITHOUT_CONTACT: bool = _env_bool(
    "DEADSAT_ALLOW_UPLINK_WITHOUT_CONTACT", True
)


# ──────────────────────────────────────────────────────────────────────
# Satellite / emulator defaults
# ──────────────────────────────────────────────────────────────────────

DEFAULT_NORAD_ID: int = _env_int("DEADSAT_NORAD_ID", 28654)  # NOAA 18
SATELLITE_ID: str = _env("DEADSAT_SATELLITE_ID", "DEADSAT-1")
EMULATOR_TICK_S: float = float(_env("DEADSAT_TICK_S", "1.0"))


# ──────────────────────────────────────────────────────────────────────
# Diagnostics
# ──────────────────────────────────────────────────────────────────────

def summary() -> dict:
    """Machine-readable view of the active configuration (no secrets)."""
    return {
        "api": {
            "bind": f"{API_HOST}:{API_PORT}",
            "public_base": API_BASE,
        },
        "rf": {
            "base": RF_BASE, 
            "timeout_s": RF_TIMEOUT_S,
            "center_frequency_hz": RF_CENTER_FREQUENCY_HZ,
            "sample_rate": RF_SAMPLE_RATE,
            "gain": RF_GAIN,
            "stream_interval_s": RF_STREAM_INTERVAL_S,
            "mock_mode": RF_MOCK_MODE,
            "ground_station": {
                "lat": GROUND_LAT,
                "lon": GROUND_LON,
                "elev_m": GROUND_ELEV_M
            },
            "target_norad_id": RF_TARGET_NORAD_ID
        },
        "crypto": {"sign": CRYPTO_SIGN_URL, "verify": CRYPTO_VERIFY_URL},
        "cors_origins": CORS_ORIGINS,
        "security": {
            "api_key_set": bool(API_KEY),
            "require_command_verification": REQUIRE_COMMAND_VERIFICATION,
            "allow_mock_signing": ALLOW_MOCK_SIGNING,
            "allow_uplink_without_contact": ALLOW_UPLINK_WITHOUT_CONTACT,
        },
        "satellite": {"norad_id": DEFAULT_NORAD_ID, "id": SATELLITE_ID},
    }


def print_banner() -> None:
    """Print the active wiring at startup so misconfiguration is obvious."""
    print("[Config] ── DeadSat deployment ─────────────────────────")
    print(f"[Config]   API bind     : {API_HOST}:{API_PORT}")
    print(f"[Config]   API base     : {API_BASE}")
    print(f"[Config]   RF station   : {RF_BASE}")
    print(f"[Config]   CORS origins : {', '.join(CORS_ORIGINS)}")
    print(f"[Config]   Verify gate  : {'ON' if REQUIRE_COMMAND_VERIFICATION else 'OFF'}")
    print(f"[Config]   Mock signing : {'ALLOWED' if ALLOW_MOCK_SIGNING else 'BLOCKED'}")
    print(f"[Config]   API key auth : {'ON' if API_KEY else 'OFF (open)'}")
    if "*" in CORS_ORIGINS:
        print("[Config]   WARNING: CORS is '*' — any site can drive this API")
    if not API_KEY:
        print("[Config]   WARNING: no DEADSAT_API_KEY — mutating routes are unauthenticated")

    # WIRING: the LAN-bind + loopback-CORS trap. This combination cannot work
    # and fails in a way that looks like a frontend bug: WebSockets are exempt
    # from CORS, so /ws/telemetry connects and the header badge reads LIVE TM,
    # while every REST panel (TLE, catalog, crypto status, ledger, alerts,
    # pipeline status, /system/links, RF spectrum) is blocked by the browser
    # and dies silently in its .catch(). Note .env.example ships exactly this
    # pairing — DEADSAT_API_HOST=0.0.0.0 with loopback-only origins.
    if cors_is_unreachable_from_lan():
        print("[Config]   " + "!" * 58)
        print(f"[Config]   WARNING: API is bound to {API_HOST} (LAN-facing) but every")
        print("[Config]            CORS origin is loopback-only:")
        print(f"[Config]              {', '.join(CORS_ORIGINS)}")
        print("[Config]            A browser on any other machine will connect the")
        print("[Config]            WebSockets — the dashboard will say LIVE TM — while")
        print("[Config]            EVERY REST panel is blocked and silently empty.")
        print("[Config]            Set DEADSAT_CORS_ORIGINS to the operator's real")
        print("[Config]            origin, e.g.:")
        print("[Config]              DEADSAT_CORS_ORIGINS=http://192.168.1.60:3000")
        print("[Config]            Do NOT use '*' — this API exposes fault injection,")
        print("[Config]            recovery and reset.")
        print("[Config]   " + "!" * 58)

    print("[Config] ─────────────────────────────────────────────")


if __name__ == "__main__":
    import json

    print_banner()
    print()
    print(json.dumps(summary(), indent=2))
