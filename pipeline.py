"""
pipeline.py — DeadSat Resurrection · End-to-End Pipeline
=========================================================
Connects every component in the correct order:

  ┌──────────────────────────────────────────────────────────────────┐
  │  SatelliteCatalog  (real GP/TLE elements for 850+ satellites)    │
  │            │  seeds the nominal orbital baseline                 │
  │            ▼                                                     │
  │  SatelliteEmulator (live telemetry + fault injection)            │
  │            │                                                     │
  │            ▼                                                     │
  │  FaultClassifierInference (AI-1 · Isolation Forest +             │
  │  Transformer Encoder, satellite_fault_classifier_V2)             │
  │    • Orbital element window -> (fault_type, confidence)          │
  │    • CRITICAL FIX: normalises fault key before handoff           │
  │            │                                                     │
  │            ▼                                                     │
  │  RecoveryAgent (AI-2 · LangGraph 9-node graph)                   │
  │    • Loads procedure_library.json                                │
  │    • Selects procedure by fault type + confidence                │
  │    • Signs commands (Dilithium / mock fallback)                  │
  │    • Checks ground contact window                                │
  │    • Uplinks to emulator and monitors recovery                   │
  │    • Persists recovery log to recovery_logs/                     │
  └──────────────────────────────────────────────────────────────────┘

Usage
-----
  # Full pipeline, all 4 fault types:
  python pipeline.py --all

  # Single fault type:
  python pipeline.py --fault SEU
  python pipeline.py --fault software_bug
  python pipeline.py --fault firmware_corruption
  python pipeline.py --fault command_injection

  # Skip the classifier (inject fault directly, mock confidence):
  python pipeline.py --fault SEU --skip-classifier

  # Use a different satellite from the catalog (default 28654 = NOAA 18):
  python pipeline.py --all --norad-id 25544

Training the classifier first (produces model_artifacts/):
  python train_classifier.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# ── Project path setup ────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
for _p in (ROOT, ROOT / "models", ROOT / "agents", ROOT / "emulator"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from emulator.satellite_emulator import SatelliteEmulator, FaultType  # noqa: E402
from models.classifier_inference import (  # noqa: E402
    ArtifactsNotFoundError,
    FaultClassifierInference,
    normalise_fault_key,
)
from feature_spec import CONFIG, FEATURE_COLS  # noqa: E402  (dependency-free spec)
from satellite_catalog import get_catalog  # noqa: E402

DEFAULT_NORAD_ID = 28654  # NOAA 18 — present in data/input.csv


# ══════════════════════════════════════════════════════════════════════
# Fault type registry
# ══════════════════════════════════════════════════════════════════════

_INJECT_MAP: dict[str, Callable[[SatelliteEmulator], None]] = {
    "SEU": lambda e: e.inject_SEU("0x3F"),
    "software_bug": lambda e: e.inject_software_bug(),
    "firmware_corruption": lambda e: e.inject_firmware_corruption(),
    "command_injection": lambda e: e.inject_command_injection(),
    "battery_failure": lambda e: e.inject_battery_failure(),
    "adcs_failure": lambda e: e.inject_adcs_failure(),
}

FAULT_TYPES: tuple[str, ...] = tuple(_INJECT_MAP.keys())

#: Faults AI-1 CANNOT classify, because it consumes orbital elements only.
#:
#: Battery state and reaction-wheel health leave no trace in mean motion,
#: eccentricity, BSTAR or TLE age — no amount of training would let the model
#: name them. Running the classifier on one of these would return whichever of
#: its four classes fit best, reinstating exactly the mismatch this change
#: removes, just one layer deeper.
#:
#: run_pipeline() forces skip_classifier for these: the operator has already
#: told us the fault type, so the diagnosis matches the label by construction.
CLASSIFIER_BLIND_FAULTS: frozenset[str] = frozenset({"battery_failure", "adcs_failure"})


# ══════════════════════════════════════════════════════════════════════
# Orbital window construction
# ══════════════════════════════════════════════════════════════════════

# Fallback baseline used only when the NORAD ID is absent from the catalog.
_FALLBACK_BASELINE: dict[str, float] = {
    "MEAN_MOTION": 14.5,
    "ECCENTRICITY": 0.001,
    "INCLINATION": 51.6,
    "RA_OF_ASC_NODE": 180.0,
    "ARG_OF_PERICENTER": 180.0,
    "MEAN_ANOMALY": 180.0,
    "BSTAR": 0.0002,
    "MEAN_MOTION_DOT": 0.00003,
    "MEAN_MOTION_DDOT": 0.0,
    "TLE_AGE_HOURS": 2.0,
    "REV_DELTA": 15.0,
}


def _catalog_baseline(norad_id: int) -> tuple[dict[str, float], str]:
    """
    Build a nominal FEATURE_COLS baseline from the real satellite catalog.

    Returns (baseline_dict, source_label). Falls back to a synthetic LEO
    baseline if the NORAD ID is not in the CSV catalog.
    """
    try:
        row = get_catalog().get_by_norad(norad_id)
    except Exception as exc:  # catalog/CSV problems must not kill the pipeline
        print(f"[Pipeline] Catalog unavailable ({exc}) — using fallback baseline")
        return dict(_FALLBACK_BASELINE), "fallback"

    if not row:
        print(
            f"[Pipeline] NORAD {norad_id} not in catalog — using fallback baseline"
        )
        return dict(_FALLBACK_BASELINE), "fallback"

    def _f(key: str, default: float) -> float:
        try:
            return float(str(row.get(key, default)).strip())
        except (TypeError, ValueError):
            return default

    mean_motion = _f("MEAN_MOTION", 14.5)
    baseline = {
        "MEAN_MOTION": mean_motion,
        "ECCENTRICITY": _f("ECCENTRICITY", 0.001),
        "INCLINATION": _f("INCLINATION", 51.6),
        "RA_OF_ASC_NODE": _f("RA_OF_ASC_NODE", 180.0),
        "ARG_OF_PERICENTER": _f("ARG_OF_PERICENTER", 180.0),
        "MEAN_ANOMALY": _f("MEAN_ANOMALY", 180.0),
        "BSTAR": _f("BSTAR", 0.0002),
        "MEAN_MOTION_DOT": _f("MEAN_MOTION_DOT", 0.00003),
        "MEAN_MOTION_DDOT": _f("MEAN_MOTION_DDOT", 0.0),
        # Healthy ephemeris: fresh TLE, revolution counter advancing normally.
        "TLE_AGE_HOURS": 2.0,
        "REV_DELTA": max(1.0, mean_motion),  # ~1 epoch of revolutions
    }
    name = str(row.get("OBJECT_NAME", "")).strip()
    return baseline, f"csv_gp:{name or norad_id}"


def _emulator_frame_to_orbital_window(
    emulator: SatelliteEmulator,
    norad_id: Optional[int] = None,
    seed: int = 42,
) -> np.ndarray:
    """
    Build an orbital-element window (seq_len, n_features) reflecting the
    emulator's current fault state.

    In production this is replaced by a database query pulling the last
    CONFIG['seq_len'] TLE epochs for this satellite. Here the nominal
    baseline comes from the real CSV catalog, then the fault signature is
    stamped onto it so AI-1 has a physically plausible window to ingest.

    Fault signatures follow satellite_fault_classifier_V2.assign_fault_labels()
    exactly, including its precedence order:
        1. TLE_AGE_HOURS > 72          -> COMMAND_INJECTION
        2. |BSTAR| > 0.005             -> FIRMWARE_CORRUPTION
        3. |MEAN_MOTION_DOT| > 0.001   -> FIRMWARE_CORRUPTION
        4. eccentricity JUMP > 0.01    -> SEU
        5. REV_DELTA <= 0              -> SOFTWARE_BUG
    """
    seq_len = CONFIG["seq_len"]
    rng = np.random.default_rng(seed)

    if norad_id is None:
        norad_id = getattr(emulator, "norad_id", DEFAULT_NORAD_ID)

    baseline, source = _catalog_baseline(norad_id)

    frame = emulator.get_latest_frame() or {}
    injected = frame.get("fault_injected")
    fault_type = normalise_fault_key(injected) if injected else "none"

    # Per-timestep values start as the nominal baseline for every row.
    window = np.zeros((seq_len, len(FEATURE_COLS)), dtype=np.float32)
    values = [dict(baseline) for _ in range(seq_len)]

    # ── Stamp the fault signature ─────────────────────────────────────
    if fault_type == "SEU":
        # A bit-flip in the on-board state vector: a sudden STEP in the
        # sequence, not a constant offset. The classifier keys on the
        # epoch-to-epoch jump, so the perturbation must start mid-window.
        jump_at = seq_len // 2
        for t in range(jump_at, seq_len):
            values[t]["ECCENTRICITY"] += 0.025  # > eccentricity_jump_threshold
            values[t]["MEAN_ANOMALY"] = (values[t]["MEAN_ANOMALY"] + 45.0) % 360.0

    elif fault_type == "software_bug":
        # Revolution counter stuck / rolled back while mean motion is normal.
        for t in range(seq_len):
            values[t]["REV_DELTA"] = 0.0

    elif fault_type == "firmware_corruption":
        # Corrupted drag/decay coefficients written by a bad flash image.
        for t in range(seq_len):
            values[t]["BSTAR"] = 0.025  # > bstar_anomaly_threshold (0.005)
            values[t]["MEAN_MOTION_DOT"] = 0.006  # > mean_motion_dot_threshold

    elif fault_type == "command_injection":
        # Stale ephemeris: the ground/space segment stopped producing updates.
        for t in range(seq_len):
            # Ages monotonically across the window, all well past 72h.
            values[t]["TLE_AGE_HOURS"] = 95.0 + t * 1.5

    # ── Add small observation noise and materialise the array ─────────
    for t in range(seq_len):
        for j, col in enumerate(FEATURE_COLS):
            base = values[t][col]
            # REV_DELTA == 0 must stay exactly 0 — noise would mask the fault.
            if col == "REV_DELTA" and base == 0.0:
                window[t, j] = 0.0
                continue
            noise = rng.normal(0.0, abs(base) * 0.02 + 1e-9)
            window[t, j] = base + noise

    if not np.isfinite(window).all():
        raise ValueError("Constructed orbital window contains NaN/Inf")

    print(
        f"[Pipeline] Window {window.shape} built from {source} "
        f"(fault signature: {fault_type})"
    )
    return window


# ══════════════════════════════════════════════════════════════════════
# Pipeline run
# ══════════════════════════════════════════════════════════════════════


def _mock_fault_report(
    fault_type: str,
    emulator: SatelliteEmulator,
    norad_id: int,
    reason: str,
    confidence: float = 0.92,
) -> dict:
    """Build a fault_report without running the classifier."""
    return {
        "fault_type": fault_type,
        "fault_detail": {"classifier_bypassed": reason},
        "telemetry_frame": emulator.get_latest_frame(),
        "confidence": confidence,
        "norad_id": norad_id,
        "anomaly_flag": True,
        "raw_fault_class": fault_type.upper(),
    }


def run_pipeline(
    fault_type: str,
    skip_classifier: bool = False,
    tick_interval: float = 0.3,
    norad_id: int = DEFAULT_NORAD_ID,
    emulator: Optional[SatelliteEmulator] = None,
) -> dict:
    """
    Run one complete inject → classify → recover cycle.

    If `emulator` is provided it is used as-is and NOT stopped on exit
    (this is how the FastAPI endpoints reuse the app's live emulator).
    Returns the result dict from RecoveryAgent.run(), enriched with the
    classification that drove it.
    """
    if fault_type not in _INJECT_MAP:
        raise ValueError(
            f"Unknown fault type {fault_type!r}. Valid: {list(_INJECT_MAP)}"
        )

    # AI-1 cannot see these faults at all — see CLASSIFIER_BLIND_FAULTS. Force
    # the bypass rather than letting the model guess and report a fault type
    # that contradicts what the operator injected.
    if fault_type in CLASSIFIER_BLIND_FAULTS and not skip_classifier:
        print(f"  Classifier  : FORCED SKIP — {fault_type} has no orbital-element "
              f"signature; AI-1 classifies from TLE data only")
        skip_classifier = True

    owns_emulator = emulator is None

    print(f"\n{'=' * 62}")
    print("DeadSat Resurrection — Pipeline Run")
    print(f"  Fault type  : {fault_type}")
    print(f"  NORAD ID    : {norad_id}")
    print(f"  Classifier  : {'SKIP (direct inject)' if skip_classifier else 'ENABLED'}")
    print(f"{'=' * 62}\n")

    # ── 1. Emulator ───────────────────────────────────────────────────
    if owns_emulator:
        emulator = SatelliteEmulator(tick_interval=tick_interval, norad_id=norad_id)
        emulator.start()
        time.sleep(0.5)  # let the tick loop produce a first frame

    try:
        # ── 2. Inject fault ───────────────────────────────────────────
        _INJECT_MAP[fault_type](emulator)
        time.sleep(0.5)  # let telemetry reflect the fault

        # ── 3. Classify ───────────────────────────────────────────────
        if skip_classifier:
            print("[Pipeline] Classifier skipped — using injected fault type directly")
            fault_report = _mock_fault_report(
                fault_type, emulator, norad_id, "skip_classifier flag"
            )
        else:
            print("[Pipeline] Building orbital window for classifier ...")
            window = _emulator_frame_to_orbital_window(emulator, norad_id=norad_id)

            try:
                bridge = FaultClassifierInference().load()
                fault_report = bridge.classify(
                    telemetry_window=window,
                    norad_id=norad_id,
                    extra_detail={"emulator_frame": emulator.get_latest_frame()},
                )
                predicted = fault_report["fault_type"]
                match = "MATCH" if predicted == fault_type else "MISMATCH"
                print("[Pipeline] Classifier result:")
                print(f"  raw_fault_class : {fault_report['raw_fault_class']}")
                print(f"  fault_type      : {predicted}  (normalised)")
                print(f"  confidence      : {fault_report['confidence']:.2%}")
                print(f"  anomaly_flag    : {fault_report['anomaly_flag']}")
                print(f"  vs injected     : {fault_type}  -> {match}")
            except (ArtifactsNotFoundError, ImportError) as exc:
                print(f"[Pipeline] {exc}")
                print("[Pipeline] Falling back to skip-classifier mode\n")
                fault_report = _mock_fault_report(
                    fault_type, emulator, norad_id, str(exc).splitlines()[0], 0.85
                )

        # ── 4. Recovery agent ─────────────────────────────────────────
        print("\n[Pipeline] Handing off to RecoveryAgent ...")
        from agents.recovery_agent import RecoveryAgent  # imported late: needs langgraph

        agent = RecoveryAgent(emulator)
        result = agent.run(fault_report)

    finally:
        # ── 5. Shutdown ───────────────────────────────────────────────
        if owns_emulator and emulator is not None:
            emulator.stop()

    # Enrich the result with what AI-1 actually said
    result = dict(result)
    result["injected_fault"] = fault_type
    result["classified_fault"] = fault_report["fault_type"]
    result["classifier_confidence"] = fault_report["confidence"]
    result["classifier_correct"] = fault_report["fault_type"] == fault_type
    result["norad_id"] = norad_id

    print("\n[Pipeline] ── Final Result ─────────────────────────────")
    print(f"  success        : {result.get('success')}")
    print(f"  procedure_used : {result.get('procedure_used')}")
    print(f"  attempts       : {result.get('attempts')}")
    print(f"  elapsed_s      : {result.get('elapsed_s')}")
    if result.get("error"):
        print(f"  error          : {result['error']}")
    print(f"{'=' * 62}\n")
    return result


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DeadSat Resurrection — End-to-End Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--fault",
        choices=list(FAULT_TYPES),
        default=None,
        help="Fault type to inject and recover from",
    )
    parser.add_argument(
        "--all", action="store_true", help="Run all 4 fault types sequentially"
    )
    parser.add_argument(
        "--skip-classifier",
        action="store_true",
        dest="skip_classifier",
        help="Bypass AI-1 and hand the injected fault straight to AI-2",
    )
    parser.add_argument(
        "--norad-id",
        type=int,
        default=DEFAULT_NORAD_ID,
        dest="norad_id",
        help=f"NORAD catalogue ID (default: {DEFAULT_NORAD_ID} = NOAA 18)",
    )
    parser.add_argument(
        "--tick-interval",
        type=float,
        default=0.3,
        dest="tick_interval",
        help="Emulator telemetry tick interval in seconds (default: 0.3)",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default=None,
        dest="json_out",
        help="Write the full run summary to this JSON file",
    )
    args = parser.parse_args()

    if not args.fault and not args.all:
        parser.print_help()
        print("\nExample: python pipeline.py --all --skip-classifier")
        return 0

    fault_types = list(FAULT_TYPES) if args.all else [args.fault]
    all_results: dict[str, dict] = {}

    for i, ft in enumerate(fault_types):
        all_results[ft] = run_pipeline(
            fault_type=ft,
            skip_classifier=args.skip_classifier,
            tick_interval=args.tick_interval,
            norad_id=args.norad_id,
        )
        if i < len(fault_types) - 1:
            print("Pausing 2s between runs ...\n")
            time.sleep(2)

    # ── Summary ───────────────────────────────────────────────────────
    if len(fault_types) > 1:
        print("\n" + "=" * 62)
        print("FULL PIPELINE SUMMARY — All 4 Fault Types")
        print("=" * 62)
        for ft, r in all_results.items():
            status = "PASS" if r.get("success") else "FAIL"
            clf = "" if args.skip_classifier else (
                "  clf=" + ("ok" if r.get("classifier_correct") else "wrong")
            )
            print(
                f"  {ft:22s} {status}  procedure={r.get('procedure_used')}  "
                f"attempts={r.get('attempts')}  elapsed={r.get('elapsed_s')}s{clf}"
            )
        n_ok = sum(1 for r in all_results.values() if r.get("success"))
        print(f"\n  Recovered: {n_ok}/{len(all_results)}")
        print("=" * 62 + "\n")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(all_results, indent=2, default=str))
        print(f"[Pipeline] Summary written to {out}")

    return 0 if all(r.get("success") for r in all_results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
