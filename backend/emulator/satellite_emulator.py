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


# ──────────────────────────────────────────────
# Ring Buffer
# ──────────────────────────────────────────────

RING_BUFFER_SIZE = 120   # store 2 minutes, AI-1 needs last 60 frames


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

    def __init__(self, tick_interval: float = 1.0):
        self.tick_interval  = tick_interval
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
        """Start background tick thread."""
        self._running = True
        self._thread  = threading.Thread(target=self._tick_loop, daemon=True)
        self._thread.start()
        print("[Emulator] Started — streaming telemetry every 1s")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
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
        """Add small realistic noise to nominal telemetry each tick."""
        if self.fault_injected in (None, FaultType.NONE):
            self.obc.temp_c             += random.uniform(-0.3, 0.3)
            self.obc.temp_c              = max(35.0, min(65.0, self.obc.temp_c))
            self.obc.cpu_usage_pct      += random.uniform(-1.0, 1.0)
            self.obc.cpu_usage_pct       = max(5.0, min(40.0, self.obc.cpu_usage_pct))

            self.adcs.rate_deg_s        += random.uniform(-0.001, 0.001)
            self.adcs.rate_deg_s         = max(0.0, min(0.01, self.adcs.rate_deg_s))
            self.adcs.reaction_wheel_rpm += random.uniform(-5, 5)

            self.power.solar_output_w   += random.uniform(-1.0, 1.0)
            self.power.battery_pct      += random.uniform(-0.05, 0.1)
            self.power.battery_pct       = max(70.0, min(100.0, self.power.battery_pct))
            self.power.bus_voltage_v    += random.uniform(-0.05, 0.05)

            self.comms.signal_strength_dbm += random.uniform(-1.0, 1.0)
            self.comms.signal_strength_dbm  = max(-95.0, min(-60.0, self.comms.signal_strength_dbm))

    def _apply_fault_effects(self):
        """Progress fault symptoms each tick once a fault is active."""
        if self.fault_injected == FaultType.SEU:
            self.adcs.rate_deg_s        += random.uniform(0.05, 0.15)
            self.adcs.pointing_error_deg += random.uniform(0.1, 0.5)
            self.adcs.status             = "fault"
            self.obc.error_count        += 1
            self.obc.status              = "degraded"

        elif self.fault_injected == FaultType.SOFTWARE_BUG:
            self.obc.cpu_usage_pct      = min(100.0, self.obc.cpu_usage_pct + random.uniform(5, 15))
            self.obc.memory_usage_pct   = min(100.0, self.obc.memory_usage_pct + random.uniform(3, 8))
            self.obc.error_count        += random.randint(1, 5)
            self.obc.status              = "fault"
            self.comms.downlink_active   = False
            self.comms.status            = "degraded"

        elif self.fault_injected == FaultType.FIRMWARE_CORRUPTION:
            self.obc.status              = "fault"
            self.adcs.status             = "degraded"
            self.power.bus_voltage_v    -= random.uniform(0.05, 0.2)
            self.power.status            = "degraded"
            self.comms.uplink_active     = False
            self.comms.downlink_active   = False
            self.comms.status            = "fault"

        elif self.fault_injected == FaultType.COMMAND_INJECTION:
            self.comms.status            = "fault"
            self.obc.error_count        += 1
            self.power.solar_output_w   -= random.uniform(2, 5)
            self.power.solar_output_w    = max(0.0, self.power.solar_output_w)

    def _build_frame(self) -> dict:
        """Build the canonical JSON telemetry frame shared with all team members."""
        return {
            "timestamp":             int(time.time()),
            "frame_id":              self._frame_count,

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

    # ── Recovery ──────────────────────────────

    def apply_recovery(self, procedure_name: str) -> bool:
        """
        Called by LangGraph agent after signed command uplinked.
        FIX 8: Added OBC_HARD_RESET_v1 and COMMS_HARD_RESET_v1 handlers.
        """
        with self._lock:
            print(f"[Emulator] Applying recovery procedure: {procedure_name}")

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
                # Minimal safe mode — beacon active, wait for next contact
                self.obc.status             = "degraded"
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

            else:
                print(f"[Emulator] Unknown procedure: {procedure_name} — no recovery applied")
                return False

            self.fault_injected = FaultType.NONE
            self.fault_detail   = {}
            print(f"[Emulator] Recovery SUCCESS — satellite nominal")
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