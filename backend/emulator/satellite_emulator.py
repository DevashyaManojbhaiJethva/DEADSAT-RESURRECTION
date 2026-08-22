"""
DeadSat Resurrection — Satellite Emulator
AI-2 owned module

State machine modelling OBC, ADCS, Power, Comms subsystems.
Streams realistic telemetry frames every second.
Accepts fault injection via inject_* methods.
FastAPI polls get_latest_frame() for current telemetry.
WebSocket push via get_frame_history() ring buffer (60 frames minimum).
"""

import time
import random
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# ──────────────────────────────────────────────
# Fault Types
# ──────────────────────────────────────────────

class FaultType(str, Enum):
    NONE                = "none"
    SEU                 = "SEU"
    SOFTWARE_BUG        = "software_bug"
    FIRMWARE_CORRUPTION = "firmware_corruption"
    COMMAND_INJECTION   = "command_injection"
    # WIRING: the operator UI has always offered five faults while the emulator
    # modelled four, so api.ts mapped battery_fail -> firmware_corruption and
    # adcs_fail -> SEU. Selecting either produced a diagnosis contradicting the
    # label the operator picked, and the code admitted it in a comment rather
    # than fixing it. Both are now first-class.
    #
    # NOTE these two are deliberately absent from AI-1's FAULT_LABELS. AI-1
    # classifies from ORBITAL ELEMENTS (mean motion, eccentricity, BSTAR, TLE
    # age...). Battery state and reaction-wheel health leave no signature in a
    # TLE, so no amount of training would let it name them. They are injected
    # and recovered with the fault type known up front — see
    # PIPELINE_SKIP_CLASSIFIER_FAULTS in pipeline.py.
    BATTERY_FAILURE     = "battery_failure"
    ADCS_FAILURE        = "adcs_failure"


# ──────────────────────────────────────────────
# Procedure applicability
# ──────────────────────────────────────────────
#
# WIRING: which faults each recovery procedure can actually remedy.
#
# Built from agents/procedure_library.json — the same file the AI-2 agent
# selects procedures from — so the emulator and the agent cannot disagree
# about what a procedure is for. A second hardcoded copy would drift the first
# time a procedure was added to the library.
#
# The fallback below is used only if the library cannot be read (it lives in a
# sibling directory that main.py adds to sys.path, so an import-order change
# should degrade rather than crash). It is the JSON's contents as of
# 2026-08-15 and is checked against the library by test_units.py.

_APPLICABILITY_FALLBACK: dict = {
    "ADCS_MEMORY_SCRUB_v2": {FaultType.SEU},
    "OBC_SOFT_REBOOT_v1":   {FaultType.SEU, FaultType.SOFTWARE_BUG},
    "OBC_HARD_RESET_v1":    {FaultType.SOFTWARE_BUG},
    "FIRMWARE_ROLLBACK_v1": {FaultType.FIRMWARE_CORRUPTION},
    "SAFE_MODE_HOLD":       {FaultType.FIRMWARE_CORRUPTION},
    "LOCKDOWN_REGEN_v1":    {FaultType.COMMAND_INJECTION},
    "COMMS_HARD_RESET_v1":  {FaultType.COMMAND_INJECTION},
}


def _load_procedure_applicability() -> dict:
    """Invert procedure_library.json: procedure_name -> {FaultType, ...}."""
    import json
    from pathlib import Path

    lib = Path(__file__).resolve().parent.parent / "agents" / "procedure_library.json"
    try:
        procedures = json.loads(lib.read_text(encoding="utf-8"))["procedures"]
    except Exception as exc:  # pragma: no cover - depends on deployment layout
        print(f"[Emulator] procedure_library.json unreadable ({exc}) — "
              f"using built-in applicability map")
        return dict(_APPLICABILITY_FALLBACK)

    mapping: dict = {}
    for fault_key, spec in procedures.items():
        try:
            fault = FaultType(fault_key)
        except ValueError:
            print(f"[Emulator] procedure_library.json has unknown fault key "
                  f"'{fault_key}' — ignored")
            continue
        for proc in spec.get("recovery_priority", []):
            name = proc.get("procedure_name") if isinstance(proc, dict) else proc
            if name:
                mapping.setdefault(name, set()).add(fault)

    return mapping or dict(_APPLICABILITY_FALLBACK)


PROCEDURE_APPLICABILITY: dict = _load_procedure_applicability()


# ──────────────────────────────────────────────
# Subsystem State Dataclasses
# ──────────────────────────────────────────────

@dataclass
class OBCState:
    register: str           = "0x3F"
    temp_c: float           = 47.2
    error_count: int        = 0
    cpu_usage_pct: float    = 18.5
    memory_usage_pct: float = 34.2
    status: str             = "nominal"   # nominal | degraded | fault


@dataclass
class ADCSState:
    rate_deg_s: float           = 0.003
    quaternion: list            = field(default_factory=lambda: [0.1, 0.2, 0.3, 0.9])
    reaction_wheel_rpm: float   = 4800.0
    pointing_error_deg: float   = 0.001
    status: str                 = "nominal"


@dataclass
class PowerState:
    solar_output_w: float = 82.4
    battery_pct: float    = 91.2
    bus_voltage_v: float  = 28.1
    charging: bool        = True
    status: str           = "nominal"


@dataclass
class CommsState:
    uplink_active: bool        = True
    downlink_active: bool      = True
    signal_strength_dbm: float = -78.3
    last_cmd_timestamp: int    = 0
    status: str                = "nominal"
    #: Low-rate health beacon. SAFE_MODE_HOLD's only success criterion is
    #: `beacon_active: true`, and the emulator never emitted this field — so
    #: _check_criteria() skipped it (its missing-key branch treated absent as
    #: pass) and SAFE_MODE_HOLD always "succeeded" without proving anything.
    #: Now that criteria fail closed on a missing key, the field must exist.
    #: A real spacecraft keeps the beacon up in safe mode; that is the point
    #: of safe mode.
    beacon_active: bool        = True


# ──────────────────────────────────────────────
# Ring Buffer
# ──────────────────────────────────────────────

RING_BUFFER_SIZE = 120   # store 2 minutes, AI-1 needs last 60 frames


# ──────────────────────────────────────────────
# Telemetry bounds
# ──────────────────────────────────────────────
#
# WIRING: solar_output_w, bus_voltage_v and reaction_wheel_rpm were unbounded
# random walks — only battery_pct was clamped. Measured over 5000 ticks the
# drift carried power_w to 192 W in one run and (as reported) 46.6 W in
# another. Since LOCKDOWN_REGEN_v1's success criterion is `power_w > 75`, a
# demo left running long enough failed on its own with no fault injected.
#
# Two bands per field:
#
#   NOMINAL_BANDS  — where a healthy satellite is held. Deliberately tight, and
#                    the power floor sits above the 75 W criterion with margin
#                    so nominal drift can never break a recovery check.
#   PHYSICAL_LIMITS — hard survivable range. Used while a fault is active, so
#                    fault effects can push telemetry far out of nominal
#                    without becoming physically absurd, and so drift noise
#                    cannot quietly undo a fault by clamping it back.
#
# Every field written by _update_nominal_drift() or _apply_fault_effects()
# appears in PHYSICAL_LIMITS. test_units.py asserts that over 5000 ticks, in
# every fault state, no frame value leaves these bounds.

# Every band here that a success_criterion also constrains is deliberately
# TIGHTER than that criterion, so nominal drift can never walk a recovered
# satellite back across a threshold and fail a check that already passed.
# The at-risk pairs (verified against procedure_library.json):
#     adcs_rate_deg_s       "< 0.01"  -> band tops out at 0.009
#     adcs_pointing_err_deg "< 0.01"  -> band tops out at 0.008
#     obc_memory_pct        "< 60"    -> band tops out at 55
#     obc_cpu_pct           "< 50"    -> band tops out at 40
#     power_w               "> 75"    -> band floors at 78
# test_units.py re-derives this check from the library, so adding a procedure
# with a threshold inside a band fails the suite rather than the demo.
NOMINAL_BANDS: dict = {
    "obc_temp_c":            (35.0,  65.0),
    "obc_cpu_pct":           (5.0,   40.0),
    "obc_memory_pct":        (20.0,  55.0),
    "adcs_rate_deg_s":       (0.0,   0.009),
    "adcs_pointing_err_deg": (0.0,   0.008),
    "adcs_wheel_rpm":        (4600.0, 5000.0),
    "power_w":               (78.0,  90.0),   # floor > the 75 W criterion
    "battery_pct":           (70.0,  100.0),
    "bus_voltage_v":         (27.5,  28.6),
    "signal_strength_dbm":   (-95.0, -60.0),
}

PHYSICAL_LIMITS: dict = {
    "obc_temp_c":            (-40.0, 125.0),   # OBC survival range
    "obc_cpu_pct":           (0.0,   100.0),
    "obc_memory_pct":        (0.0,   100.0),
    "obc_error_count":       (0,     9999),    # counter saturates, never grows forever
    "adcs_rate_deg_s":       (0.0,   30.0),    # a tumbling spacecraft
    "adcs_pointing_err_deg": (0.0,   180.0),   # cannot be more than fully inverted
    "adcs_wheel_rpm":        (-6000.0, 6000.0),
    "power_w":               (0.0,   120.0),
    "battery_pct":           (0.0,   100.0),
    "bus_voltage_v":         (18.0,  34.0),
    "signal_strength_dbm":   (-130.0, -40.0),
}


def _clamp(value, bounds_key: str, faulted: bool):
    """Clamp to the nominal band when healthy, to survivable limits when not."""
    table = PHYSICAL_LIMITS if faulted else NOMINAL_BANDS
    lo, hi = table.get(bounds_key) or PHYSICAL_LIMITS[bounds_key]
    return max(lo, min(hi, value))


# ──────────────────────────────────────────────
# Main Emulator
# ──────────────────────────────────────────────

class SatelliteEmulator:
    """
    Satellite state machine.
    Call start() to begin background telemetry ticking.
    Call get_latest_frame() from FastAPI poll endpoint.
    Call get_frame_history(n) for AI-1 classifier sliding window — real ring buffer.
    Call inject_* methods to simulate faults.
    Call apply_recovery() to restore nominal after agent uplinks fix.
    """

    def __init__(self, tick_interval: float = 1.0, norad_id: int = 28654):
        self.tick_interval  = tick_interval
        self.norad_id       = norad_id          # PIPELINE: identifies which satellite is emulated
        self.obc            = OBCState()
        self.adcs           = ADCSState()
        self.power          = PowerState()
        self.comms          = CommsState()
        self.fault_injected: Optional[FaultType] = None
        self.fault_detail: dict = {}
        self._lock          = threading.Lock()
        self._running       = False
        self._frame_count   = 0
        self._latest_frame: dict = {}
        self._ring_buffer: deque = deque(maxlen=RING_BUFFER_SIZE)  # FIX 1 & 6: real ring buffer, 60+ frames
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ──────────────────────────────

    def start(self):
        """
        Start the background tick thread. Idempotent.

        WIRING: this used to overwrite self._thread unconditionally. A second
        start() left the first thread ticking with no reference to it, so
        stop() could only ever join the most recent one — the orphan kept
        mutating shared state and appending to the ring buffer forever.
        Measured: two live threads after two start() calls, and the first still
        running after stop().

        This is reachable in practice: main.py constructs the emulator at module
        scope and starts it in the lifespan handler, and `python main.py`
        executes the module twice (once as __main__, once as "main" when uvicorn
        imports it — see Prompt 5.2 A).
        """
        if self._running and self._thread and self._thread.is_alive():
            print("[Emulator] Already running — start() ignored")
            return
        self._running = True
        self._thread  = threading.Thread(target=self._tick_loop, daemon=True)
        self._thread.start()
        print(f"[Emulator] Started — streaming telemetry every {self.tick_interval}s")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        print("[Emulator] Stopped")

    # ── Tick Loop ─────────────────────────────

    def _tick_loop(self):
        while self._running:
            with self._lock:
                self._update_nominal_drift()
                self._apply_fault_effects()
                self._latest_frame = self._build_frame()
                self._ring_buffer.append(dict(self._latest_frame))  # push to real ring buffer
                self._frame_count += 1
            time.sleep(self.tick_interval)

    def _update_nominal_drift(self):
        """
        Add small realistic sensor noise to telemetry each tick.

        WIRING: this used to be wrapped in

            if self.fault_injected in (None, FaultType.NONE):

        so the moment any fault was injected EVERY subsystem froze — measured:
        1 distinct obc_temp_c and 1 distinct signal_strength_dbm across 30
        ticks. That directly contradicted the module's own "Improvement 2 —
        fault state telemetry has noise on top of fault effects", and made
        faulted telemetry trivially identifiable by its total absence of
        sensor noise.

        Noise now runs in every state. It cannot undo a fault, because while a
        fault is active values are clamped to PHYSICAL_LIMITS rather than the
        tight NOMINAL_BANDS, and _apply_fault_effects() runs immediately after
        this and re-asserts the fault's own values. Drift amplitude is small
        relative to every fault's per-tick effect.

        Unbounded walks are also gone: solar_output_w, bus_voltage_v and
        reaction_wheel_rpm are clamped like battery_pct always was.
        """
        faulted = self.fault_injected not in (None, FaultType.NONE)

        self.obc.temp_c        = _clamp(self.obc.temp_c + random.uniform(-0.3, 0.3),
                                        "obc_temp_c", faulted)
        self.obc.cpu_usage_pct = _clamp(self.obc.cpu_usage_pct + random.uniform(-1.0, 1.0),
                                        "obc_cpu_pct", faulted)
        self.obc.memory_usage_pct = _clamp(
            self.obc.memory_usage_pct + random.uniform(-0.5, 0.5),
            "obc_memory_pct", faulted)

        self.adcs.rate_deg_s = _clamp(self.adcs.rate_deg_s + random.uniform(-0.001, 0.001),
                                      "adcs_rate_deg_s", faulted)
        self.adcs.pointing_error_deg = _clamp(
            self.adcs.pointing_error_deg + random.uniform(-0.0005, 0.0005),
            "adcs_pointing_err_deg", faulted)
        self.adcs.reaction_wheel_rpm = _clamp(
            self.adcs.reaction_wheel_rpm + random.uniform(-5, 5),
            "adcs_wheel_rpm", faulted)

        self.power.solar_output_w = _clamp(
            self.power.solar_output_w + random.uniform(-1.0, 1.0), "power_w", faulted)
        self.power.battery_pct    = _clamp(
            self.power.battery_pct + random.uniform(-0.05, 0.1), "battery_pct", faulted)
        self.power.bus_voltage_v  = _clamp(
            self.power.bus_voltage_v + random.uniform(-0.05, 0.05),
            "bus_voltage_v", faulted)

        self.comms.signal_strength_dbm = _clamp(
            self.comms.signal_strength_dbm + random.uniform(-1.0, 1.0),
            "signal_strength_dbm", faulted)

    def _apply_fault_effects(self):
        """
        Progress fault symptoms each tick once a fault is active.

        WIRING: every additive effect below now saturates at a physical
        ceiling. Previously they added to the same field on every tick with no
        cap — measured for an SEU: adcs_rate_deg_s reached 15.4 deg/s after
        150 ticks (nominal is < 0.01), pointing error 44.9 deg, and
        obc_error_count grew without limit for as long as the process ran.
        A fault is a condition to be recovered from, not an unbounded ramp.
        """
        if self.fault_injected == FaultType.SEU:
            self.adcs.rate_deg_s = _clamp(
                self.adcs.rate_deg_s + random.uniform(0.05, 0.15),
                "adcs_rate_deg_s", True)
            self.adcs.pointing_error_deg = _clamp(
                self.adcs.pointing_error_deg + random.uniform(0.1, 0.5),
                "adcs_pointing_err_deg", True)
            self.adcs.status             = "fault"
            self.obc.error_count         = min(PHYSICAL_LIMITS["obc_error_count"][1],
                                               self.obc.error_count + 1)
            self.obc.status              = "degraded"

        elif self.fault_injected == FaultType.SOFTWARE_BUG:
            self.obc.cpu_usage_pct      = min(100.0, self.obc.cpu_usage_pct + random.uniform(5, 15))
            self.obc.memory_usage_pct   = min(100.0, self.obc.memory_usage_pct + random.uniform(3, 8))
            self.obc.error_count        = min(PHYSICAL_LIMITS["obc_error_count"][1],
                                              self.obc.error_count + random.randint(1, 5))
            self.obc.status              = "fault"
            self.comms.downlink_active   = False
            self.comms.status            = "degraded"

        elif self.fault_injected == FaultType.FIRMWARE_CORRUPTION:
            self.obc.status              = "fault"
            self.adcs.status             = "degraded"
            self.power.bus_voltage_v     = _clamp(
                self.power.bus_voltage_v - random.uniform(0.05, 0.2),
                "bus_voltage_v", True)
            self.power.status            = "degraded"
            self.comms.uplink_active     = False
            self.comms.downlink_active   = False
            self.comms.status            = "fault"
            # A corrupted firmware image takes the beacon down with the rest of
            # comms. Without this, beacon_active would be True for the whole run
            # and SAFE_MODE_HOLD's only success criterion would pass trivially —
            # as weak as the missing-key skip it replaces. SAFE_MODE_HOLD
            # restores it, which is the point of safe mode.
            self.comms.beacon_active     = False

        elif self.fault_injected == FaultType.COMMAND_INJECTION:
            self.comms.status            = "fault"
            self.obc.error_count         = min(PHYSICAL_LIMITS["obc_error_count"][1],
                                               self.obc.error_count + 1)
            self.power.solar_output_w    = _clamp(
                self.power.solar_output_w - random.uniform(2, 5), "power_w", True)

        elif self.fault_injected == FaultType.BATTERY_FAILURE:
            # Capacity collapse: charge drains and the bus sags with it. Both
            # saturate at their physical floors rather than running away —
            # same discipline as every other fault effect (Prompt 3.3).
            self.power.battery_pct    = _clamp(
                self.power.battery_pct - random.uniform(0.4, 1.2), "battery_pct", True)
            self.power.bus_voltage_v  = _clamp(
                self.power.bus_voltage_v - random.uniform(0.1, 0.3), "bus_voltage_v", True)
            self.power.charging       = False
            self.power.status         = "fault"
            # An undervolting bus eventually browns out the OBC.
            if self.power.bus_voltage_v < 24.0:
                self.obc.status       = "degraded"

        elif self.fault_injected == FaultType.ADCS_FAILURE:
            # Dead actuator: the wheel is stopped and cannot desaturate, so
            # body rate and pointing error grow. Note obc.error_count does NOT
            # increase — that is what separates this from an SEU.
            self.adcs.reaction_wheel_rpm = 0.0
            self.adcs.rate_deg_s         = _clamp(
                self.adcs.rate_deg_s + random.uniform(0.02, 0.08),
                "adcs_rate_deg_s", True)
            self.adcs.pointing_error_deg = _clamp(
                self.adcs.pointing_error_deg + random.uniform(0.2, 0.8),
                "adcs_pointing_err_deg", True)
            self.adcs.status             = "fault"

    def _build_frame(self) -> dict:
        """Build the canonical JSON telemetry frame shared with all team members."""
        return {
            "timestamp":             int(time.time()),
            "frame_id":              self._frame_count,
            "norad_id":              self.norad_id,

            # OBC
            "obc_register":          self.obc.register,
            "obc_temp_c":            round(self.obc.temp_c, 2),
            "obc_error_count":       self.obc.error_count,
            "obc_cpu_pct":           round(self.obc.cpu_usage_pct, 1),
            "obc_memory_pct":        round(self.obc.memory_usage_pct, 1),
            "obc_status":            self.obc.status,

            # ADCS
            "adcs_rate_deg_s":       round(self.adcs.rate_deg_s, 5),
            "adcs_quaternion":       [round(q, 4) for q in self.adcs.quaternion],
            "adcs_wheel_rpm":        round(self.adcs.reaction_wheel_rpm, 1),
            "adcs_pointing_err_deg": round(self.adcs.pointing_error_deg, 4),
            "adcs_status":           self.adcs.status,

            # Power
            "power_w":               round(self.power.solar_output_w, 2),
            "battery_pct":           round(self.power.battery_pct, 2),
            "bus_voltage_v":         round(self.power.bus_voltage_v, 3),
            "power_charging":        self.power.charging,
            "power_status":          self.power.status,

            # Comms
            "comms_uplink":          self.comms.uplink_active,
            "comms_downlink":        self.comms.downlink_active,
            "signal_strength_dbm":   round(self.comms.signal_strength_dbm, 2),
            "comms_status":          self.comms.status,
            "beacon_active":         self.comms.beacon_active,

            # Fault
            "fault_injected":        self.fault_injected.value if self.fault_injected else None,
            "fault_detail":          self.fault_detail,
        }

    # ── FastAPI Poll + WebSocket Interface ─────

    def get_latest_frame(self) -> dict:
        """Called by FastAPI GET /telemetry to return current state."""
        with self._lock:
            return dict(self._latest_frame)

    def get_frame_history(self, last_n: int = 60) -> list:
        """
        FIX 1 & 6: Real ring buffer — returns last N frames (min 60).
        AI-1 classifier calls this for its 60-frame sliding window.
        WebSocket /ws/telemetry also uses this for chart history on connect.
        """
        with self._lock:
            frames = list(self._ring_buffer)
            return frames[-last_n:] if len(frames) >= last_n else frames

    # ── Fault Injection ───────────────────────

    def inject_SEU(self, register: str = "0x3F"):
        """Single Event Upset — cosmic ray flips a bit in OBC register."""
        with self._lock:
            self.fault_injected          = FaultType.SEU
            self.obc.register            = register
            self.obc.error_count        += 1
            self.adcs.rate_deg_s         = 0.45
            self.adcs.pointing_error_deg = 2.3
            self.adcs.status             = "fault"
            self.fault_detail            = {
                "register":    register,
                "bit_flipped": 3,
                "subsystem":   "ADCS",
            }
        print(f"[Emulator] FAULT INJECTED: SEU on register {register}")

    def inject_software_bug(self):
        """Memory pointer corruption — OBC enters crash loop."""
        with self._lock:
            self.fault_injected         = FaultType.SOFTWARE_BUG
            self.obc.cpu_usage_pct      = 95.0
            self.obc.memory_usage_pct   = 88.0
            self.obc.error_count       += 10
            self.obc.status             = "fault"
            self.comms.downlink_active  = False
            self.fault_detail           = {
                "subsystem":   "OBC",
                "crash_type":  "memory_pointer_corruption",
            }
        print("[Emulator] FAULT INJECTED: Software Bug — OBC crash loop")

    def inject_firmware_corruption(self):
        """Firmware image corrupted — all subsystems degrading."""
        with self._lock:
            self.fault_injected         = FaultType.FIRMWARE_CORRUPTION
            self.obc.status             = "fault"
            self.adcs.status            = "degraded"
            self.comms.uplink_active    = False
            self.comms.downlink_active  = False
            self.comms.status           = "fault"
            self.fault_detail           = {
                "subsystem":          "firmware",
                "checksum_mismatch":  True,
            }
        print("[Emulator] FAULT INJECTED: Firmware Corruption")

    def inject_command(self, payload: str = "ROGUE_CMD_0xDEAD"):
        """Unsigned malicious command injected to comms channel."""
        with self._lock:
            self.fault_injected = FaultType.COMMAND_INJECTION
            self.comms.status   = "fault"
            self.fault_detail   = {
                "subsystem": "comms",
                "payload":   payload,
                "signed":    False,
            }
        print(f"[Emulator] FAULT INJECTED: Rogue Command → {payload}")

    def inject_battery_failure(self, cell: str = "CELL_3"):
        """
        Battery cell failure — capacity collapses and the bus browns out.

        Distinct from firmware_corruption (which degrades the whole platform):
        this is a power-subsystem hardware fault. OBC, ADCS and comms stay
        nominal until the bus voltage sags, which is what makes it separable
        from the fault it used to be mapped onto.
        """
        with self._lock:
            self.fault_injected      = FaultType.BATTERY_FAILURE
            self.power.charging      = False
            self.power.status        = "fault"
            self.fault_detail        = {
                "subsystem": "power",
                "cell":      cell,
                "symptom":   "capacity collapse, bus undervoltage",
            }
        print(f"[Emulator] FAULT INJECTED: Battery Failure → {cell}")

    def inject_adcs_failure(self, wheel: str = "RW_Y"):
        """
        Reaction-wheel failure — attitude control is lost mechanically.

        Distinct from an SEU: an SEU is a transient bit-flip in the OBC's
        state vector that a memory scrub clears. This is a dead actuator, so
        the wheel speed decays to zero and pointing error grows without the
        OBC reporting any error count.
        """
        with self._lock:
            self.fault_injected           = FaultType.ADCS_FAILURE
            self.adcs.status              = "fault"
            self.adcs.reaction_wheel_rpm  = 0.0
            self.fault_detail             = {
                "subsystem": "adcs",
                "wheel":     wheel,
                "symptom":   "reaction wheel stalled, attitude drifting",
            }
        print(f"[Emulator] FAULT INJECTED: ADCS Actuator Failure → {wheel}")

    def inject_command_injection(self, payload: str = "ROGUE_CMD_0xDEAD"):
        """
        PIPELINE ALIAS for inject_command().

        pipeline.py and the model-pipeline test suite name the four injectors
        after the procedure_library.json keys:
            SEU / software_bug / firmware_corruption / command_injection
        This alias keeps that naming symmetric without breaking existing
        callers of inject_command().
        """
        return self.inject_command(payload)

    # ── Recovery ──────────────────────────────

    def apply_recovery(self, procedure_name: str) -> bool:
        """
        Called by LangGraph agent after signed command uplinked.
        FIX 8: Added OBC_HARD_RESET_v1 and COMMS_HARD_RESET_v1 handlers.

        WIRING: a procedure now only applies to the faults it can actually
        remedy. This method used to end with an unconditional

            self.fault_injected = FaultType.NONE
            print("[Emulator] Recovery SUCCESS — satellite nominal")
            return True

        for ANY recognised procedure name. Reproduced: inject_SEU() then
        apply_recovery("LOCKDOWN_REGEN_v1") — a comms lockdown, which touches
        neither the OBC register nor the ADCS — returned True, printed
        "Recovery SUCCESS", and cleared fault_injected while adcs_status was
        still "fault".

        That made the whole recovery loop untestable: any procedure "worked",
        so success_criteria never had to be met, the fallback path could never
        be reached, and a wrong diagnosis from AI-1 was indistinguishable from
        a right one. The applicability check is what makes a wrong procedure
        observable.
        """
        with self._lock:
            print(f"[Emulator] Applying recovery procedure: {procedure_name}")

            # ── Applicability gate ────────────────────────────────────────
            applicable = PROCEDURE_APPLICABILITY.get(procedure_name)
            if applicable is None:
                print(f"[Emulator] Unknown procedure: {procedure_name} — "
                      f"no recovery applied")
                return False

            active = self.fault_injected
            # fault_injected is Optional[FaultType] and is None on a fresh
            # emulator, not FaultType.NONE — matching the existing check at
            # _apply_fault_effects(). Testing only against FaultType.NONE let
            # a healthy satellite fall through to the applicability branch and
            # raise AttributeError on None.value, which would have surfaced as
            # a 500 from /recovery/trigger.
            if active in (None, FaultType.NONE):
                # Nothing to fix. Applying a procedure to a healthy satellite
                # is a no-op, not a success.
                print(f"[Emulator] No active fault — {procedure_name} not applied")
                return False

            if active not in applicable:
                print(f"[Emulator] {procedure_name} does not address "
                      f"{active.value} (applies to: "
                      f"{', '.join(f.value for f in applicable)}) — "
                      f"REFUSED, state unchanged")
                return False

            if procedure_name == "ADCS_MEMORY_SCRUB_v2":
                self.adcs.rate_deg_s         = 0.003
                self.adcs.pointing_error_deg = 0.001
                self.adcs.status             = "nominal"
                self.obc.register            = "0x3F"
                self.obc.error_count         = 0
                self.obc.status              = "nominal"

            elif procedure_name == "OBC_SOFT_REBOOT_v1":
                self.obc.cpu_usage_pct      = 18.5
                self.obc.memory_usage_pct   = 34.2
                self.obc.error_count        = 0
                self.obc.status             = "nominal"
                self.comms.downlink_active  = True
                self.comms.status           = "nominal"

            elif procedure_name == "OBC_HARD_RESET_v1":
                # FIX 8: Full OBC power cycle — resets all OBC state to nominal
                self.obc                    = OBCState()
                self.comms.downlink_active  = True
                self.comms.status           = "nominal"

            elif procedure_name == "FIRMWARE_ROLLBACK_v1":
                self.obc.status             = "nominal"
                self.adcs.status            = "nominal"
                self.power.status           = "nominal"
                self.comms.uplink_active    = True
                self.comms.downlink_active  = True
                self.comms.status           = "nominal"

            elif procedure_name == "SAFE_MODE_HOLD":
                # Minimal safe mode — beacon active, wait for next contact.
                # Restoring the beacon is what the procedure's success
                # criterion (`beacon_active: true`) actually verifies.
                self.obc.status             = "degraded"
                self.comms.beacon_active    = True
                print("[Emulator] Satellite in SAFE MODE HOLD — awaiting next contact")

            elif procedure_name == "LOCKDOWN_REGEN_v1":
                self.comms.status           = "nominal"
                self.comms.uplink_active    = True
                self.comms.downlink_active  = True
                self.power.solar_output_w   = 82.4
                self.power.status           = "nominal"

            elif procedure_name == "COMMS_HARD_RESET_v1":
                # FIX 8: Full comms subsystem power cycle
                self.comms                  = CommsState()

            elif procedure_name == "BATTERY_CELL_ISOLATE_v1":
                # Isolate the failed cell and fall back to the remaining string.
                # Capacity is reduced but the bus is stable — recovery here means
                # "safe and stable", not "as new", which is why the success
                # criteria ask for a healthy bus rather than a full battery.
                self.power.charging      = True
                self.power.bus_voltage_v = 28.0
                self.power.battery_pct   = max(self.power.battery_pct, 55.0)
                self.power.status        = "nominal"
                self.obc.status          = "nominal"

            elif procedure_name == "POWER_SAFE_MODE_v1":
                # Shed non-essential loads: keep the beacon and the bus alive.
                self.power.charging      = True
                self.power.bus_voltage_v = max(self.power.bus_voltage_v, 26.5)
                self.power.status        = "degraded"
                self.comms.beacon_active = True
                self.comms.downlink_active = False

            elif procedure_name == "ADCS_WHEEL_RESTART_v1":
                # Attempt to spin the stalled wheel back up.
                self.adcs.reaction_wheel_rpm = 4800.0
                self.adcs.rate_deg_s         = 0.003
                self.adcs.pointing_error_deg = 0.001
                self.adcs.status             = "nominal"

            elif procedure_name == "ADCS_MAGNETORQUER_FALLBACK_v1":
                # Wheel is gone for good — hold attitude on magnetorquers.
                # Coarser than wheel control, so pointing error settles at a
                # higher value than ADCS_WHEEL_RESTART_v1 achieves.
                self.adcs.reaction_wheel_rpm = 0.0
                self.adcs.rate_deg_s         = 0.008
                self.adcs.pointing_error_deg = 0.4
                self.adcs.status             = "degraded"

            else:
                # Unreachable: PROCEDURE_APPLICABILITY is keyed by the same
                # names as the branches above, and an unknown name is rejected
                # by the applicability gate. Kept as a guard so that adding a
                # procedure to the map without a handler fails loudly.
                print(f"[Emulator] {procedure_name} is in PROCEDURE_APPLICABILITY "
                      f"but has no handler — no recovery applied")
                return False

            self.fault_injected = FaultType.NONE
            self.fault_detail   = {}
            print(f"[Emulator] Recovery SUCCESS — {procedure_name} applied for "
                  f"{active.value}")
            return True

    # ── Utility ───────────────────────────────

    def get_overall_health(self) -> str:
        statuses = [self.obc.status, self.adcs.status, self.power.status, self.comms.status]
        if "fault"    in statuses: return "fault"
        if "degraded" in statuses: return "degraded"
        return "nominal"

    def reset(self):
        """Full reset to initial nominal state."""
        with self._lock:
            self.obc            = OBCState()
            self.adcs           = ADCSState()
            self.power          = PowerState()
            self.comms          = CommsState()
            self.fault_injected = None
            self.fault_detail   = {}
            self._ring_buffer.clear()
        print("[Emulator] Full reset to nominal state")


# ── Smoke test ────────────────────────────────

if __name__ == "__main__":
    emulator = SatelliteEmulator(tick_interval=1.0)
    emulator.start()

    print("\n--- Nominal (3s) ---")
    for _ in range(3):
        time.sleep(1)
        f = emulator.get_latest_frame()
        print(f"  t={f['timestamp']} | battery={f['battery_pct']}% | health={emulator.get_overall_health()} | buffer_size={len(emulator._ring_buffer)}")

    print("\n--- Inject SEU ---")
    emulator.inject_SEU("0x3F")
    for _ in range(3):
        time.sleep(1)
        f = emulator.get_latest_frame()
        print(f"  adcs_rate={f['adcs_rate_deg_s']} | fault={f['fault_injected']}")

    print(f"\n--- Ring buffer has {len(emulator._ring_buffer)} real frames ---")
    history = emulator.get_frame_history(60)
    print(f"  get_frame_history(60) returned {len(history)} frames ✓")

    print("\n--- Recovery: OBC_HARD_RESET_v1 ---")
    emulator.apply_recovery("OBC_HARD_RESET_v1")

    print("\n--- Recovery: COMMS_HARD_RESET_v1 ---")
    emulator.apply_recovery("COMMS_HARD_RESET_v1")

    emulator.stop()


# ──────────────────────────────────────────────
# Real Data Seeding (SatNOGS baselines)
# ──────────────────────────────────────────────

def seed_from_real_data(emulator: "SatelliteEmulator",
                        n2yo_api_key: str = "",
                        norad_id: int = 28654) -> bool:
    """
    Pull real SatNOGS telemetry for the target satellite and
    seed the emulator's nominal baseline values from actual data.

    Call this once at startup before emulator.start().

    Returns True if real data was applied, False if defaults kept.
    """
    try:
        import sys, os
        sys.path.append(os.path.dirname(__file__))
        from real_data_fetcher import RealDataFetcher

        fetcher   = RealDataFetcher(n2yo_api_key=n2yo_api_key, norad_id=norad_id)
        baselines = fetcher.get_satnogs_baselines(limit=50)

        if not baselines:
            print("[Emulator] No SatNOGS baselines found — using default nominal values")
            return False

        with emulator._lock:
            if "battery_pct"   in baselines:
                emulator.power.battery_pct    = baselines["battery_pct"]
            if "obc_temp_c"    in baselines:
                emulator.obc.temp_c           = baselines["obc_temp_c"]
            if "power_w"       in baselines:
                emulator.power.solar_output_w = baselines["power_w"]
            if "bus_voltage_v" in baselines:
                emulator.power.bus_voltage_v  = baselines["bus_voltage_v"]

        print(f"[Emulator] Seeded from real SatNOGS data: {baselines}")
        return True

    except Exception as e:
        print(f"[Emulator] Real data seeding failed: {e} — using defaults")
        return False