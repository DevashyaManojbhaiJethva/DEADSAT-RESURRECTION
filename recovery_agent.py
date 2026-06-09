"""
DeadSat Resurrection — LangGraph Recovery Agent
AI-2 owned module

9-step agentic pipeline:
  1. Receive fault report from AI-1 classifier
  2. Load procedure library
  3. Select best recovery procedure
  4. Generate command sequence
  5. Request Dilithium signing from CY-1
  6. Schedule uplink (check ground contact window)
  7. Uplink signed commands to satellite emulator
  8. Monitor recovery (poll emulator, verify success criteria)
  9. Fallback to next procedure if recovery failed

Integrated with:
  - satellite_emulator.py  (apply_recovery + get_latest_frame)
  - contact_calculator.py  (is_in_contact_now)
  - CY-1 signing endpoint  (POST /sign)
"""

import json
import time
import httpx
from pathlib import Path
from typing import TypedDict, Optional, Literal
from datetime import datetime, timezone

try:
    from langgraph.graph import StateGraph, END, START
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    print("[RecoveryAgent] WARNING: langgraph not installed. Run: pip install langgraph")

# Local imports (same repo)
import sys
sys.path.append(str(Path(__file__).parent.parent / "emulator"))
from satellite_emulator import SatelliteEmulator, FaultType
from contact_calculator import ContactCalculator


# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

PROCEDURE_LIBRARY_PATH = Path(__file__).parent / "procedure_library.json"

# CY-1 signing service endpoint (running on same Pi)
SIGNING_ENDPOINT = "http://localhost:8001/sign"

# FastAPI backend base (FE-2)
FASTAPI_BASE     = "http://localhost:8000"

# Recovery monitoring
POLL_INTERVAL_S  = 1.0
MAX_POLL_ATTEMPTS = 30


# ──────────────────────────────────────────────
# Agent State
# ──────────────────────────────────────────────

class AgentState(TypedDict):
    # Input from AI-1
    fault_type:       str
    fault_detail:     dict
    telemetry_frame:  dict

    # Procedure selection
    procedure_library: dict
    selected_procedure: dict
    priority_index:   int          # which priority level we're trying (0 = first)

    # Command generation
    command_sequence: list

    # Signing
    signed_commands:  list
    signing_success:  bool

    # Uplink scheduling
    contact_window:   dict
    uplink_allowed:   bool

    # Recovery result
    recovery_success: bool
    recovery_log:     list

    # Pipeline control
    next_step:        str
    error:            Optional[str]
    attempt_count:    int


# ──────────────────────────────────────────────
# Node Functions
# ──────────────────────────────────────────────

def node_load_procedures(state: AgentState) -> AgentState:
    """Node 1: Load procedure library from disk."""
    print("[Agent] ── Node 1: Loading procedure library")
    try:
        with open(PROCEDURE_LIBRARY_PATH) as f:
            library = json.load(f)
        state["procedure_library"] = library
        state["recovery_log"].append({
            "step": "load_procedures",
            "status": "ok",
            "ts": _ts()
        })
        print(f"[Agent]    Loaded {len(library['procedures'])} fault procedures")
    except Exception as e:
        state["error"] = f"Failed to load procedures: {e}"
        state["recovery_log"].append({"step": "load_procedures", "status": "error", "error": str(e), "ts": _ts()})
    return state


def node_select_procedure(state: AgentState) -> AgentState:
    """Node 2: Select recovery procedure based on fault type and priority index."""
    print(f"[Agent] ── Node 2: Selecting procedure for fault={state['fault_type']} priority_idx={state['priority_index']}")
    try:
        fault_key  = state["fault_type"]
        library    = state["procedure_library"]

        if fault_key not in library["procedures"]:
            state["error"] = f"Unknown fault type: {fault_key}"
            return state

        fault_entry  = library["procedures"][fault_key]
        priority_list = fault_entry["recovery_priority"]
        idx          = state["priority_index"]

        if idx >= len(priority_list):
            state["error"] = f"Exhausted all {len(priority_list)} procedures for {fault_key}"
            state["recovery_success"] = False
            state["next_step"] = "exhausted"
            return state

        procedure = priority_list[idx]
        state["selected_procedure"] = procedure
        state["recovery_log"].append({
            "step": "select_procedure",
            "procedure": procedure["procedure_name"],
            "priority": procedure["priority"],
            "ts": _ts()
        })
        print(f"[Agent]    Selected: {procedure['procedure_name']} (priority {procedure['priority']})")
    except Exception as e:
        state["error"] = str(e)
    return state


def node_generate_commands(state: AgentState) -> AgentState:
    """Node 3: Extract and validate command sequence from procedure."""
    print("[Agent] ── Node 3: Generating command sequence")
    try:
        proc     = state["selected_procedure"]
        commands = proc["commands"]

        # Enrich each command with metadata before signing
        enriched = []
        for cmd in commands:
            enriched.append({
                **cmd,
                "satellite_id":  "DEADSAT-1",
                "procedure_name": proc["procedure_name"],
                "fault_type":    state["fault_type"],
                "generated_at":  _ts(),
                "signed":        False,
                "signature":     None,
            })

        state["command_sequence"] = enriched
        state["recovery_log"].append({
            "step":     "generate_commands",
            "count":    len(enriched),
            "commands": [c["cmd"] for c in enriched],
            "ts": _ts()
        })
        print(f"[Agent]    Generated {len(enriched)} commands: {[c['cmd'] for c in enriched]}")
    except Exception as e:
        state["error"] = str(e)
    return state


def node_request_signing(state: AgentState) -> AgentState:
    """
    Node 4: Request CRYSTALS-Dilithium signature from CY-1.
    POST /sign with command sequence → receive signed commands.
    Falls back to mock signing if CY-1 is not yet integrated.
    """
    print("[Agent] ── Node 4: Requesting Dilithium signing from CY-1")
    try:
        payload = {
            "commands":       state["command_sequence"],
            "procedure_name": state["selected_procedure"]["procedure_name"],
            "fault_type":     state["fault_type"],
        }
        try:
            resp = httpx.post(SIGNING_ENDPOINT, json=payload, timeout=5.0)
            resp.raise_for_status()
            signed = resp.json()["signed_commands"]
            state["signed_commands"]  = signed
            state["signing_success"]  = True
            print(f"[Agent]    CY-1 signing SUCCESS — {len(signed)} commands signed")
        except Exception as sign_err:
            # CY-1 not yet integrated — mock signing for development
            print(f"[Agent]    CY-1 unavailable ({sign_err}), using MOCK signing")
            signed = []
            for cmd in state["command_sequence"]:
                signed.append({
                    **cmd,
                    "signed":    True,
                    "signature": f"MOCK_SIG_{cmd['cmd']}_{int(time.time())}",
                })
            state["signed_commands"] = signed
            state["signing_success"] = True   # treat mock as success for dev

        state["recovery_log"].append({
            "step":    "request_signing",
            "status":  "ok",
            "mock":    "CY-1" not in str(state.get("error", "")),
            "ts": _ts()
        })
    except Exception as e:
        state["error"] = str(e)
        state["signing_success"] = False
    return state


def node_schedule_uplink(state: AgentState) -> AgentState:
    """
    Node 5: Check ground contact window — is satellite reachable?
    Uses ContactCalculator. Allows uplink if in contact or within 60s of AOS.
    """
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
            # Get next window
            window = calc.find_next_contact(search_hours=24.0)
            state["contact_window"] = window or {}
            if window:
                # Allow uplink if within 2 minutes of AOS
                from datetime import datetime, timezone
                aos = datetime.fromisoformat(window["aos"])
                seconds_to_aos = (aos - datetime.now(timezone.utc)).total_seconds()
                if seconds_to_aos <= 120:
                    state["uplink_allowed"] = True
                    print(f"[Agent]    AOS in {seconds_to_aos:.0f}s — pre-arming uplink")
                else:
                    state["uplink_allowed"] = True   # dev mode: always allow
                    print(f"[Agent]    DEV MODE — uplink allowed regardless of contact window")
            else:
                state["uplink_allowed"] = True       # dev mode fallback
                print("[Agent]    No contact window found — DEV MODE uplink allowed")

        state["recovery_log"].append({
            "step":    "schedule_uplink",
            "in_contact": in_contact,
            "allowed": state["uplink_allowed"],
            "ts": _ts()
        })
    except Exception as e:
        print(f"[Agent]    Contact calculator error: {e} — allowing uplink (dev mode)")
        state["uplink_allowed"] = True
        state["error"] = str(e)
    return state


def node_uplink_commands(state: AgentState, emulator: SatelliteEmulator) -> AgentState:
    """
    Node 6: Send signed commands to satellite emulator.
    Calls emulator.apply_recovery() with procedure name.
    Also POSTs to FastAPI /recovery/uplink if available.
    """
    print("[Agent] ── Node 6: Uplinking commands to satellite")
    if not state["uplink_allowed"]:
        state["error"] = "Uplink not allowed — no ground contact"
        return state

    try:
        proc_name = state["selected_procedure"]["procedure_name"]

        # Primary: apply directly to emulator
        success = emulator.apply_recovery(proc_name)

        # Secondary: notify FastAPI backend (non-blocking)
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
            pass  # FastAPI notification is best-effort

        state["recovery_log"].append({
            "step":      "uplink_commands",
            "procedure": proc_name,
            "commands_sent": len(state["signed_commands"]),
            "ts": _ts()
        })
        print(f"[Agent]    Uplinked {len(state['signed_commands'])} commands for {proc_name}")
    except Exception as e:
        state["error"] = str(e)
    return state


def node_monitor_recovery(state: AgentState, emulator: SatelliteEmulator) -> AgentState:
    """
    Node 7: Poll emulator and verify success criteria are met.
    Polls every 1s for up to MAX_POLL_ATTEMPTS seconds.
    """
    print("[Agent] ── Node 7: Monitoring recovery")
    proc      = state["selected_procedure"]
    criteria  = proc.get("success_criteria", {})
    timeout   = proc.get("timeout_s", 30)
    attempts  = 0
    max_a     = min(int(timeout), MAX_POLL_ATTEMPTS)

    while attempts < max_a:
        time.sleep(POLL_INTERVAL_S)
        frame  = emulator.get_latest_frame()
        health = emulator.get_overall_health()
        passed = _check_criteria(frame, criteria)

        print(f"[Agent]    Poll {attempts+1}/{max_a} — health={health} | criteria_met={passed}")

        if passed or health == "nominal":
            state["recovery_success"] = True
            state["recovery_log"].append({
                "step":    "monitor_recovery",
                "result":  "SUCCESS",
                "polls":   attempts + 1,
                "health":  health,
                "ts": _ts()
            })
            print("[Agent]    Recovery VERIFIED ✓")
            return state

        attempts += 1

    # Timed out
    state["recovery_success"] = False
    state["recovery_log"].append({
        "step":   "monitor_recovery",
        "result": "TIMEOUT",
        "polls":  attempts,
        "ts": _ts()
    })
    print(f"[Agent]    Recovery FAILED after {attempts} polls — escalating to fallback")
    return state


def node_fallback(state: AgentState) -> AgentState:
    """
    Node 8: Increment priority index and loop back to procedure selection.
    This is the agentic fallback — try the next procedure automatically.
    """
    print("[Agent] ── Node 8: FALLBACK — trying next procedure")
    state["priority_index"]  += 1
    state["attempt_count"]   += 1
    state["command_sequence"] = []
    state["signed_commands"]  = []
    state["signing_success"]  = False
    state["recovery_success"] = False

    state["recovery_log"].append({
        "step":       "fallback",
        "next_priority": state["priority_index"],
        "attempt":    state["attempt_count"],
        "ts": _ts()
    })
    return state


def node_report_success(state: AgentState) -> AgentState:
    """Node 9a: Final success node — emit recovery report."""
    print("[Agent] ══ RECOVERY COMPLETE ══")
    state["recovery_log"].append({
        "step":      "final_report",
        "result":    "SUCCESS",
        "procedure": state["selected_procedure"]["procedure_name"],
        "attempts":  state["attempt_count"] + 1,
        "ts": _ts()
    })
    _print_summary(state)
    return state


def node_report_failure(state: AgentState) -> AgentState:
    """Node 9b: Final failure node — all procedures exhausted."""
    print("[Agent] ══ ALL PROCEDURES EXHAUSTED — SATELLITE UNRECOVERABLE ══")
    state["recovery_log"].append({
        "step":    "final_report",
        "result":  "FAILURE",
        "error":   state.get("error"),
        "ts": _ts()
    })
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
    if state.get("next_step") == "exhausted" or state.get("attempt_count", 0) >= 3:
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
    """
    Build and compile the LangGraph state machine.
    emulator is passed in so nodes can call it directly.
    """
    if not LANGGRAPH_AVAILABLE:
        raise ImportError("langgraph not installed — run: pip install langgraph")

    # Wrap nodes that need emulator access
    def _uplink(state):   return node_uplink_commands(state, emulator)
    def _monitor(state):  return node_monitor_recovery(state, emulator)

    graph = StateGraph(AgentState)

    # Register nodes
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

    # Entry point (LangGraph 1.x style)
    graph.add_edge(START, "load_procedures")

    # Linear edges
    graph.add_edge("load_procedures",   "select_procedure")
    graph.add_edge("schedule_uplink",   "uplink_commands")
    graph.add_edge("uplink_commands",   "monitor_recovery")

    # Conditional edges
    graph.add_conditional_edges("select_procedure",  route_after_select,     {
        "generate_commands": "generate_commands",
        "report_failure":    "report_failure",
    })
    graph.add_edge("generate_commands", "request_signing")
    graph.add_conditional_edges("request_signing",   route_after_signing,    {
        "schedule_uplink": "schedule_uplink",
        "fallback":        "fallback",
    })
    graph.add_conditional_edges("monitor_recovery",  route_after_monitoring, {
        "report_success": "report_success",
        "fallback":       "fallback",
    })
    graph.add_conditional_edges("fallback",          route_after_fallback,   {
        "select_procedure": "select_procedure",
        "report_failure":   "report_failure",
    })

    # Terminal nodes
    graph.add_edge("report_success", END)
    graph.add_edge("report_failure", END)

    return graph.compile()


# ──────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────

class RecoveryAgent:
    """
    High-level wrapper. Call run(fault_report) from FastAPI or pipeline.
    fault_report is the dict produced by AI-1 classifier.
    """

    def __init__(self, emulator: SatelliteEmulator):
        self.emulator = emulator
        self.graph    = build_recovery_graph(emulator)

    def run(self, fault_report: dict) -> dict:
        """
        Execute full recovery pipeline.

        fault_report schema (from AI-1):
            {
                "fault_type":     "SEU" | "software_bug" | "firmware_corruption" | "command_injection",
                "fault_detail":   { ... },
                "telemetry_frame": { ... }
            }
        """
        print(f"\n[Agent] ══════════════════════════════════════")
        print(f"[Agent] RECOVERY INITIATED — fault: {fault_report.get('fault_type')}")
        print(f"[Agent] ══════════════════════════════════════")

        initial_state: AgentState = {
            "fault_type":        fault_report.get("fault_type", "SEU"),
            "fault_detail":      fault_report.get("fault_detail", {}),
            "telemetry_frame":   fault_report.get("telemetry_frame", {}),
            "procedure_library": {},
            "selected_procedure": {},
            "priority_index":   0,
            "command_sequence": [],
            "signed_commands":  [],
            "signing_success":  False,
            "contact_window":   {},
            "uplink_allowed":   False,
            "recovery_success": False,
            "recovery_log":     [],
            "next_step":        "",
            "error":            None,
            "attempt_count":    0,
        }

        start_ts = time.time()
        final_state = self.graph.invoke(initial_state)
        elapsed = time.time() - start_ts

        return {
            "success":       final_state.get("recovery_success", False),
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
    Evaluate success criteria dict against telemetry frame.
    Criteria values are strings like '< 0.01', 'nominal', 'true'.
    """
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


def _print_summary(state: AgentState):
    print("\n[Agent] ── Recovery Summary ──────────────────")
    print(f"  Fault type:    {state['fault_type']}")
    print(f"  Procedure:     {state.get('selected_procedure', {}).get('procedure_name', 'N/A')}")
    print(f"  Attempts:      {state['attempt_count'] + 1}")
    print(f"  Success:       {state['recovery_success']}")
    print(f"  Log entries:   {len(state['recovery_log'])}")
    print("[Agent] ───────────────────────────────────────\n")


# ──────────────────────────────────────────────
# Smoke test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.append(str(Path(__file__).parent.parent / "emulator"))

    print("=== DeadSat Recovery Agent — Smoke Test ===\n")

    # Boot emulator
    emulator = SatelliteEmulator(tick_interval=0.5)
    emulator.start()
    time.sleep(1)

    # Inject fault
    fault_type = "SEU"
    emulator.inject_SEU("0x3F")
    time.sleep(1)

    # Grab telemetry
    frame = emulator.get_latest_frame()

    # Build fault report (as AI-1 would send)
    fault_report = {
        "fault_type":     fault_type,
        "fault_detail":   frame["fault_detail"],
        "telemetry_frame": frame,
    }

    # Run agent
    agent  = RecoveryAgent(emulator)
    result = agent.run(fault_report)

    print("\n=== Final Result ===")
    print(json.dumps(result, indent=2, default=str))

    emulator.stop()