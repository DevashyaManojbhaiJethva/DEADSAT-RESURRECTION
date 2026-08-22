"""
test_integration.py — DeadSat Resurrection integration tests
=============================================================
Tests every integration seam WITHOUT requiring:
  - Trained ML artifacts (model_artifacts/)
  - PyTorch / scikit-learn
  - LangGraph
  - Network access (N2YO, signing endpoint)

Anything genuinely unavailable is reported as SKIP, not FAIL, so this suite
is meaningful on a bare checkout and stricter once dependencies are present.

Run with:
    python test_integration.py
    python -m pytest test_integration.py -v
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
for _p in (ROOT, ROOT / "models", ROOT / "emulator", ROOT / "agents"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_USE_COLOR = sys.stdout.isatty()
PASS = "\033[92m PASS \033[0m" if _USE_COLOR else " PASS "
FAIL = "\033[91m FAIL \033[0m" if _USE_COLOR else " FAIL "
SKIP = "\033[93m SKIP \033[0m" if _USE_COLOR else " SKIP "

_results: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    _results.append((name, "pass" if condition else "fail"))
    print(f"  [{status}] {name}" + (f"   ({detail})" if detail else ""))
    return condition


def skip(name: str, reason: str) -> None:
    _results.append((name, "skip"))
    print(f"  [{SKIP}] {name}   ({reason})")


def _has(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


# ══════════════════════════════════════════════════════════════════════
# TEST 1 — Fault key normalisation (the critical fix)
# ══════════════════════════════════════════════════════════════════════


def test_fault_key_normalisation():
    print("\n[1] Fault key normalisation (classifier -> procedure_library)")
    from models.classifier_inference import FAULT_KEY_MAP, normalise_fault_key

    cases = [
        ("SEU", "SEU"),
        ("SOFTWARE_BUG", "software_bug"),
        ("FIRMWARE_CORRUPTION", "firmware_corruption"),
        ("COMMAND_INJECTION", "command_injection"),
        ("seu", "SEU"),  # lowercase input
        ("software_bug", "software_bug"),  # already normalised
        ("Firmware_Corruption", "firmware_corruption"),  # mixed case
    ]
    for raw, expected in cases:
        got = normalise_fault_key(raw)
        check(f"'{raw}' -> '{expected}'", got == expected, f"got '{got}'")

    # Every normalised key must exist in procedure_library.json
    with open(ROOT / "agents" / "procedure_library.json") as f:
        library = json.load(f)
    lib_keys = set(library["procedures"].keys())

    for raw, normalised in FAULT_KEY_MAP.items():
        if normalised == "none":
            continue
        check(
            f"normalised key '{normalised}' exists in procedure_library",
            normalised in lib_keys,
            f"available: {sorted(lib_keys)}",
        )

    # The legacy ml/ import path must resolve to the same function
    from ml.classifier_inference import normalise_fault_key as legacy
    check("ml/ compat shim resolves to same function", legacy is normalise_fault_key)


# ══════════════════════════════════════════════════════════════════════
# TEST 2 — Satellite emulator
# ══════════════════════════════════════════════════════════════════════


def test_satellite_emulator():
    print("\n[2] Satellite emulator — fault injection & recovery")
    from emulator.satellite_emulator import SatelliteEmulator

    em = SatelliteEmulator(tick_interval=0.1, norad_id=28654)
    em.start()
    time.sleep(0.25)

    frame = em.get_latest_frame()
    check("norad_id present in frame", frame.get("norad_id") == 28654)
    check("nominal obc_status", frame["obc_status"] == "nominal")
    check("nominal adcs_status", frame["adcs_status"] == "nominal")
    check("nominal power_w > 75", frame["power_w"] > 75, f"{frame['power_w']}")

    # SEU
    em.inject_SEU("0x3F")
    time.sleep(0.3)
    frame = em.get_latest_frame()
    check("SEU: adcs_status fault", frame["adcs_status"] == "fault")
    check("SEU: fault_injected tag", frame["fault_injected"] == "SEU")
    ok = em.apply_recovery("ADCS_MEMORY_SCRUB_v2")
    time.sleep(0.3)
    frame = em.get_latest_frame()
    check("SEU: recovery accepted", ok)
    check("SEU: adcs nominal after recovery", frame["adcs_status"] == "nominal")

    # Software bug
    em.inject_software_bug()
    time.sleep(0.3)
    frame = em.get_latest_frame()
    check("SW_BUG: obc_cpu_pct elevated", frame["obc_cpu_pct"] > 85, f"{frame['obc_cpu_pct']}")
    check("SW_BUG: fault_injected tag", frame["fault_injected"] == "software_bug")
    em.apply_recovery("OBC_SOFT_REBOOT_v1")
    time.sleep(0.2)

    # Command injection — via the new pipeline alias
    em.inject_command_injection()
    time.sleep(0.3)
    frame = em.get_latest_frame()
    check(
        "CMD_INJECT: alias sets correct fault",
        frame["fault_injected"] == "command_injection",
    )
    check("CMD_INJECT: fault_detail signed=False", frame["fault_detail"].get("signed") is False)
    em.apply_recovery("LOCKDOWN_REGEN_v1")
    time.sleep(0.2)

    # Firmware corruption
    em.inject_firmware_corruption()
    time.sleep(0.3)
    frame = em.get_latest_frame()
    check(
        "FW_CORRUPT: fault_injected tag",
        frame["fault_injected"] == "firmware_corruption",
    )
    em.apply_recovery("FIRMWARE_ROLLBACK_v1")
    time.sleep(0.2)
    check("FW_CORRUPT: recovered to nominal", em.get_overall_health() == "nominal")

    # Ring buffer feeds AI-1's sliding window
    history = em.get_frame_history(60)
    check("frame history is a list", isinstance(history, list))
    check("frame history non-empty", len(history) > 0, f"{len(history)} frames")

    em.stop()


# ══════════════════════════════════════════════════════════════════════
# TEST 3 — Contact calculator
# ══════════════════════════════════════════════════════════════════════


def test_contact_calculator():
    print("\n[3] Contact calculator")
    try:
        from emulator.contact_calculator import ContactCalculator
    except ImportError as exc:
        skip("contact_calculator import", str(exc))
        return

    calc = ContactCalculator()
    calc.load_tle()

    is_contact = calc.is_in_contact_now()
    check("is_in_contact_now() returns bool", isinstance(is_contact, bool))

    window = calc.find_next_contact(search_hours=2.0, step_seconds=10.0)
    if window is None:
        check("find_next_contact (None acceptable mid-pass)", True)
    else:
        check("window has 'aos'", "aos" in window)
        check("window has 'los'", "los" in window)
        check("window has max_elevation_deg", "max_elevation_deg" in window)


# ══════════════════════════════════════════════════════════════════════
# TEST 4 — Satellite catalog (real CSV data)
# ══════════════════════════════════════════════════════════════════════


def test_satellite_catalog():
    print("\n[4] Satellite catalog — real CSV orbital data")
    from satellite_catalog import get_catalog

    cat = get_catalog()
    check("catalog loaded satellites", len(cat) > 100, f"{len(cat)} satellites")

    # ISS (25544)
    iss = cat.get_anomaly_baselines(25544)
    if iss is None:
        skip("ISS (25544) baselines", "not present in CSV datasets")
    else:
        check("ISS altitude 300-500km", 300 <= iss["altitude_km_approx"] <= 500,
              f"{iss['altitude_km_approx']} km")
        check("ISS mean_motion > 15", iss["mean_motion_nominal"] > 15,
              f"{iss['mean_motion_nominal']}")

    # NOAA 18 (28654) — the pipeline default
    noaa = cat.get_anomaly_baselines(28654)
    check("NOAA 18 (28654) baselines returned", noaa is not None)
    if noaa:
        check("NOAA 18 has mean_motion_nominal", "mean_motion_nominal" in noaa)
        check("NOAA 18 altitude sane", 500 <= noaa["altitude_km_approx"] <= 1200,
              f"{noaa['altitude_km_approx']} km")

    # Unknown NORAD
    check("unknown NORAD returns None", cat.get_anomaly_baselines(9999999) is None)

    # TLE generation
    tle = cat.get_tle(28654)
    check("TLE generated for NOAA 18", tle is not None)
    if tle:
        check("TLE line1 is 69 chars", len(tle["line1"]) == 69, f"{len(tle['line1'])}")
        check("TLE line2 is 69 chars", len(tle["line2"]) == 69, f"{len(tle['line2'])}")


# ══════════════════════════════════════════════════════════════════════
# TEST 5 — Procedure library schema
# ══════════════════════════════════════════════════════════════════════


def test_procedure_library():
    print("\n[5] Procedure library — schema validation")
    lib_path = ROOT / "agents" / "procedure_library.json"
    if not check("procedure_library.json exists", lib_path.exists()):
        return

    with open(lib_path) as f:
        library = json.load(f)

    required = {
        "SEU", "software_bug", "firmware_corruption", "command_injection",
        "battery_failure", "adcs_failure",
    }
    present = set(library["procedures"].keys())
    check("all 6 fault types present", required == present, f"present={sorted(present)}")

    for fault_key, entry in library["procedures"].items():
        priority_list = entry.get("recovery_priority", [])
        check(f"{fault_key}: has >=1 procedure", len(priority_list) >= 1)
        for proc in priority_list:
            name = proc.get("procedure_name", "?")
            check(f"{fault_key}/{name}: has commands", len(proc.get("commands", [])) > 0)
            check(f"{fault_key}/{name}: has success_criteria", "success_criteria" in proc)
            check(f"{fault_key}/{name}: has min_confidence", "min_confidence" in proc)


# ══════════════════════════════════════════════════════════════════════
# TEST 6 — Procedure names are all implemented by the emulator
# ══════════════════════════════════════════════════════════════════════


def test_procedures_implemented():
    print("\n[6] Every library procedure is implemented by the emulator")
    from emulator.satellite_emulator import SatelliteEmulator

    with open(ROOT / "agents" / "procedure_library.json") as f:
        library = json.load(f)

    # procedure_name -> a fault it is declared to remedy. apply_recovery() is
    # now fault-aware (it used to return True for any recognised name even on
    # a healthy satellite), so the applicable fault must be injected first.
    # The property under test is unchanged: every procedure in the library has
    # a working handler in the emulator.
    proc_to_fault = {
        proc["procedure_name"]: fault_key
        for fault_key, entry in library["procedures"].items()
        for proc in entry.get("recovery_priority", [])
    }

    injectors = {
        "SEU": lambda e: e.inject_SEU(),
        "software_bug": lambda e: e.inject_software_bug(),
        "firmware_corruption": lambda e: e.inject_firmware_corruption(),
        "command_injection": lambda e: e.inject_command(),
        "battery_failure": lambda e: e.inject_battery_failure(),
        "adcs_failure": lambda e: e.inject_adcs_failure(),
    }

    for name in sorted(proc_to_fault):
        em = SatelliteEmulator(tick_interval=0.1)
        injectors[proc_to_fault[name]](em)
        handled = em.apply_recovery(name)
        check(f"emulator implements '{name}'", handled)

    # The gate itself: a procedure must refuse a fault it cannot remedy.
    em = SatelliteEmulator(tick_interval=0.1)
    em.inject_SEU()
    refused = em.apply_recovery("LOCKDOWN_REGEN_v1")
    check("wrong procedure refused for SEU", refused is False)
    # read the attribute, not get_latest_frame(): the emulator is not started
    # here, so the frame buffer is empty.
    check("fault preserved after refusal",
          em.fault_injected is not None and em.fault_injected.value == "SEU",
          str(em.fault_injected))


# ══════════════════════════════════════════════════════════════════════
# TEST 7 — Recovery agent success-criteria logic
# ══════════════════════════════════════════════════════════════════════


def test_check_criteria():
    print("\n[7] Recovery agent _check_criteria logic")
    if not _has("langgraph"):
        skip("recovery_agent._check_criteria", "langgraph not installed")
        return
    try:
        from agents.recovery_agent import _check_criteria
    except ImportError as exc:
        skip("recovery_agent._check_criteria", str(exc))
        return

    frame = {
        "adcs_rate_deg_s": 0.005,
        "adcs_pointing_err_deg": 0.003,
        "adcs_status": "nominal",
    }
    criteria = {"adcs_rate_deg_s": "< 0.01", "adcs_status": "nominal"}
    check("SEU criteria met when nominal", _check_criteria(frame, criteria))
    check(
        "SEU criteria fails with adcs_status=fault",
        not _check_criteria({**frame, "adcs_status": "fault"}, criteria),
    )

    frame_power = {"comms_status": "nominal", "power_status": "nominal", "power_w": 82.0}
    criteria_ci = {"comms_status": "nominal", "power_w": "> 75"}
    check("CMD_INJECT criteria met", _check_criteria(frame_power, criteria_ci))
    check(
        "CMD_INJECT criteria fails on low power",
        not _check_criteria({**frame_power, "power_w": 50.0}, criteria_ci),
    )


# ══════════════════════════════════════════════════════════════════════
# TEST 8 — RecoveryAgent construction
# ══════════════════════════════════════════════════════════════════════


def test_agent_construction():
    print("\n[8] RecoveryAgent construction (no graph execution)")
    if not _has("langgraph"):
        skip("RecoveryAgent construction", "langgraph not installed")
        return

    from emulator.satellite_emulator import SatelliteEmulator

    try:
        from agents.recovery_agent import RecoveryAgent
    except ImportError as exc:
        skip("RecoveryAgent import", str(exc))
        return

    em = SatelliteEmulator(tick_interval=0.1)
    em.start()
    time.sleep(0.15)
    try:
        RecoveryAgent(em)
        check("RecoveryAgent constructs OK", True)
    except Exception as exc:
        check("RecoveryAgent constructs OK", False, str(exc))
    finally:
        em.stop()


# ══════════════════════════════════════════════════════════════════════
# TEST 9 — Orbital window builder
# ══════════════════════════════════════════════════════════════════════


def test_orbital_window_builder():
    print("\n[9] Orbital window builder (emulator -> classifier input)")
    from emulator.satellite_emulator import SatelliteEmulator
    from pipeline import _emulator_frame_to_orbital_window
    from feature_spec import CONFIG, FEATURE_COLS

    seq_len, n_feat = CONFIG["seq_len"], len(FEATURE_COLS)
    idx = {c: i for i, c in enumerate(FEATURE_COLS)}

    em = SatelliteEmulator(tick_interval=0.1, norad_id=28654)
    em.start()
    time.sleep(0.2)

    cases = [
        ("nominal", lambda e: None, "ADCS_MEMORY_SCRUB_v2"),
        ("SEU", lambda e: e.inject_SEU(), "ADCS_MEMORY_SCRUB_v2"),
        ("software_bug", lambda e: e.inject_software_bug(), "OBC_SOFT_REBOOT_v1"),
        ("firmware_corruption", lambda e: e.inject_firmware_corruption(), "FIRMWARE_ROLLBACK_v1"),
        ("command_injection", lambda e: e.inject_command_injection(), "LOCKDOWN_REGEN_v1"),
    ]

    for fault_name, inject_fn, recovery in cases:
        inject_fn(em)
        time.sleep(0.15)
        w = _emulator_frame_to_orbital_window(em, norad_id=28654)

        check(f"{fault_name}: shape ({seq_len}, {n_feat})", w.shape == (seq_len, n_feat), str(w.shape))
        check(f"{fault_name}: finite values", bool(np.isfinite(w).all()))

        # The signature must actually cross the classifier's label thresholds,
        # in the same precedence order as assign_fault_labels().
        tle_age = w[:, idx["TLE_AGE_HOURS"]]
        bstar = np.abs(w[:, idx["BSTAR"]])
        mmdot = np.abs(w[:, idx["MEAN_MOTION_DOT"]])
        ecc_jump = np.abs(np.diff(w[:, idx["ECCENTRICITY"]])).max()
        rev_delta = w[:, idx["REV_DELTA"]]

        if fault_name == "command_injection":
            check(
                f"{fault_name}: TLE_AGE > {CONFIG['tle_age_stale_hours']}h",
                bool((tle_age > CONFIG["tle_age_stale_hours"]).all()),
                f"min={tle_age.min():.1f}h",
            )
        elif fault_name == "firmware_corruption":
            check(
                f"{fault_name}: BSTAR > {CONFIG['bstar_anomaly_threshold']}",
                bool((bstar > CONFIG["bstar_anomaly_threshold"]).all()),
                f"min={bstar.min():.4f}",
            )
            check(
                f"{fault_name}: MEAN_MOTION_DOT > {CONFIG['mean_motion_dot_threshold']}",
                bool((mmdot > CONFIG["mean_motion_dot_threshold"]).all()),
            )
            check(
                f"{fault_name}: TLE_AGE stays fresh (no CMD_INJECT shadowing)",
                bool((tle_age < CONFIG["tle_age_stale_hours"]).all()),
            )
        elif fault_name == "SEU":
            check(
                f"{fault_name}: eccentricity JUMP > {CONFIG['eccentricity_jump_threshold']}",
                ecc_jump > CONFIG["eccentricity_jump_threshold"],
                f"max jump={ecc_jump:.4f}",
            )
            check(
                f"{fault_name}: not shadowed by higher-priority rules",
                bool((tle_age < CONFIG["tle_age_stale_hours"]).all())
                and bool((bstar < CONFIG["bstar_anomaly_threshold"]).all())
                and bool((mmdot < CONFIG["mean_motion_dot_threshold"]).all()),
            )
        elif fault_name == "software_bug":
            check(f"{fault_name}: REV_DELTA == 0", bool((rev_delta == 0).all()))
            check(
                f"{fault_name}: not shadowed by higher-priority rules",
                bool((tle_age < CONFIG["tle_age_stale_hours"]).all())
                and bool((bstar < CONFIG["bstar_anomaly_threshold"]).all())
                and bool((mmdot < CONFIG["mean_motion_dot_threshold"]).all())
                and ecc_jump < CONFIG["eccentricity_jump_threshold"],
            )
        else:  # nominal
            check(
                f"{fault_name}: no fault signature present",
                bool((tle_age < CONFIG["tle_age_stale_hours"]).all())
                and bool((bstar < CONFIG["bstar_anomaly_threshold"]).all())
                and ecc_jump < CONFIG["eccentricity_jump_threshold"]
                and bool((rev_delta > 0).all()),
            )

        em.apply_recovery(recovery)
        time.sleep(0.1)

    em.stop()


# ══════════════════════════════════════════════════════════════════════
# TEST 10 — Bridge contract (artifact-free parts)
# ══════════════════════════════════════════════════════════════════════


def test_spec_no_drift():
    print("\n[10] feature_spec is the single source of truth (no drift)")
    import feature_spec

    check("CONFIG has seq_len", "seq_len" in feature_spec.CONFIG)
    check("FEATURE_COLS has 11 columns", len(feature_spec.FEATURE_COLS) == 11,
          str(len(feature_spec.FEATURE_COLS)))
    check("no duplicate feature columns",
          len(set(feature_spec.FEATURE_COLS)) == len(feature_spec.FEATURE_COLS))
    check("num_classes matches FAULT_LABELS",
          feature_spec.CONFIG["num_classes"] == len(feature_spec.FAULT_LABELS))
    check("IDX_TO_LABEL inverts FAULT_LABELS",
          all(feature_spec.IDX_TO_LABEL[v] == k
              for k, v in feature_spec.FAULT_LABELS.items()))

    # Every classifier label must map to a real procedure_library key
    from models.classifier_inference import normalise_fault_key

    with open(ROOT / "agents" / "procedure_library.json") as f:
        lib_keys = set(json.load(f)["procedures"].keys())
    for label in feature_spec.FAULT_LABELS:
        check(f"label '{label}' maps into procedure_library",
              normalise_fault_key(label) in lib_keys,
              normalise_fault_key(label))

    # When the ML stack is present, V2 must re-export the identical objects
    if not (_has("torch") and _has("sklearn") and _has("pandas")):
        skip("V2 re-exports match feature_spec", "ML stack not installed")
        return
    import satellite_fault_classifier_V2 as v2

    check("V2.CONFIG is feature_spec.CONFIG", v2.CONFIG is feature_spec.CONFIG)
    check("V2.FEATURE_COLS is feature_spec.FEATURE_COLS",
          v2.FEATURE_COLS is feature_spec.FEATURE_COLS)
    check("V2.IDX_TO_LABEL is feature_spec.IDX_TO_LABEL",
          v2.IDX_TO_LABEL is feature_spec.IDX_TO_LABEL)


# ══════════════════════════════════════════════════════════════════════
# TEST 11 — Bridge contract (artifact-free parts + full inference)
# ══════════════════════════════════════════════════════════════════════


def test_bridge_contract():
    print("\n[11] Classifier bridge contract")
    from models.classifier_inference import FaultClassifierInference

    bridge = FaultClassifierInference()
    check("artifacts_available() returns bool", isinstance(bridge.artifacts_available(), bool))
    check("missing_artifacts() returns list", isinstance(bridge.missing_artifacts(), list))
    check("is_loaded is False before load()", bridge.is_loaded is False)

    # classify() before load() must raise, not silently return garbage
    try:
        bridge.classify(np.zeros((8, 11), dtype=np.float32))
        check("classify() before load() raises", False)
    except RuntimeError:
        check("classify() before load() raises RuntimeError", True)
    except Exception as exc:
        check("classify() before load() raises RuntimeError", False, type(exc).__name__)

    if not bridge.artifacts_available():
        skip("full inference", f"missing: {', '.join(bridge.missing_artifacts())}")
        return
    if not _has("torch"):
        skip("full inference", "torch not installed")
        return

    from pipeline import _emulator_frame_to_orbital_window
    from emulator.satellite_emulator import SatelliteEmulator

    bridge.load()
    em = SatelliteEmulator(tick_interval=0.1, norad_id=28654)
    em.start()
    time.sleep(0.2)

    for fault, inject_fn, recovery in [
        ("SEU", lambda e: e.inject_SEU(), "ADCS_MEMORY_SCRUB_v2"),
        ("software_bug", lambda e: e.inject_software_bug(), "OBC_SOFT_REBOOT_v1"),
        ("firmware_corruption", lambda e: e.inject_firmware_corruption(), "FIRMWARE_ROLLBACK_v1"),
        ("command_injection", lambda e: e.inject_command_injection(), "LOCKDOWN_REGEN_v1"),
    ]:
        inject_fn(em)
        time.sleep(0.15)
        window = _emulator_frame_to_orbital_window(em, norad_id=28654)
        report = bridge.classify(window, norad_id=28654)

        for key in (
            "fault_type", "fault_detail", "telemetry_frame", "confidence",
            "norad_id", "anomaly_flag", "raw_fault_class",
        ):
            check(f"{fault}: report has '{key}'", key in report)
        check(
            f"{fault}: fault_type is a procedure_library key",
            report["fault_type"] in {"SEU", "software_bug", "firmware_corruption", "command_injection"},
            report["fault_type"],
        )
        check(f"{fault}: classifier predicted correctly",
              report["fault_type"] == fault, f"got {report['fault_type']}")
        em.apply_recovery(recovery)
        time.sleep(0.1)

    em.stop()


# ══════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════


def main() -> int:
    print("=" * 62)
    print("DeadSat Resurrection — Integration Test Suite")
    print("=" * 62)

    tests = [
        test_fault_key_normalisation,
        test_satellite_emulator,
        test_contact_calculator,
        test_satellite_catalog,
        test_procedure_library,
        test_procedures_implemented,
        test_check_criteria,
        test_agent_construction,
        test_orbital_window_builder,
        test_spec_no_drift,
        test_bridge_contract,
    ]

    for test in tests:
        try:
            test()
        except Exception as exc:
            print(f"  [{FAIL}] {test.__name__} raised: {exc}")
            import traceback

            traceback.print_exc()
            _results.append((test.__name__, "fail"))

    passed = sum(1 for _, s in _results if s == "pass")
    failed = [n for n, s in _results if s == "fail"]
    skipped = sum(1 for _, s in _results if s == "skip")

    print("\n" + "=" * 62)
    print(f"Results: {passed} passed, {len(failed)} failed, {skipped} skipped")
    if failed:
        print("\nFAILED:")
        for name in failed:
            print(f"  - {name}")
    else:
        print("ALL CHECKS PASSED")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
