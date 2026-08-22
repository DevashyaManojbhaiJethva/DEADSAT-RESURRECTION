"""
DeadSat Resurrection — LangGraph Recovery Agent
AI-2 owned module

Each line below states what the code does and where to check it. The previous
header opened "All bugs fixed, all improvements applied" and listed four claims
that were not true of the code beneath it — the kind of thing that, once a
reviewer finds one, costs the rest of the file its credibility.

Verified:
  Bug 1 — ThreadPoolExecutor(max_workers=10) set in main.py lifespan
  Bug 2 — Fallback cap uses len(priority_list), not a hardcoded 3
          (route_after_fallback, via state["priority_list_len"])
  Bug 3 — /seed guarded by the demo lock in main.py
  Improvement 1 — Recovery log persisted per run by _persist_log()
  Improvement 3 — min_confidence respected in node_select_procedure, and a
                  skip now routes back into selection instead of falling
                  through with a stale procedure
  Improvement 5 — Catalog baselines included in the recovery-log trace

Corrected:
  Improvement 2 — "Fault state telemetry has noise on top of fault effects"
          was FALSE: _update_nominal_drift() returned early during a fault, so
          every subsystem froze (1 distinct obc_temp_c across 30 ticks). Now
          true — drift runs in every state, clamped to physical limits while
          faulted. See emulator/satellite_emulator.py.
  Improvement 4 — "Fallback TLE updated to recent epoch" was FALSE: the epoch
          was 24163 (day 163 of 2024). Now 26158 with a staleness warning.
          See emulator/contact_calculator.py.

Outstanding — do not describe as fixed:
  Bug 4 — "Contact calculator step size reduced to 10s" is accurate but was
          not an optimisation: 24 h at 10 s is 8,640 SGP4 propagations run
          synchronously inside the recovery graph, 3x MORE work than before.
          Scheduled for replacement with coarse-then-refine (Prompt 8.2).
"""

import json
import time
import httpx
import os
from pathlib import Path
from typing import TypedDict, Optional, Literal
from datetime import datetime, timezone

try:
    from langgraph.graph import StateGraph, END, START
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    StateGraph = None  # type: ignore
    END = None         # type: ignore
    START = None       # type: ignore
    print("[RecoveryAgent] WARNING: langgraph not installed. Run: pip install langgraph")

import sys
sys.path.append(str(Path(__file__).parent.parent / "emulator"))
from satellite_emulator import SatelliteEmulator, FaultType
from contact_calculator import ContactCalculator


# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

# Find procedure library — works whether file is in agents/ or root
def _find_procedure_library() -> Path:
    candidates = [
        Path(__file__).parent / "procedure_library.json",           # agents/procedure_library.json
        Path(__file__).parent.parent / "agents" / "procedure_library.json",  # ../agents/
        Path(__file__).parent.parent / "procedure_library.json",    # root
        Path.cwd() / "agents" / "procedure_library.json",           # cwd/agents/
        Path.cwd() / "procedure_library.json",                      # cwd/
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]  # fallback

PROCEDURE_LIBRARY_PATH = _find_procedure_library()

# ── Deployment configuration ───────────────────────────────────────
# WIRING: endpoints come from config.py so the agent works on the two-Pi
# split (crypto lives on Pi #1 alongside the agent, but the URLs are no
# longer hardcoded to localhost, which broke any non-loopback deployment).
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    import config as _cfg

    SIGNING_ENDPOINT   = _cfg.CRYPTO_SIGN_URL
    VERIFY_ENDPOINT    = _cfg.CRYPTO_VERIFY_URL
    FASTAPI_BASE       = _cfg.API_BASE
    SATELLITE_ID       = _cfg.SATELLITE_ID
    DEFAULT_NORAD_ID   = _cfg.DEFAULT_NORAD_ID
    REQUIRE_VERIFY     = _cfg.REQUIRE_COMMAND_VERIFICATION
    ALLOW_MOCK_SIGNING = _cfg.ALLOW_MOCK_SIGNING
    ALLOW_NO_CONTACT   = _cfg.ALLOW_UPLINK_WITHOUT_CONTACT
except ImportError:  # standalone use without the repo root on sys.path
    SIGNING_ENDPOINT   = os.environ.get("DEADSAT_CRYPTO_SIGN_URL", "http://localhost:8000/crypto/sign")
    VERIFY_ENDPOINT    = os.environ.get("DEADSAT_CRYPTO_VERIFY_URL", "http://localhost:8000/crypto/verify")
    FASTAPI_BASE       = os.environ.get("DEADSAT_API_BASE", "http://localhost:8000")
    SATELLITE_ID       = "DEADSAT-1"
    DEFAULT_NORAD_ID   = 28654
    REQUIRE_VERIFY     = True
    ALLOW_MOCK_SIGNING = False
    ALLOW_NO_CONTACT   = True

POLL_INTERVAL_S        = 1.0
MAX_POLL_ATTEMPTS      = 30

# Recovery log persistence directory
LOG_DIR = Path(__file__).parent.parent / "recovery_logs"


# ──────────────────────────────────────────────
# Agent State
# ──────────────────────────────────────────────

class AgentState(TypedDict):
    fault_type:           str
    fault_detail:         dict
    telemetry_frame:      dict
    fault_confidence:     float        # AI-1 classifier confidence (0.0–1.0)
    norad_id:             int

    procedure_library:    dict
    selected_procedure:   dict
    priority_index:       int
    priority_list_len:    int          # FIX Bug 2: track actual list length

    command_sequence:     list
    signed_commands:      list
    signing_success:      bool

    contact_window:       dict
    uplink_allowed:       bool

    recovery_success:     bool
    recovery_log:         list
    catalog_baselines:    dict         # Improvement 5: orbital baselines for reasoning

    next_step:            str
    error:                Optional[str]
    last_error:           Optional[str]   # WIRING: error carried over from the previous attempt
    attempt_count:        int


# ──────────────────────────────────────────────
# Node Functions
# ──────────────────────────────────────────────

def node_load_procedures(state: AgentState) -> AgentState:
    """Node 1: Load procedure library + fetch catalog baselines for reasoning."""
    print("[Agent] ── Node 1: Loading procedure library")
    try:
        with open(PROCEDURE_LIBRARY_PATH) as f:
            library = json.load(f)
        state["procedure_library"] = library

        # Improvement 5: Load catalog baselines for this satellite
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from satellite_catalog import get_catalog
            baselines = get_catalog().get_anomaly_baselines(state.get("norad_id", DEFAULT_NORAD_ID))
            state["catalog_baselines"] = baselines or {}
            if baselines:
                print(f"[Agent]    Catalog baselines loaded: alt={baselines.get('altitude_km_approx')}km, "
                      f"period={baselines.get('period_minutes')}min")
        except Exception as e:
            state["catalog_baselines"] = {}
            print(f"[Agent]    Catalog baselines unavailable: {e}")

        state["recovery_log"].append({
            "step":               "load_procedures",
            "status":             "ok",
            "catalog_baselines":  state["catalog_baselines"],
            "ts":                 _ts()
        })
        print(f"[Agent]    Loaded {len(library['procedures'])} fault procedures")
    except Exception as e:
        state["error"] = f"Failed to load procedures: {e}"
        state["recovery_log"].append({"step": "load_procedures", "status": "error", "error": str(e), "ts": _ts()})
    return state


def node_select_procedure(state: AgentState) -> AgentState:
    """
    Node 2: Select procedure by fault type, priority index, and confidence.
    Improvement 3: Skips procedures where fault_confidence < min_confidence.
    Bug Fix 2: Uses actual priority_list length for fallback cap.
    """
    print(f"[Agent] ── Node 2: Selecting procedure for fault={state['fault_type']} "
          f"priority_idx={state['priority_index']} confidence={state.get('fault_confidence', 1.0):.2f}")
    try:
        fault_key     = state["fault_type"]
        library       = state["procedure_library"]
        confidence    = state.get("fault_confidence", 1.0)

        if fault_key not in library["procedures"]:
            state["error"] = f"Unknown fault type: {fault_key}"
            return state

        fault_entry   = library["procedures"][fault_key]
        priority_list = fault_entry["recovery_priority"]
        idx           = state["priority_index"]

        # Store actual list length for Bug 2 fix
        state["priority_list_len"] = len(priority_list)

        if idx >= len(priority_list):
            state["error"]        = f"Exhausted all {len(priority_list)} procedures for {fault_key}"
            state["recovery_success"] = False
            state["next_step"]    = "exhausted"
            return state

        procedure = priority_list[idx]

        # Improvement 3: Check min_confidence threshold
        #
        # BUG E: this used to increment priority_index and return WITHOUT
        # setting selected_procedure, error or next_step. The comment claimed
        # "graph will re-enter select", but route_after_select() had no such
        # route — it saw no error and no "exhausted", so it went straight to
        # generate_commands, which read a STALE selected_procedure from the
        # previous attempt (or KeyError'd on the first pass). The agent could
        # therefore uplink a procedure it had just decided not to trust.
        #
        # Live trigger: software_bug has min_confidence 0.70 then 0.80, so any
        # confidence in [0.70, 0.80) hits this.
        #
        # Now signalled explicitly with next_step="reselect", which
        # route_after_select() routes back into this node.
        min_conf = procedure.get("min_confidence", 0.0)
        if confidence < min_conf:
            print(f"[Agent]    Skipping {procedure['procedure_name']} — "
                  f"confidence {confidence:.2f} < required {min_conf:.2f}")
            state["priority_index"] += 1
            state["attempt_count"]  += 1
            state["next_step"]       = "reselect"
            # Clear it so a stale value from a previous attempt cannot be
            # uplinked if the routing is ever changed again.
            state["selected_procedure"] = {}
            state["recovery_log"].append({
                "step":      "select_procedure",
                "skipped":   procedure["procedure_name"],
                "reason":    f"confidence {confidence:.2f} < min_confidence {min_conf:.2f}",
                "ts":        _ts()
            })
            return state

        state["next_step"] = None
        state["selected_procedure"] = procedure

        # Improvement 5: Add baseline comparison to log
        baseline_note = ""
        frame    = state.get("telemetry_frame", {})
        baselines = state.get("catalog_baselines", {})
        if baselines and frame:
            bat_nom  = baselines.get("mean_motion_nominal")
            alt      = baselines.get("altitude_km_approx")
            bat_cur  = frame.get("battery_pct")
            if bat_cur and alt:
                baseline_note = (f"Satellite nominal altitude ~{alt}km. "
                                 f"Current battery: {bat_cur}%. "
                                 f"Fault pattern consistent with {fault_key}.")

        state["recovery_log"].append({
            "step":           "select_procedure",
            "procedure":      procedure["procedure_name"],
            "priority":       procedure["priority"],
            "min_confidence": min_conf,
            "fault_confidence": confidence,
            "baseline_note":  baseline_note,
            "ts":             _ts()
        })
        print(f"[Agent]    Selected: {procedure['procedure_name']} (priority {procedure['priority']})")
        if baseline_note:
            print(f"[Agent]    {baseline_note}")
    except Exception as e:
        state["error"] = str(e)
    return state


def node_generate_commands(state: AgentState) -> AgentState:
    """Node 3: Extract and validate command sequence from procedure."""
    print("[Agent] ── Node 3: Generating command sequence")
    try:
        proc     = state["selected_procedure"]
        commands = proc["commands"]
        enriched = []
        for cmd in commands:
            enriched.append({
                **cmd,
                "satellite_id":   SATELLITE_ID,
                "procedure_name": proc["procedure_name"],
                "fault_type":     state["fault_type"],
                "generated_at":   _ts(),
                "signed":         False,
                "signature":      None,
            })
        state["command_sequence"] = enriched
        state["recovery_log"].append({
            "step":     "generate_commands",
            "count":    len(enriched),
            "commands": [c["cmd"] for c in enriched],
            "ts":       _ts()
        })
        print(f"[Agent]    Generated {len(enriched)} commands: {[c['cmd'] for c in enriched]}")
    except Exception as e:
        state["error"] = str(e)
    return state


#: Re-sign if a signature has less than this much life left when the uplink
#: actually happens. crypto/sign.py issues a 120 s TTL; anything under a few
#: seconds is not worth transmitting because verification happens after it.
SIGNATURE_MIN_REMAINING_S = 15


def _sign_one(cmd: dict) -> dict:
    """
    Request a hybrid signature for one command. Raises on failure.

    Extracted so the uplink node can re-sign at transmission time without
    duplicating the request/response contract — see _refresh_expiring_signatures().
    """
    resp = httpx.post(
        SIGNING_ENDPOINT,
        json={"command_bytes": cmd["cmd"].encode().hex()},
        timeout=5.0,
    )
    resp.raise_for_status()
    result = resp.json()
    return {
        **cmd,
        "signed":      True,
        "ml_dsa_sig":  result["ml_dsa_sig"],
        "ed25519_sig": result["ed25519_sig"],
        "nonce":       result["nonce"],
        "ledger_id":   result["ledger_id"],
        # The signing endpoint refuses to fabricate (503 SIGNING_UNAVAILABLE),
        # so `mock` should never be set — carried through defensively so the
        # verification gate can reject it if it ever is.
        "mock":        bool(result.get("mock", False)),
        "valid_until": result.get("valid_until"),
    }


def _refresh_expiring_signatures(state: AgentState) -> Optional[str]:
    """
    BUG B — sign at TRANSMISSION time, not at planning time.

    crypto/sign.py stamps a 120 s TTL (TTL_SECONDS), but the graph signs at
    node 4 and only then schedules the uplink at node 5, where
    find_next_contact() may return an AOS up to 24 hours out. Every command
    genuinely held for a real ground-contact window therefore expired long
    before transmission, and verify_command()'s first check —
    `checked_at > valid_until` — rejected it as COMMAND_EXPIRED. The failure
    only stayed hidden because DEADSAT_ALLOW_UPLINK_WITHOUT_CONTACT defaults
    to 1, so bench runs uplink immediately and never wait.

    DECISION — sign at transmission time. Considered and rejected: making the
    TTL a function of the scheduled AOS. That would mean minting a signature
    valid for up to 24 hours, which is a far larger replay window for a
    command authorising a spacecraft action, and it cannot be done here anyway
    because signing happens before the window is known. Re-signing just before
    transmission keeps the TTL short — the property that makes it useful —
    and costs one extra round trip on a path that has just waited hours.

    Returns None on success, or an error string if re-signing failed.
    """
    now = int(time.time())
    refreshed, failures = [], []

    for cmd in state.get("signed_commands", []):
        remaining = int(cmd.get("valid_until") or 0) - now
        if remaining >= SIGNATURE_MIN_REMAINING_S:
            refreshed.append(cmd)
            continue
        print(f"[Agent]    Signature for {cmd.get('cmd')} has {remaining}s left "
              f"— re-signing at transmission time")
        try:
            refreshed.append(_sign_one(cmd))
        except Exception as exc:
            failures.append(f"{cmd.get('cmd')}: {exc}")

    if failures:
        return "Re-signing before uplink failed: " + "; ".join(failures)

    if refreshed != state.get("signed_commands"):
        state["signed_commands"] = refreshed
        state["recovery_log"].append({
            "step":   "refresh_signatures",
            "reason": "signatures near or past TTL at transmission time",
            "count":  len(refreshed),
            "ts":     _ts(),
        })
    return None


def node_request_signing(state: AgentState) -> AgentState:
    """Node 4: Request CRYSTALS-Dilithium signing from CY-1."""
    print("[Agent] ── Node 4: Requesting Dilithium signing from CY-1")
    try:
        signed = []
        for cmd in state["command_sequence"]:
            try:
                signed.append(_sign_one(cmd))
            except Exception as sign_err:
                # WIRING / SECURITY FIX: previously ANY signing failure produced a
                # fake signature marked `signed: True`, after which the agent
                # printed "signing SUCCESS". Taking the crypto service offline
                # therefore bypassed command signing completely. Now signing
                # failure fails the node, and the mock path must be opted into
                # explicitly via DEADSAT_ALLOW_MOCK_SIGNING=1.
                # Distinguish "the signer refused because crypto is mocked"
                # from "the signer is unreachable". Both abort, but the
                # operator needs to know which — the first is fixed by
                # installing liboqs, the second by starting the service.
                reason = "SIGNING_UNAVAILABLE"
                detail = str(sign_err)
                resp_obj = getattr(sign_err, "response", None)
                if resp_obj is not None:
                    try:
                        body = resp_obj.json().get("detail")
                        if isinstance(body, dict):
                            reason = body.get("reason", reason)
                            detail = body.get("message", detail)
                    except Exception:
                        pass

                if not ALLOW_MOCK_SIGNING:
                    print(f"[Agent]    Signing FAILED [{reason}] {detail} "
                          f"— aborting uplink")
                    state["signing_success"] = False
                    state["error"] = f"{reason}: {detail}"
                    state["recovery_log"].append({
                        "step":   "request_signing",
                        "status": "error",
                        "reason": reason,
                        "error":  detail,
                        "mock_allowed": False,
                        "ts":     _ts(),
                    })
                    return state

                print(f"[Agent]    CY-1 unavailable ({sign_err}) — MOCK signing "
                      f"(DEADSAT_ALLOW_MOCK_SIGNING=1, NOT cryptographically valid)")
                signed.append({
                    **cmd,
                    "signed":    True,
                    "mock":      True,
                    "signature": f"MOCK_SIG_{cmd['cmd']}_{int(time.time())}",
                })

        state["signed_commands"] = signed
        state["signing_success"] = True
        n_mock = sum(1 for c in signed if c.get("mock"))
        if n_mock:
            print(f"[Agent]    Signed {len(signed)} commands ({n_mock} MOCK)")
        else:
            print(f"[Agent]    CY-1 signing SUCCESS — {len(signed)} commands signed")
        state["recovery_log"].append({
            "step":   "request_signing",
            "status": "ok",
            "count":  len(signed),
            "mock_count": n_mock,
            "ts":     _ts()
        })
    except Exception as e:
        state["error"]           = str(e)
        state["signing_success"] = False
    return state


def _verify_command(cmd: dict) -> tuple[bool, str]:
    """
    WIRING: ask the crypto layer to verify one signed command.

    This closes the gap where the agent requested signatures but nothing ever
    checked them before the emulator executed the procedure — verify_command()
    existed but was unreachable from the recovery path.

    Returns (ok, reason).
    """
    if cmd.get("mock"):
        return (False, "MOCK_SIGNATURE")

    required = ("ml_dsa_sig", "ed25519_sig")
    if not all(cmd.get(k) for k in required):
        return (False, "MISSING_SIGNATURE_FIELDS")

    try:
        # WIRING: this must match crypto_routes.VerifyRequest, which is now the
        # endpoint actually serving /crypto/verify on both trees. The agent
        # previously sent {command_bytes, ml_dsa_sig, ed25519_sig, nonce} — the
        # shape of main.py's hand-rolled proxy handler. Against the real router
        # that is a 422, which surfaced as VERIFY_UNAVAILABLE and looked like
        # the crypto service being down rather than a contract mismatch.
        resp = httpx.post(
            VERIFY_ENDPOINT,
            json={
                "command_hex":     cmd["cmd"].encode().hex(),
                "ml_dsa_sig_hex":  cmd["ml_dsa_sig"],
                "ed25519_sig_hex": cmd["ed25519_sig"],
                "valid_until":     int(cmd.get("valid_until") or 0),
                # Replay protection is consumed at verification time, not at
                # signing time — /crypto/verify claims this nonce atomically
                # and rejects REPLAYED_NONCE on a second presentation. Omitting
                # it yields MISSING_NONCE rather than a silent pass.
                "nonce":           cmd.get("nonce"),
            },
            timeout=5.0,
        )
        resp.raise_for_status()
        result = resp.json()
        # verify_command() flags a result produced by the development shim.
        # Treat it as a failure here too, so a mocked signer cannot be
        # certified by a client that forgot to look.
        if result.get("mock") and not result.get("valid"):
            return (False, str(result.get("reason", "MOCK_CRYPTO_NOT_VERIFIABLE")))
        return (bool(result.get("valid")), str(result.get("reason", "UNKNOWN")))
    except Exception as exc:
        return (False, f"VERIFY_UNAVAILABLE: {exc}")


def node_schedule_uplink(state: AgentState) -> AgentState:
    """Node 5: Check ground contact window. Bug Fix 4: step_seconds=10."""
    print("[Agent] ── Node 5: Scheduling uplink")
    try:
        calc = ContactCalculator()
        calc.load_tle()
        in_contact = calc.is_in_contact_now()

        if in_contact:
            state["uplink_allowed"] = True
            state["contact_window"] = {"status": "IN_CONTACT", "ts": _ts()}
            print("[Agent]    Ground contact: ACTIVE — uplink allowed immediately")
        else:
            # Bug Fix 4: step_seconds=10 for accurate AOS timing
            window = calc.find_next_contact(search_hours=24.0, step_seconds=10.0)
            state["contact_window"] = window or {}
            # WIRING: was unconditionally True. Now driven by config so a real
            # deployment can enforce contact windows, while bench/demo runs
            # keep the permissive behaviour (default ON).
            state["uplink_allowed"] = ALLOW_NO_CONTACT
            if window:
                from datetime import datetime, timezone
                aos           = datetime.fromisoformat(window["aos"])
                seconds_to_aos = (aos - datetime.now(timezone.utc)).total_seconds()
                print(f"[Agent]    Next AOS in {seconds_to_aos:.0f}s "
                      f"(max El {window['max_elevation_deg']}°) — DEV MODE uplink allowed")
            else:
                print("[Agent]    No contact window — DEV MODE uplink allowed")

        state["recovery_log"].append({
            "step":       "schedule_uplink",
            "in_contact": in_contact,
            "allowed":    state["uplink_allowed"],
            "window":     state.get("contact_window", {}),
            "ts":         _ts()
        })
    except Exception as e:
        # WIRING: don't set state["error"] here — a contact-calculator problem
        # (e.g. sgp4 missing) used to abort the whole recovery via
        # route_after_select even though the uplink was still being allowed.
        print(f"[Agent]    Contact calc error: {e} — "
              f"{'allowing' if ALLOW_NO_CONTACT else 'blocking'} uplink")
        state["uplink_allowed"] = ALLOW_NO_CONTACT
        state["contact_window"] = {"status": "CALC_ERROR", "error": str(e), "ts": _ts()}
        state["recovery_log"].append({
            "step":    "schedule_uplink",
            "status":  "calc_error",
            "error":   str(e),
            "allowed": state["uplink_allowed"],
            "ts":      _ts(),
        })
    return state


def node_uplink_commands(state: AgentState, emulator: SatelliteEmulator) -> AgentState:
    """Node 6: Uplink signed commands to satellite emulator."""
    print("[Agent] ── Node 6: Uplinking commands to satellite")
    if not state["uplink_allowed"]:
        state["error"] = "Uplink not allowed — no ground contact"
        return state
    try:
        proc_name = state["selected_procedure"]["procedure_name"]

        # BUG B: signatures were minted at node 4 with a 120 s TTL, but the
        # uplink may have waited for a contact window since. Refresh anything
        # that has expired or is about to, BEFORE the verification gate runs —
        # otherwise verify_command() rejects it as COMMAND_EXPIRED and the
        # operator sees a signature failure for a command that was fine.
        resign_error = _refresh_expiring_signatures(state)
        if resign_error:
            print(f"[Agent]    {resign_error}")
            state["error"] = resign_error
            state["recovery_log"].append({
                "step":   "uplink_commands",
                "status": "resign_failed",
                "error":  resign_error,
                "ts":     _ts(),
            })
            return state

        # ── WIRING: verification gate ─────────────────────────────────
        # Nothing previously checked signatures before execution. Every
        # command must now verify before the emulator applies anything.
        if REQUIRE_VERIFY:
            print(f"[Agent]    Verifying {len(state['signed_commands'])} signatures with CY-1 ...")
            failures = []
            for cmd in state["signed_commands"]:
                ok, reason = _verify_command(cmd)
                if not ok:
                    failures.append({"cmd": cmd.get("cmd"), "reason": reason})

            if failures:
                detail = ", ".join(f"{f['cmd']}={f['reason']}" for f in failures)
                print(f"[Agent]    VERIFICATION FAILED — refusing uplink ({detail})")
                state["error"] = f"Command verification failed: {detail}"
                state["recovery_log"].append({
                    "step":     "verify_commands",
                    "status":   "rejected",
                    "failures": failures,
                    "ts":       _ts(),
                })
                return state

            print(f"[Agent]    All signatures VERIFIED — uplink authorised")
            state["recovery_log"].append({
                "step":     "verify_commands",
                "status":   "ok",
                "verified": len(state["signed_commands"]),
                "ts":       _ts(),
            })
        else:
            print("[Agent]    Verification gate DISABLED "
                  "(DEADSAT_REQUIRE_VERIFICATION=0) — uplinking unverified commands")

        success = emulator.apply_recovery(proc_name)

        # WIRING: the return value used to be assigned and never read, so a
        # rejected procedure still advanced to monitoring as if it had worked.
        if not success:
            print(f"[Agent]    Emulator REJECTED procedure {proc_name}")
            state["error"] = f"Emulator rejected procedure: {proc_name}"
            state["recovery_log"].append({
                "step":      "uplink_commands",
                "status":    "rejected_by_emulator",
                "procedure": proc_name,
                "ts":        _ts(),
            })
            return state

        try:
            httpx.post(
                f"{FASTAPI_BASE}/recovery/uplink",
                json={
                    "procedure_name": proc_name,
                    "commands":       state["signed_commands"],
                    "fault_type":     state["fault_type"],
                    "ts":             _ts(),
                },
                timeout=2.0
            )
        except Exception:
            pass
        state["recovery_log"].append({
            "step":          "uplink_commands",
            "procedure":     proc_name,
            "commands_sent": len(state["signed_commands"]),
            "ts":            _ts()
        })
        print(f"[Agent]    Uplinked {len(state['signed_commands'])} commands for {proc_name}")
    except Exception as e:
        state["error"] = str(e)
    return state


def node_monitor_recovery(state: AgentState, emulator: SatelliteEmulator) -> AgentState:
    """Node 7: Poll emulator and verify success criteria."""
    print("[Agent] ── Node 7: Monitoring recovery")
    proc     = state["selected_procedure"]
    criteria = proc.get("success_criteria", {})
    timeout  = proc.get("timeout_s", 30)
    attempts = 0
    max_a    = min(int(timeout), MAX_POLL_ATTEMPTS)

    while attempts < max_a:
        time.sleep(POLL_INTERVAL_S)
        frame  = emulator.get_latest_frame()
        health = emulator.get_overall_health()
        passed = _check_criteria(frame, criteria)
        print(f"[Agent]    Poll {attempts+1}/{max_a} — health={health} | criteria_met={passed}")

        # BUG A: was `if passed or health == "nominal"`. The `or` made
        # success_criteria advisory: apply_recovery() resets subsystem statuses,
        # so health flips to "nominal" on the very first poll and recovery was
        # declared successful regardless of whether the criteria were met.
        # The fallback path was therefore all but unreachable, which is why the
        # project's headline claim — "automatically falls back to an alternate
        # procedure" — had never actually been exercised.
        #
        # The criteria are now authoritative. `health` is still recorded for
        # diagnostics, but it cannot substitute for them.
        if passed:
            state["recovery_success"] = True
            state["recovery_log"].append({
                "step":   "monitor_recovery",
                "result": "SUCCESS",
                "polls":  attempts + 1,
                "health": health,
                "criteria_met": True,
                "ts":     _ts()
            })
            print("[Agent]    Recovery VERIFIED ✓")
            return state
        attempts += 1

    state["recovery_success"] = False
    state["recovery_log"].append({
        "step":   "monitor_recovery",
        "result": "TIMEOUT",
        "polls":  attempts,
        "ts":     _ts()
    })
    print(f"[Agent]    Recovery FAILED after {attempts} polls — escalating to fallback")
    return state


def node_fallback(state: AgentState) -> AgentState:
    """Node 8: Fallback — try next procedure."""
    print("[Agent] ── Node 8: FALLBACK — trying next procedure")
    state["priority_index"]   += 1
    state["attempt_count"]    += 1
    state["command_sequence"]  = []
    state["signed_commands"]   = []
    state["signing_success"]   = False
    state["recovery_success"]  = False
    # WIRING: clear the error from the failed attempt, otherwise
    # route_after_select() sees it and aborts before the fallback procedure
    # is ever tried. The failure is already recorded in recovery_log.
    state["last_error"]        = state.get("error")
    state["error"]             = None
    state["recovery_log"].append({
        "step":          "fallback",
        "after_error":   state.get("last_error"),
        "next_priority": state["priority_index"],
        "attempt":       state["attempt_count"],
        "ts":            _ts()
    })
    return state


def node_report_success(state: AgentState) -> AgentState:
    """Node 9a: Success — persist log to disk."""
    print("[Agent] ══ RECOVERY COMPLETE ══")
    state["recovery_log"].append({
        "step":      "final_report",
        "result":    "SUCCESS",
        "procedure": state["selected_procedure"]["procedure_name"],
        "attempts":  state["attempt_count"] + 1,
        "ts":        _ts()
    })
    _persist_log(state)   # Improvement 1
    _print_summary(state)
    return state


def node_report_failure(state: AgentState) -> AgentState:
    """Node 9b: Failure — persist log to disk."""
    print("[Agent] ══ ALL PROCEDURES EXHAUSTED — SATELLITE UNRECOVERABLE ══")
    state["recovery_log"].append({
        "step":   "final_report",
        "result": "FAILURE",
        "error":  state.get("error"),
        "ts":     _ts()
    })
    _persist_log(state)   # Improvement 1
    _print_summary(state)
    return state


# ──────────────────────────────────────────────
# Routing Functions
# ──────────────────────────────────────────────

def route_after_signing(state: AgentState) -> Literal["schedule_uplink", "fallback"]:
    if state.get("signing_success"):
        return "schedule_uplink"
    return "fallback"


def route_after_monitoring(state: AgentState) -> Literal["report_success", "fallback"]:
    if state.get("recovery_success"):
        return "report_success"
    return "fallback"


def route_after_uplink(state: AgentState) -> Literal["monitor_recovery", "fallback"]:
    """
    WIRING: the uplink -> monitor edge used to be unconditional, so a failed
    signature verification or an emulator rejection still advanced to
    monitoring. Divert those to the fallback procedure instead.
    """
    if state.get("error"):
        return "fallback"
    return "monitor_recovery"


def route_after_fallback(state: AgentState) -> Literal["select_procedure", "report_failure"]:
    # Bug Fix 2: cap based on actual procedure list length (2 per fault type)
    max_attempts = state.get("priority_list_len", 2)
    if state.get("next_step") == "exhausted" or state.get("attempt_count", 0) >= max_attempts:
        return "report_failure"
    return "select_procedure"


def route_after_select(
    state: AgentState,
) -> Literal["generate_commands", "select_procedure", "report_failure"]:
    """
    BUG E: the "reselect" route did not exist. When node_select_procedure
    skipped a procedure on min_confidence it returned with priority_index
    advanced but selected_procedure untouched, and this router sent the state
    to generate_commands anyway — uplinking a stale procedure, or raising
    KeyError on the first pass.
    """
    if state.get("error") or state.get("next_step") == "exhausted":
        return "report_failure"
    if state.get("next_step") == "reselect":
        return "select_procedure"
    return "generate_commands"


# ──────────────────────────────────────────────
# Graph Builder
# ──────────────────────────────────────────────

def build_recovery_graph(emulator: SatelliteEmulator):
    if not LANGGRAPH_AVAILABLE:
        raise ImportError("langgraph not installed — run: pip install langgraph")

    def _uplink(state):  return node_uplink_commands(state, emulator)
    def _monitor(state): return node_monitor_recovery(state, emulator)

    graph = StateGraph(AgentState)  # type: ignore

    graph.add_node("load_procedures",   node_load_procedures)
    graph.add_node("select_procedure",  node_select_procedure)
    graph.add_node("generate_commands", node_generate_commands)
    graph.add_node("request_signing",   node_request_signing)
    graph.add_node("schedule_uplink",   node_schedule_uplink)
    graph.add_node("uplink_commands",   _uplink)
    graph.add_node("monitor_recovery",  _monitor)
    graph.add_node("fallback",          node_fallback)
    graph.add_node("report_success",    node_report_success)
    graph.add_node("report_failure",    node_report_failure)

    graph.add_edge(START,                "load_procedures")
    graph.add_edge("load_procedures",    "select_procedure")
    graph.add_edge("schedule_uplink",    "uplink_commands")
    graph.add_edge("generate_commands",  "request_signing")

    graph.add_conditional_edges("uplink_commands",   route_after_uplink, {
        "monitor_recovery": "monitor_recovery",
        "fallback":         "fallback",
    })

    graph.add_conditional_edges("select_procedure",  route_after_select, {
        "generate_commands": "generate_commands",
        # BUG E: self-edge so a min_confidence skip re-enters selection with the
        # advanced priority_index instead of falling through to command
        # generation with a stale selected_procedure. Terminates because
        # priority_index only ever increases and idx >= len(priority_list)
        # sets next_step="exhausted".
        "select_procedure":  "select_procedure",
        "report_failure":    "report_failure",
    })
    graph.add_conditional_edges("request_signing",   route_after_signing, {
        "schedule_uplink": "schedule_uplink",
        "fallback":        "fallback",
    })
    graph.add_conditional_edges("monitor_recovery",  route_after_monitoring, {
        "report_success": "report_success",
        "fallback":       "fallback",
    })
    graph.add_conditional_edges("fallback",          route_after_fallback, {
        "select_procedure": "select_procedure",
        "report_failure":   "report_failure",
    })

    graph.add_edge("report_success", END)
    graph.add_edge("report_failure", END)

    return graph.compile()


# ──────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────

class RecoveryAgent:
    def __init__(self, emulator: SatelliteEmulator):
        self.emulator = emulator
        self.graph    = build_recovery_graph(emulator)

    def run(self, fault_report: dict) -> dict:
        print(f"\n[Agent] ══════════════════════════════════════")
        print(f"[Agent] RECOVERY INITIATED — fault: {fault_report.get('fault_type')}")
        print(f"[Agent] ══════════════════════════════════════")

        initial_state: AgentState = {
            "fault_type":        fault_report.get("fault_type", "SEU"),
            "fault_detail":      fault_report.get("fault_detail", {}),
            "telemetry_frame":   fault_report.get("telemetry_frame", {}),
            "fault_confidence":  float(fault_report.get("confidence", 1.0)),
            "norad_id":          int(fault_report.get("norad_id", DEFAULT_NORAD_ID)),
            "procedure_library": {},
            "selected_procedure": {},
            "priority_index":    0,
            "priority_list_len": 2,
            "command_sequence":  [],
            "signed_commands":   [],
            "signing_success":   False,
            "contact_window":    {},
            "uplink_allowed":    False,
            "recovery_success":  False,
            "recovery_log":      [],
            "catalog_baselines": {},
            "next_step":         "",
            "error":             None,
            "last_error":        None,
            "attempt_count":     0,
        }

        start_ts    = time.time()
        final_state = self.graph.invoke(initial_state)
        elapsed     = time.time() - start_ts

        return {
            "success":        final_state.get("recovery_success", False),
            "procedure_used": final_state.get("selected_procedure", {}).get("procedure_name"),
            "attempts":       final_state.get("attempt_count", 0) + 1,
            "elapsed_s":      round(elapsed, 2),
            "log":            final_state.get("recovery_log", []),
            "error":          final_state.get("error"),
        }


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_criteria(frame: dict, criteria: dict) -> bool:
    """
    Evaluate a procedure's success_criteria against a telemetry frame.

    Three bugs fixed here; all three made criteria pass when they should not.

    BUG B — a missing key was treated as PASS:
        val = frame.get(key)
        if val is None: continue
    Live instance: SAFE_MODE_HOLD requires `beacon_active`, which the emulator
    never emitted, so the only criterion that procedure has was silently
    skipped and SAFE_MODE_HOLD always "succeeded". A criterion that cannot be
    evaluated is not a criterion that passed — this now FAILS CLOSED.
    (`beacon_active` has been added to the emulator frame alongside this fix.)

    BUG C — '<=' and '>=' could never pass. `condition.startswith("<")` matched
    "<= 0.01", then `float(condition[1:])` parsed "= 0.01" and raised
    ValueError, which fell through to a string comparison that returned False.
    Operators are now parsed longest-first, so two-character operators are
    matched before their one-character prefixes.

    BUG D — a JSON boolean raised an uncaught AttributeError. `condition` may
    be a real bool (`{"beacon_active": true}` in JSON), and `True.startswith`
    is an AttributeError, which is not caught by `except (ValueError, TypeError)`
    — it propagated out of node_monitor_recovery and killed the recovery run.
    Booleans are now handled before any string parsing.
    """
    if not criteria:
        return True

    for key, condition in criteria.items():
        # BUG B: fail closed on a key the telemetry frame does not carry.
        if key not in frame:
            print(f"[Agent]    criterion '{key}' not present in telemetry "
                  f"frame — FAILING CLOSED")
            return False
        val = frame[key]
        if val is None:
            print(f"[Agent]    criterion '{key}' is None — FAILING CLOSED")
            return False

        # BUG D: booleans first — never reach .startswith() on a bool.
        if isinstance(condition, bool) or isinstance(val, bool):
            if isinstance(condition, str):
                want = condition.strip().lower() in ("true", "1", "yes", "on")
            else:
                want = bool(condition)
            if bool(val) is not want:
                return False
            continue

        if isinstance(condition, (int, float)):
            try:
                if float(val) != float(condition):
                    return False
            except (ValueError, TypeError):
                return False
            continue

        text = str(condition).strip()

        # BUG C: longest operators first, so "<=" is not shadowed by "<".
        for op in ("<=", ">=", "==", "!=", "<", ">"):
            if text.startswith(op):
                rhs = text[len(op):].strip()
                try:
                    lhs_f, rhs_f = float(val), float(rhs)
                except (ValueError, TypeError):
                    # Non-numeric operand: only equality is meaningful.
                    if op == "==":
                        if str(val) != rhs:
                            return False
                    elif op == "!=":
                        if str(val) == rhs:
                            return False
                    else:
                        print(f"[Agent]    criterion '{key}': cannot apply "
                              f"'{op}' to non-numeric {val!r} — FAILING CLOSED")
                        return False
                    break
                ok = {"<=": lhs_f <= rhs_f, ">=": lhs_f >= rhs_f,
                      "==": lhs_f == rhs_f, "!=": lhs_f != rhs_f,
                      "<":  lhs_f <  rhs_f, ">":  lhs_f >  rhs_f}[op]
                if not ok:
                    return False
                break
        else:
            # No operator: exact match, numeric if both sides parse.
            try:
                if float(val) != float(text):
                    return False
            except (ValueError, TypeError):
                if str(val) != text:
                    return False

    return True


def _persist_log(state: AgentState):
    """Improvement 1: Write recovery log to disk as JSON file."""
    try:
        # WIRING: created here rather than at import time, so importing the
        # agent has no filesystem side effects.
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts        = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        fault     = state.get("fault_type", "unknown")
        result    = "SUCCESS" if state.get("recovery_success") else "FAILURE"
        filename  = LOG_DIR / f"{ts}_{fault}_{result}.json"
        payload   = {
            "fault_type":        state.get("fault_type"),
            "fault_confidence":  state.get("fault_confidence"),
            "norad_id":          state.get("norad_id"),
            "catalog_baselines": state.get("catalog_baselines"),
            "procedure_used":    state.get("selected_procedure", {}).get("procedure_name"),
            "attempts":          state.get("attempt_count", 0) + 1,
            "success":           state.get("recovery_success"),
            "recovery_log":      state.get("recovery_log", []),
        }
        with open(filename, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        print(f"[Agent]    Recovery log saved: {filename.name}")
    except Exception as e:
        print(f"[Agent]    Log persistence failed: {e}")


def _print_summary(state: AgentState):
    print("\n[Agent] ── Recovery Summary ──────────────────")
    print(f"  Fault type:    {state['fault_type']}")
    print(f"  Confidence:    {state.get('fault_confidence', 1.0):.2f}")
    print(f"  Procedure:     {state.get('selected_procedure', {}).get('procedure_name', 'N/A')}")
    print(f"  Attempts:      {state['attempt_count'] + 1}")
    print(f"  Success:       {state['recovery_success']}")
    print(f"  Log entries:   {len(state['recovery_log'])}")
    print("[Agent] ───────────────────────────────────────\n")


# ──────────────────────────────────────────────
# Smoke test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=== DeadSat Recovery Agent — Smoke Test ===\n")
    from satellite_emulator import SatelliteEmulator
    emulator = SatelliteEmulator(tick_interval=0.5)
    emulator.start()
    time.sleep(1)

    emulator.inject_SEU("0x3F")
    time.sleep(1)
    frame = emulator.get_latest_frame()

    fault_report = {
        "fault_type":   "SEU",
        "fault_detail": frame["fault_detail"],
        "telemetry_frame": frame,
        "confidence":   0.95,
        "norad_id":     28654,
    }

    agent  = RecoveryAgent(emulator)
    result = agent.run(fault_report)
    print("\n=== Final Result ===")
    print(json.dumps(result, indent=2, default=str))
    emulator.stop()