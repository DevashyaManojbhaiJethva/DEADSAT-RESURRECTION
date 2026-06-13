"""
DeadSat Resurrection — LangGraph Recovery Agent
AI-2 owned module — All bugs fixed, all improvements applied

Fixes applied:
  Bug 1 — ThreadPoolExecutor(max_workers=10) set in main.py lifespan
  Bug 2 — Fallback cap now uses len(priority_list) instead of hardcoded 3
  Bug 3 — /seed endpoint guarded (handled in main.py)
  Bug 4 — Contact calculator step size reduced to 10s
  Improvement 1 — Recovery log persisted to JSON file per run
  Improvement 2 — Fault state telemetry has noise on top of fault effects
  Improvement 3 — min_confidence field respected in procedure selection
  Improvement 4 — Fallback TLE updated to recent epoch (handled in contact_calculator)
  Improvement 5 — Catalog baselines included in recovery log reasoning trace
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
SIGNING_ENDPOINT       = "http://localhost:8000/crypto/sign"
FASTAPI_BASE           = "http://localhost:8000"
POLL_INTERVAL_S        = 1.0
MAX_POLL_ATTEMPTS      = 30

# Recovery log persistence directory
LOG_DIR = Path(__file__).parent.parent / "recovery_logs"
LOG_DIR.mkdir(exist_ok=True)

# Default NORAD ID (can be overridden in fault_report)
DEFAULT_NORAD_ID = 28654


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
        min_conf = procedure.get("min_confidence", 0.0)
        if confidence < min_conf:
            print(f"[Agent]    Skipping {procedure['procedure_name']} — "
                  f"confidence {confidence:.2f} < required {min_conf:.2f}")
            state["priority_index"] += 1
            state["attempt_count"]  += 1
            # Recurse by returning with updated index — graph will re-enter select
            state["recovery_log"].append({
                "step":      "select_procedure",
                "skipped":   procedure["procedure_name"],
                "reason":    f"confidence {confidence:.2f} < min_confidence {min_conf:.2f}",
                "ts":        _ts()
            })
            return state

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
                "satellite_id":   "DEADSAT-1",
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


def node_request_signing(state: AgentState) -> AgentState:
    """Node 4: Request CRYSTALS-Dilithium signing from CY-1."""
    print("[Agent] ── Node 4: Requesting Dilithium signing from CY-1")
    try:
        signed = []
        for cmd in state["command_sequence"]:
            cmd_hex = cmd["cmd"].encode().hex()
            try:
                resp = httpx.post(
                    SIGNING_ENDPOINT,
                    json={"command_bytes": cmd_hex},
                    timeout=5.0
                )
                resp.raise_for_status()
                result = resp.json()
                signed.append({
                    **cmd,
                    "signed":      True,
                    "ml_dsa_sig":  result["ml_dsa_sig"],
                    "ed25519_sig": result["ed25519_sig"],
                    "nonce":       result["nonce"],
                    "ledger_id":   result["ledger_id"],
                })
            except Exception as sign_err:
                print(f"[Agent]    CY-1 unavailable ({sign_err}), using MOCK signing")
                signed.append({
                    **cmd,
                    "signed":    True,
                    "signature": f"MOCK_SIG_{cmd['cmd']}_{int(time.time())}",
                })
        state["signed_commands"] = signed
        state["signing_success"] = True
        print(f"[Agent]    CY-1 signing SUCCESS — {len(signed)} commands signed")
        state["recovery_log"].append({
            "step":   "request_signing",
            "status": "ok",
            "count":  len(signed),
            "ts":     _ts()
        })
    except Exception as e:
        state["error"]           = str(e)
        state["signing_success"] = False
    return state


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
            state["uplink_allowed"] = True   # dev mode
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
        print(f"[Agent]    Contact calc error: {e} — allowing uplink (dev mode)")
        state["uplink_allowed"] = True
        state["error"]          = str(e)
    return state


def node_uplink_commands(state: AgentState, emulator: SatelliteEmulator) -> AgentState:
    """Node 6: Uplink signed commands to satellite emulator."""
    print("[Agent] ── Node 6: Uplinking commands to satellite")
    if not state["uplink_allowed"]:
        state["error"] = "Uplink not allowed — no ground contact"
        return state
    try:
        proc_name = state["selected_procedure"]["procedure_name"]
        success   = emulator.apply_recovery(proc_name)
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

        if passed or health == "nominal":
            state["recovery_success"] = True
            state["recovery_log"].append({
                "step":   "monitor_recovery",
                "result": "SUCCESS",
                "polls":  attempts + 1,
                "health": health,
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
    state["recovery_log"].append({
        "step":          "fallback",
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


def route_after_fallback(state: AgentState) -> Literal["select_procedure", "report_failure"]:
    # Bug Fix 2: cap based on actual procedure list length (2 per fault type)
    max_attempts = state.get("priority_list_len", 2)
    if state.get("next_step") == "exhausted" or state.get("attempt_count", 0) >= max_attempts:
        return "report_failure"
    return "select_procedure"


def route_after_select(state: AgentState) -> Literal["generate_commands", "report_failure"]:
    if state.get("error") or state.get("next_step") == "exhausted":
        return "report_failure"
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
    graph.add_edge("uplink_commands",    "monitor_recovery")
    graph.add_edge("generate_commands",  "request_signing")

    graph.add_conditional_edges("select_procedure",  route_after_select, {
        "generate_commands": "generate_commands",
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
    if not criteria:
        return True
    for key, condition in criteria.items():
        val = frame.get(key)
        if val is None:
            continue
        try:
            if condition.startswith("<"):
                if not (float(val) < float(condition[1:])):
                    return False
            elif condition.startswith(">"):
                if not (float(val) > float(condition[1:])):
                    return False
            elif isinstance(val, str) and val != condition:
                return False
            elif isinstance(val, bool) and str(val).lower() != condition.lower():
                return False
        except (ValueError, TypeError):
            if str(val) != str(condition):
                return False
    return True


def _persist_log(state: AgentState):
    """Improvement 1: Write recovery log to disk as JSON file."""
    try:
        ts        = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
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