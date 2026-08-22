#!/usr/bin/env python3
"""
test_units.py — unit tests for the logic that keeps breaking
=============================================================

`test_integration.py` covers the seams between components. These cover logic
inside them. Started in Phase 2 with the three data-leakage fixes; Prompt 7.1
extends it.

    python test_units.py            # run everything
    python test_units.py -v         # show each assertion

Requires torch + scikit-learn (same dependencies as training). Tests that
cannot run without them are reported as SKIPPED, never as passed.

EVERY TEST HERE MUST FAIL IF ITS FIX IS REVERTED. A test that cannot fail is
not a test — where the reversion is easy to describe, it is named in the
docstring so it can be checked by hand.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "models"))

VERBOSE = "-v" in sys.argv
_results: list[tuple[str, str, str]] = []


def _record(name, status, detail=""):
    _results.append((name, status, detail))
    mark = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))


def test(fn):
    """Register and run a test function immediately."""
    name = fn.__name__.replace("test_", "").replace("_", " ")
    try:
        fn()
        _record(name, "pass")
    except _Skip as exc:
        _record(name, "skip", str(exc))
    except AssertionError as exc:
        _record(name, "fail", str(exc) or "assertion failed")
        if VERBOSE:
            traceback.print_exc()
    except Exception as exc:  # noqa: BLE001
        _record(name, "fail", f"{type(exc).__name__}: {exc}")
        if VERBOSE:
            traceback.print_exc()
    return fn


class _Skip(Exception):
    pass


def _need(*mods):
    for m in mods:
        try:
            __import__(m)
        except ImportError:
            raise _Skip(f"{m} not installed")


def _clf():
    _need("torch", "sklearn", "pandas")
    import satellite_fault_classifier_V2 as clf
    return clf


# ===========================================================================
# LEAK 1 — windows must not span satellites
# ===========================================================================

@test
def test_no_window_spans_two_satellites():
    """
    Revert check: pass groups=None (the pre-fix behaviour) and this fails —
    on this fixture 14 of 23 legacy windows straddle a boundary.
    """
    _need("torch", "sklearn")
    import satellite_fault_classifier_V2 as clf

    seq = 8
    groups = np.repeat([101, 202, 303], 10)
    # feature 0 carries the satellite id, so a straddling window is visible
    X = np.stack([groups.astype(float), np.arange(30.0)], axis=1)
    y = np.arange(30) % 4

    ds = clf.OrbitalSequenceDataset(X, y, seq, groups=groups)

    assert len(ds) == 9, f"expected 3*(10-8+1)=9 windows, got {len(ds)}"
    for w in ds.samples:
        ids = set(w[:, 0].tolist())
        assert len(ids) == 1, f"window spans satellites {ids}"

    # the label must be the last row OF ITS OWN window
    for i in range(len(ds)):
        last_row = int(ds.samples[i][-1, 1])
        assert int(ds.labels[i]) == int(y[last_row]), "label/window mismatch"


@test
def test_group_shorter_than_seq_len_yields_no_windows():
    """A satellite with fewer than seq_len rows must contribute nothing."""
    _need("torch", "sklearn")
    import satellite_fault_classifier_V2 as clf

    seq = 8
    g = np.array([1] * 9 + [2] * 3 + [3] * 12)
    X = np.stack([g.astype(float), np.arange(len(g), dtype=float)], axis=1)
    ds = clf.OrbitalSequenceDataset(X, np.zeros(len(g), dtype=int), seq, groups=g)

    assert len(ds) == 7, f"expected (9-7)+(0)+(12-7)=7, got {len(ds)}"
    for w in ds.samples:
        assert len(set(w[:, 0].tolist())) == 1, "window spans satellites"
    assert 2.0 not in {w[0, 0] for w in ds.samples}, \
        "satellite 2 has 3 rows < seq_len and must produce no window"


@test
def test_empty_input_produces_wellformed_empty_dataset():
    """A split with no usable rows must give a shaped empty array, not crash."""
    _need("torch", "sklearn")
    import satellite_fault_classifier_V2 as clf

    ds = clf.OrbitalSequenceDataset(
        np.empty((0, 11), dtype=np.float32), np.empty((0,), dtype=np.int64),
        8, groups=np.empty((0,)))
    assert len(ds) == 0
    assert ds.samples.shape == (0, 8, 11), f"bad shape {ds.samples.shape}"


# ===========================================================================
# LEAK 1 — no satellite in more than one split
# ===========================================================================

@test
def test_no_norad_id_in_more_than_one_split():
    """
    Revert check: swap split_by_satellite() for train_test_split() on rows and
    this fails immediately — every multi-epoch satellite lands in 2+ splits.
    """
    clf = _clf()
    import pandas as pd

    rng = np.random.default_rng(0)
    rows = []
    for sat in range(60):
        for epoch in range(12):
            rows.append({
                "NORAD_CAT_ID": 1000 + sat,
                "EPOCH": pd.Timestamp("2026-01-01") + pd.Timedelta(hours=epoch),
                "fault_label": ["SEU", "SOFTWARE_BUG", "FIRMWARE_CORRUPTION",
                                "COMMAND_INJECTION"][sat % 4],
                **{c: float(rng.normal()) for c in clf.FEATURE_COLS},
            })
    df = pd.DataFrame(rows)

    train_df, val_df, test_df = clf.split_by_satellite(df)

    tr = set(train_df["NORAD_CAT_ID"])
    va = set(val_df["NORAD_CAT_ID"])
    te = set(test_df["NORAD_CAT_ID"])

    assert not (tr & va), f"train/val share {len(tr & va)} satellites"
    assert not (tr & te), f"train/test share {len(tr & te)} satellites"
    assert not (va & te), f"val/test share {len(va & te)} satellites"
    assert tr and va and te, "a split came out empty"
    assert len(tr | va | te) == 60, "satellites lost or duplicated in the split"


@test
def test_split_rows_are_sorted_within_satellite():
    """Windowing assumes contiguous, epoch-ordered rows per satellite."""
    clf = _clf()
    import pandas as pd

    rng = np.random.default_rng(1)
    rows = []
    for sat in range(30):
        for epoch in reversed(range(10)):        # deliberately out of order
            rows.append({
                "NORAD_CAT_ID": 2000 + sat,
                "EPOCH": pd.Timestamp("2026-01-01") + pd.Timedelta(hours=epoch),
                "fault_label": "SEU",
                **{c: float(rng.normal()) for c in clf.FEATURE_COLS},
            })
    df = pd.DataFrame(rows).sample(frac=1.0, random_state=2)   # shuffle

    for part in clf.split_by_satellite(df):
        ids = part["NORAD_CAT_ID"].values
        runs = np.flatnonzero(ids[1:] != ids[:-1]).size + 1
        assert runs == part["NORAD_CAT_ID"].nunique(), \
            "a satellite appears in more than one contiguous run"
        for _, g in part.groupby("NORAD_CAT_ID"):
            assert g["EPOCH"].is_monotonic_increasing, "epochs not sorted"


# ===========================================================================
# LEAK 3 — scaler fitted once, on training data only
# ===========================================================================

@test
def test_scaler_fitted_once_on_training_rows_only():
    """
    Revert check: restore the old `scaler.fit_transform(all_rows)` inside
    train_isolation_forest() and this fails on both counts — fit runs twice,
    and the second sees rows from every split.
    """
    clf = _clf()
    import pandas as pd
    from sklearn.preprocessing import StandardScaler

    calls = {"fit": 0, "rows": []}
    real_fit = StandardScaler.fit

    def spy_fit(self, X, *a, **k):
        calls["fit"] += 1
        calls["rows"].append(len(X))
        return real_fit(self, X, *a, **k)

    rng = np.random.default_rng(3)
    rows = []
    for sat in range(80):
        for epoch in range(12):
            rows.append({
                "NORAD_CAT_ID": 3000 + sat,
                "EPOCH": pd.Timestamp("2026-01-01") + pd.Timedelta(hours=epoch),
                "fault_label": ["SEU", "SOFTWARE_BUG", "FIRMWARE_CORRUPTION",
                                "COMMAND_INJECTION", "NORMAL"][sat % 5],
                **{c: float(rng.normal()) for c in clf.FEATURE_COLS},
            })
    df = pd.DataFrame(rows)

    StandardScaler.fit = spy_fit
    try:
        _, _, _, scaler, train_df = clf.build_dataloaders(df, target_per_class=50)
    finally:
        StandardScaler.fit = real_fit

    assert calls["fit"] == 1, f"scaler.fit() called {calls['fit']} times, expected 1"
    assert calls["rows"][0] == len(train_df), (
        f"scaler fitted on {calls['rows'][0]} rows but the training frame has "
        f"{len(train_df)} — it saw data outside the training split")


# ===========================================================================
# LEAK 2 — augmentation confined to the training split
# ===========================================================================

@test
def test_augmentation_does_not_reach_val_or_test():
    """
    Augmented rows are near-duplicates. If any satellite's row count grows in
    val/test, augmentation ran before the split (the Leak 2 bug).
    """
    clf = _clf()
    import pandas as pd

    rng = np.random.default_rng(4)
    rows = []
    for sat in range(80):
        # FIRMWARE_CORRUPTION deliberately rare — the class Leak 2 hurt most
        label = "FIRMWARE_CORRUPTION" if sat < 4 else \
                ["SEU", "SOFTWARE_BUG", "COMMAND_INJECTION"][sat % 3]
        for epoch in range(12):
            rows.append({
                "NORAD_CAT_ID": 4000 + sat,
                "EPOCH": pd.Timestamp("2026-01-01") + pd.Timedelta(hours=epoch),
                "fault_label": label,
                **{c: float(rng.normal()) for c in clf.FEATURE_COLS},
            })
    df = pd.DataFrame(rows)

    train_df, val_df, test_df = clf.split_by_satellite(df)
    n_before = {s: n for s, n in df.groupby("NORAD_CAT_ID").size().items()}

    augmented = clf.augment_fault_samples(train_df, target_per_class=200)

    for part, name in ((val_df, "val"), (test_df, "test")):
        for sat, n in part.groupby("NORAD_CAT_ID").size().items():
            assert n <= n_before[sat], \
                f"{name} satellite {sat} grew {n_before[sat]} -> {n}"
    assert len(augmented) > len(train_df), "augmentation did not run on train"


# ===========================================================================
# Isolation Forest — fitted on NORMAL rows only
# ===========================================================================

@test
def test_isolation_forest_fits_on_normal_rows_only():
    """
    Revert check: drop the NORMAL filter and this fails — the forest is handed
    fault rows, which is what made its 5% 'anomaly rate' circular.
    """
    clf = _clf()
    import pandas as pd
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler

    seen = {"n": None}
    real_fit = IsolationForest.fit

    def spy_fit(self, X, *a, **k):
        seen["n"] = len(X)
        return real_fit(self, X, *a, **k)

    rng = np.random.default_rng(5)
    rows = []
    for sat in range(40):
        for epoch in range(10):
            rows.append({
                "NORAD_CAT_ID": 5000 + sat,
                "EPOCH": pd.Timestamp("2026-01-01") + pd.Timedelta(hours=epoch),
                "fault_label": "NORMAL" if sat % 2 == 0 else "SEU",
                **{c: float(rng.normal()) for c in clf.FEATURE_COLS},
            })
    df = pd.DataFrame(rows)
    n_normal = int((df["fault_label"] == "NORMAL").sum())

    scaler = StandardScaler().fit(df[clf.FEATURE_COLS].values)

    IsolationForest.fit = spy_fit
    try:
        clf.train_isolation_forest(df, scaler)
    finally:
        IsolationForest.fit = real_fit

    assert seen["n"] == n_normal, (
        f"IsolationForest fitted on {seen['n']} rows but there are "
        f"{n_normal} NORMAL rows — it saw faults")


# ===========================================================================
# The deprecated synthetic generator contradicts the labeller
# ===========================================================================

@test
def test_synthetic_seu_contradicts_the_labeller():
    """
    Documents, rather than fixes, the contradiction: assign_fault_labels()
    defines SEU as an eccentricity JUMP between epochs, while
    _generate_synthetic_class('SEU') emits a CONSTANT eccentricity of 0.05.
    Relabelling its own output must NOT return SEU. If this ever starts
    failing, the two definitions have been reconciled and the deprecation
    note in _generate_synthetic_class() should be revisited.
    """
    clf = _clf()
    import pandas as pd

    syn = clf._generate_synthetic_class("SEU", n=40)
    syn = syn.sort_values("EPOCH").reset_index(drop=True)
    syn["REV_DELTA"] = 15.0
    syn["TLE_AGE_HOURS"] = 2.0

    relabelled = clf.assign_fault_labels(syn)["fault_label"]
    assert (relabelled == "SEU").sum() == 0, (
        "synthetic SEU rows now label as SEU — the contradiction described in "
        "_generate_synthetic_class.__doc__ may have been fixed")


# ===========================================================================
# Emulator — apply_recovery() must be fault-aware (Prompt 3.1)
# ===========================================================================

def _emulator():
    sys.path.insert(0, str(ROOT / "emulator"))
    import satellite_emulator as em
    return em


@test
def test_wrong_procedure_is_refused_and_leaves_state_untouched():
    """
    THE Prompt 3.1 acceptance test.

    Revert check: delete the applicability gate in apply_recovery() and this
    fails — the old code returned True and cleared fault_injected for any
    recognised procedure name.
    """
    em = _emulator()
    e = em.SatelliteEmulator(tick_interval=0.01)
    e.inject_SEU()
    adcs_before = e.adcs.status

    got = e.apply_recovery("LOCKDOWN_REGEN_v1")   # comms procedure, wrong for SEU

    assert got is False, f"wrong procedure returned {got}, expected False"
    assert e.fault_injected == em.FaultType.SEU, \
        f"fault cleared by a wrong procedure: {e.fault_injected}"
    assert e.adcs.status == adcs_before, "subsystem state mutated by a refusal"


@test
def test_matching_procedure_still_recovers():
    em = _emulator()
    e = em.SatelliteEmulator(tick_interval=0.01)
    e.inject_SEU()

    assert e.apply_recovery("ADCS_MEMORY_SCRUB_v2") is True
    assert e.fault_injected == em.FaultType.NONE
    assert e.adcs.status == "nominal"
    assert e.get_overall_health() == "nominal"


@test
def test_procedure_fault_matrix_is_exhaustively_correct():
    """Every procedure against every fault: applied iff applicable."""
    em = _emulator()
    inject = {
        em.FaultType.SEU: lambda e: e.inject_SEU(),
        em.FaultType.SOFTWARE_BUG: lambda e: e.inject_software_bug(),
        em.FaultType.FIRMWARE_CORRUPTION: lambda e: e.inject_firmware_corruption(),
        em.FaultType.COMMAND_INJECTION: lambda e: e.inject_command(),
    }
    for proc, applicable in em.PROCEDURE_APPLICABILITY.items():
        for fault, do_inject in inject.items():
            e = em.SatelliteEmulator(tick_interval=0.01)
            do_inject(e)
            got = e.apply_recovery(proc)
            want = fault in applicable
            assert got is want, f"{proc} vs {fault.value}: got {got}, want {want}"
            assert (e.fault_injected == em.FaultType.NONE) is want, \
                f"{proc} vs {fault.value}: fault-clearing disagrees with return"


@test
def test_apply_recovery_on_healthy_satellite_does_not_crash():
    """
    fault_injected is None (not FaultType.NONE) on a fresh emulator. Checking
    only against FaultType.NONE let a healthy satellite reach the applicability
    branch and raise AttributeError on None.value — a 500 from
    /recovery/trigger. Found by this test during Prompt 3.1.
    """
    em = _emulator()
    e = em.SatelliteEmulator(tick_interval=0.01)

    assert e.fault_injected is None, "fixture assumption changed"
    assert e.apply_recovery("ADCS_MEMORY_SCRUB_v2") is False
    assert e.apply_recovery("NOT_A_PROCEDURE") is False

    e.inject_SEU()
    e.reset()
    assert e.apply_recovery("ADCS_MEMORY_SCRUB_v2") is False, "after reset()"


@test
def test_applicability_map_matches_procedure_library():
    """
    The emulator derives the map from agents/procedure_library.json. If the two
    ever disagree, the agent will select a procedure the emulator refuses and
    recovery will fail for a reason nothing reports.
    """
    import json
    em = _emulator()

    lib = json.loads((ROOT / "agents" / "procedure_library.json")
                     .read_text(encoding="utf-8"))["procedures"]
    expected: dict[str, set] = {}
    for fault_key, spec in lib.items():
        for proc in spec.get("recovery_priority", []):
            name = proc.get("procedure_name") if isinstance(proc, dict) else proc
            expected.setdefault(name, set()).add(em.FaultType(fault_key))

    assert em.PROCEDURE_APPLICABILITY == expected, (
        f"emulator map disagrees with procedure_library.json\n"
        f"  emulator: {em.PROCEDURE_APPLICABILITY}\n"
        f"  library : {expected}")

    # every mapped procedure must have a handler branch
    for proc in expected:
        e = em.SatelliteEmulator(tick_interval=0.01)
        fault = next(iter(expected[proc]))
        {em.FaultType.SEU: e.inject_SEU,
         em.FaultType.SOFTWARE_BUG: e.inject_software_bug,
         em.FaultType.FIRMWARE_CORRUPTION: e.inject_firmware_corruption,
         em.FaultType.COMMAND_INJECTION: e.inject_command}[fault]()
        assert e.apply_recovery(proc) is True, \
            f"{proc} is in the map but has no working handler"


# ===========================================================================
# Emulator — bounded telemetry and thread safety (Prompt 3.3)
# ===========================================================================

_TICKS = 5000


def _drive(e, n=_TICKS):
    """Tick without sleeping — exercises the same code the tick loop runs."""
    for _ in range(n):
        e._update_nominal_drift()
        e._apply_fault_effects()


@test
def test_telemetry_stays_within_physical_bounds_over_5000_ticks():
    """
    THE Prompt 3.3 acceptance test, run in every fault state.

    Revert check: remove any _clamp() call and this fails. Measured before the
    fix — power_w reached 192 W from a 82.4 W start, adcs_rate_deg_s 15.4 deg/s
    after 150 ticks (nominal < 0.01), obc_error_count unbounded.
    """
    em = _emulator()
    scenarios = {
        "healthy": None,
        "SEU": lambda e: e.inject_SEU(),
        "software_bug": lambda e: e.inject_software_bug(),
        "firmware_corruption": lambda e: e.inject_firmware_corruption(),
        "command_injection": lambda e: e.inject_command(),
    }
    for name, inject in scenarios.items():
        e = em.SatelliteEmulator(tick_interval=0)
        if inject:
            inject(e)
        lo = {f: float("inf") for f in em.PHYSICAL_LIMITS}
        hi = {f: float("-inf") for f in em.PHYSICAL_LIMITS}
        for _ in range(_TICKS):
            e._update_nominal_drift()
            e._apply_fault_effects()
            frame = e._build_frame()
            for f in em.PHYSICAL_LIMITS:
                v = float(frame[f])
                lo[f] = min(lo[f], v)
                hi[f] = max(hi[f], v)
        for f, (bl, bh) in em.PHYSICAL_LIMITS.items():
            assert lo[f] >= bl - 1e-9, \
                f"{name}: {f} fell to {lo[f]}, below limit {bl}"
            assert hi[f] <= bh + 1e-9, \
                f"{name}: {f} rose to {hi[f]}, above limit {bh}"


@test
def test_healthy_drift_stays_inside_nominal_bands():
    """
    The demo-failure case: with no fault injected, power_w must never fall to
    the point where LOCKDOWN_REGEN_v1's `power_w > 75` criterion would fail.
    It was an unbounded walk and reached 46.6 W in ~33 minutes of ticking.
    """
    em = _emulator()
    e = em.SatelliteEmulator(tick_interval=0)
    lo = {f: float("inf") for f in em.NOMINAL_BANDS}
    hi = {f: float("-inf") for f in em.NOMINAL_BANDS}
    for _ in range(_TICKS):
        e._update_nominal_drift()
        e._apply_fault_effects()
        frame = e._build_frame()
        for f in em.NOMINAL_BANDS:
            v = float(frame[f])
            lo[f] = min(lo[f], v)
            hi[f] = max(hi[f], v)
    for f, (bl, bh) in em.NOMINAL_BANDS.items():
        assert lo[f] >= bl - 1e-9 and hi[f] <= bh + 1e-9, \
            f"healthy {f} left its nominal band: [{lo[f]}, {hi[f]}] vs ({bl}, {bh})"
    assert lo["power_w"] > 75.0, \
        f"power_w reached {lo['power_w']} — below the LOCKDOWN_REGEN_v1 criterion"


@test
def test_nominal_drift_cannot_break_a_recovery_criterion():
    """
    Every NOMINAL_BAND that a success_criterion also constrains must be
    strictly tighter than that criterion, or drift can walk a recovered
    satellite back across the threshold and fail a check that already passed.
    Derived from procedure_library.json so a new procedure with a threshold
    inside a band fails here rather than mid-demo.
    """
    import json
    em = _emulator()
    lib = json.loads((ROOT / "agents" / "procedure_library.json")
                     .read_text(encoding="utf-8"))["procedures"]

    for spec in lib.values():
        for proc in spec.get("recovery_priority", []):
            for key, cond in (proc.get("success_criteria") or {}).items():
                if key not in em.NOMINAL_BANDS:
                    continue
                band_lo, band_hi = em.NOMINAL_BANDS[key]
                text = str(cond).strip()
                for op in ("<=", ">=", "<", ">"):
                    if text.startswith(op):
                        thr = float(text[len(op):])
                        ok = {"<": band_hi < thr, "<=": band_hi <= thr,
                              ">": band_lo > thr, ">=": band_lo >= thr}[op]
                        assert ok, (
                            f"{proc['procedure_name']}: criterion {key} {text} "
                            f"can be broken by nominal drift within band "
                            f"({band_lo}, {band_hi})")
                        break


@test
def test_telemetry_has_noise_during_faults():
    """
    "Improvement 2: fault state telemetry has noise on top of fault effects"
    was false — _update_nominal_drift() returned early whenever a fault was
    active, so every subsystem froze. Measured: 1 distinct obc_temp_c across
    30 ticks. Faulted telemetry was identifiable by having no sensor noise.
    """
    em = _emulator()
    for name, inject in (("SEU", lambda e: e.inject_SEU()),
                         ("software_bug", lambda e: e.inject_software_bug()),
                         ("firmware_corruption", lambda e: e.inject_firmware_corruption()),
                         ("command_injection", lambda e: e.inject_command())):
        e = em.SatelliteEmulator(tick_interval=0)
        inject(e)
        temps, sig = set(), set()
        for _ in range(30):
            e._update_nominal_drift()
            e._apply_fault_effects()
            frame = e._build_frame()
            temps.add(frame["obc_temp_c"])
            sig.add(frame["signal_strength_dbm"])
        assert len(temps) > 5, f"{name}: obc_temp_c frozen ({len(temps)} distinct)"
        assert len(sig) > 5, f"{name}: signal_strength_dbm frozen ({len(sig)} distinct)"


@test
def test_start_is_idempotent():
    """
    start() overwrote self._thread unconditionally, so a second call left the
    first thread ticking with nothing referencing it — stop() could only join
    the newest. Reachable in practice: `python main.py` executes the module
    twice (once as __main__, once when uvicorn imports it).
    """
    import threading
    em = _emulator()

    e = em.SatelliteEmulator(tick_interval=0.02)
    baseline = threading.active_count()
    e.start()
    first = e._thread
    e.start()
    try:
        assert e._thread is first, "second start() replaced the thread object"
        assert threading.active_count() - baseline == 1, \
            f"{threading.active_count() - baseline} live threads, expected 1"
    finally:
        e.stop()

    assert not first.is_alive(), "thread still running after stop()"
    e.start()
    try:
        assert e._thread is not None and e._thread.is_alive(), \
            "restart after stop() failed"
    finally:
        e.stop()


# ===========================================================================
# Recovery agent — success criteria and fallback (Prompt 3.2)
# ===========================================================================

def _agent():
    """
    Import recovery_agent with httpx stubbed.

    recovery_agent imports httpx unconditionally at module level, but none of
    the logic under test here makes an HTTP call. langgraph already degrades
    via LANGGRAPH_AVAILABLE, so the node and routing functions are importable
    and testable without either package installed.
    """
    import types
    if "httpx" not in sys.modules:
        stub = types.ModuleType("httpx")
        def _no_network(*a, **k):
            raise AssertionError("test made an unexpected HTTP call")
        stub.post = stub.get = _no_network
        sys.modules["httpx"] = stub
    sys.path.insert(0, str(ROOT / "emulator"))
    sys.path.insert(0, str(ROOT / "agents"))
    import recovery_agent
    return recovery_agent


def _library():
    import json
    return json.loads((ROOT / "agents" / "procedure_library.json")
                      .read_text(encoding="utf-8"))


def _state(ra, fault: str, confidence: float, idx: int = 0) -> dict:
    return {
        "fault_type": fault, "fault_detail": {}, "telemetry_frame": {},
        "fault_confidence": confidence, "norad_id": 28654,
        "procedure_library": _library(), "selected_procedure": {},
        "priority_index": idx, "priority_list_len": 2, "attempt_count": 0,
        "command_sequence": [], "signed_commands": [], "signing_success": False,
        "contact_window": {}, "uplink_allowed": False, "recovery_success": False,
        "recovery_log": [], "error": None, "next_step": None,
    }


@test
def test_check_criteria_operators_and_missing_keys():
    """BUGS B, C, D — every branch of _check_criteria."""
    ra = _agent()
    cc = ra._check_criteria
    cases = [
        ({"a": 0.005}, {"a": "< 0.01"}, True),
        ({"a": 0.02}, {"a": "< 0.01"}, False),
        ({"a": 0.01}, {"a": "<= 0.01"}, True),      # BUG C
        ({"a": 0.011}, {"a": "<= 0.01"}, False),
        ({"a": 80}, {"a": ">= 75"}, True),          # BUG C
        ({"a": 70}, {"a": ">= 75"}, False),
        ({"a": 80}, {"a": "> 75"}, True),
        ({"b": True}, {"b": True}, True),           # BUG D
        ({"b": False}, {"b": True}, False),
        ({"b": True}, {"b": "true"}, True),
        ({"s": "nominal"}, {"s": "nominal"}, True),
        ({"s": "fault"}, {"s": "nominal"}, False),
        ({}, {"beacon_active": "true"}, False),     # BUG B: missing -> closed
        ({"beacon_active": None}, {"beacon_active": "true"}, False),
        ({"a": 1}, {}, True),
    ]
    for frame, crit, want in cases:
        got = cc(frame, crit)
        assert got is want, f"_check_criteria({frame}, {crit}) = {got}, want {want}"


@test
def test_bool_criterion_does_not_raise():
    """BUG D: `True.startswith` is an AttributeError, uncaught by the old
    `except (ValueError, TypeError)` — it killed the whole recovery run."""
    ra = _agent()
    try:
        ra._check_criteria({"beacon_active": True}, {"beacon_active": True})
    except AttributeError as exc:
        raise AssertionError(f"bool criterion still raises: {exc}")


@test
def test_emulator_emits_beacon_active():
    """BUG B's other half: the key SAFE_MODE_HOLD checks must actually exist."""
    em = _emulator()
    e = em.SatelliteEmulator(tick_interval=0.01)
    frame = e._build_frame()
    assert "beacon_active" in frame, "emulator still does not emit beacon_active"

    e.inject_firmware_corruption()
    e._apply_fault_effects()
    assert e._build_frame()["beacon_active"] is False, \
        "beacon should drop with comms during firmware corruption"

    e.apply_recovery("SAFE_MODE_HOLD")
    assert e._build_frame()["beacon_active"] is True, \
        "SAFE_MODE_HOLD must restore the beacon — that is its success criterion"


@test
def test_min_confidence_skip_does_not_uplink_a_stale_procedure():
    """
    THE Prompt 3.2 Bug E acceptance test — confidence 0.75 on software_bug.

    software_bug priorities: [0] OBC_SOFT_REBOOT_v1 (min 0.70),
                             [1] OBC_HARD_RESET_v1  (min 0.80)
    At 0.75 the fallback is skipped. The old code advanced priority_index and
    returned without setting selected_procedure/error/next_step, so
    route_after_select() sent the state to generate_commands with the PREVIOUS
    procedure still in selected_procedure — re-uplinking the procedure that had
    just failed.

    Revert check: delete the "reselect" branch and this fails at the routing
    assertion.
    """
    ra = _agent()

    # index 0 is selected normally at 0.75 (min_confidence 0.70)
    s = _state(ra, "software_bug", confidence=0.75, idx=0)
    s = ra.node_select_procedure(s)
    assert s["selected_procedure"].get("procedure_name") == "OBC_SOFT_REBOOT_v1"
    assert ra.route_after_select(s) == "generate_commands"

    # now the fallback: index 1 requires 0.80, so it must be skipped
    s["priority_index"] = 1
    s["next_step"] = None
    s = ra.node_select_procedure(s)

    assert s["next_step"] == "reselect", \
        f"skip must signal reselect, got next_step={s['next_step']!r}"
    assert not s["selected_procedure"], \
        f"stale procedure left in state: {s['selected_procedure'].get('procedure_name')}"
    route = ra.route_after_select(s)
    assert route == "select_procedure", \
        f"routed to {route!r} after a min_confidence skip — a stale procedure " \
        f"would be uplinked"
    assert s["priority_index"] == 2, "priority_index must advance past the skip"

    # re-entering selection with the advanced index exhausts the list cleanly
    s = ra.node_select_procedure(s)
    assert s["next_step"] == "exhausted"
    assert ra.route_after_select(s) == "report_failure"


@test
def test_fallback_runs_when_success_criteria_fail():
    """
    THE Prompt 3.2 Bug A acceptance test — the primary fails, the FALLBACK runs
    and is recorded in recovery_log.

    Drives the node sequence directly rather than through the compiled graph:
    langgraph is an optional dependency, and the end-to-end path additionally
    depends on Prompt 4.0 (the verification gate currently refuses every
    command before monitoring is reached).

    Revert check: restore `or health == "nominal"` in node_monitor_recovery and
    the monitor declares success on the first poll, so the fallback never runs.
    """
    ra = _agent()
    em = _emulator()

    e = em.SatelliteEmulator(tick_interval=0.02)
    e.inject_firmware_corruption()
    e.start()
    try:
        s = _state(ra, "firmware_corruption", confidence=1.0, idx=0)
        s = ra.node_select_procedure(s)
        primary = s["selected_procedure"]["procedure_name"]
        assert primary == "FIRMWARE_ROLLBACK_v1"

        # Force the primary to fail: monitor against a criterion the emulator
        # cannot satisfy, standing in for a procedure that did not take.
        s["selected_procedure"] = dict(s["selected_procedure"])
        s["selected_procedure"]["success_criteria"] = {"obc_status": "nominal"}
        s["selected_procedure"]["timeout_s"] = 1
        s = ra.node_monitor_recovery(s, e)          # never applied -> still fault

        assert s["recovery_success"] is False, \
            "criteria unmet but recovery reported success (Bug A regression)"
        assert ra.route_after_monitoring(s) == "fallback"

        s = ra.node_fallback(s)
        assert ra.route_after_fallback(s) == "select_procedure"

        s = ra.node_select_procedure(s)
        fallback = s["selected_procedure"].get("procedure_name")
        assert fallback and fallback != primary, \
            f"fallback did not advance: primary={primary} fallback={fallback}"
        assert fallback == "SAFE_MODE_HOLD"

        steps = [entry.get("step") for entry in s["recovery_log"]]
        assert "fallback" in steps, f"fallback not recorded in recovery_log: {steps}"
        assert steps.count("select_procedure") >= 2, \
            "the fallback procedure was not selected through select_procedure"

        # and the fallback genuinely succeeds against its real criteria
        assert e.apply_recovery(fallback) is True
        # Build the frame directly rather than reading the ring buffer: the
        # tick loop may not have run since apply_recovery(), and this assertion
        # is about the procedure's effect, not about tick timing.
        assert ra._check_criteria(e._build_frame(),
                                  s["selected_procedure"]["success_criteria"]) is True
    finally:
        e.stop()


@test
def test_monitor_does_not_accept_nominal_health_as_success():
    """
    BUG A, isolated: health == 'nominal' must not substitute for criteria.
    apply_recovery() resets subsystem statuses, so health flips nominal on the
    first poll — that is exactly how the old `or` made criteria advisory.
    """
    ra = _agent()
    em = _emulator()

    e = em.SatelliteEmulator(tick_interval=0.02)
    e.start()
    try:
        import time as _t
        _t.sleep(0.1)
        assert e.get_overall_health() == "nominal", "fixture assumption changed"

        s = _state(ra, "software_bug", confidence=1.0)
        s["selected_procedure"] = {
            "procedure_name": "X",
            # a criterion the healthy emulator cannot meet
            "success_criteria": {"obc_cpu_pct": "> 999"},
            "timeout_s": 1,
        }
        s = ra.node_monitor_recovery(s, e)
        assert s["recovery_success"] is False, \
            "nominal health still overrides unmet success_criteria"
    finally:
        e.stop()


# ===========================================================================
# Crypto wiring — real hybrid path, no fabricated signatures (Prompt 4.0)
# ===========================================================================

@test
def test_both_trees_expose_the_same_crypto_routes():
    """
    ACCEPTANCE: root main.py and backend/main.py must expose the same
    /crypto/* route set. They did not — root never mounted crypto_router, so
    the real hybrid implementation was unreachable on the tree that boots.
    """
    import re

    def app_routes(p):
        src = (ROOT / p).read_text(encoding="utf-8")
        return set(re.findall(r'@app\.(?:get|post|websocket|put|delete)\("([^"]+)"', src))

    def router_routes(p):
        src = (ROOT / p).read_text(encoding="utf-8")
        prefix = re.search(r"APIRouter\(prefix='([^']+)'", src).group(1)
        return {prefix + m for m in re.findall(r"@router\.(?:get|post)\('([^']+)'", src)}

    root_set = app_routes("main.py") | router_routes("crypto/crypto_routes.py")
    be_set = app_routes("backend/main.py") | router_routes("backend/crypto/crypto_routes.py")

    assert root_set == be_set, (
        f"route sets differ\n  root only: {sorted(root_set - be_set)}\n"
        f"  backend only: {sorted(be_set - root_set)}")

    for path in ("/crypto/sign", "/crypto/verify", "/crypto/ledger"):
        assert path in root_set, f"{path} missing from root"


def _code_only(rel_path: str) -> str:
    """
    Source with comments and docstrings stripped.

    These tests assert on what the code DOES. Searching raw text matches the
    comments that document the removed behaviour — every one of these three
    tests failed on its own explanatory comment the first time it ran.
    """
    import ast
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                body.pop(0)
    return ast.unparse(tree)


@test
def test_root_main_mounts_the_real_crypto_router():
    src = _code_only("main.py")
    assert "include_router(crypto_router)" in src, \
        "root main.py does not mount crypto_router — the hybrid implementation " \
        "is unreachable and /crypto/* is mock-only"
    assert "MOCK_ML_DSA_" not in src, \
        "root main.py still fabricates a signature in executable code"


@test
def test_sign_refuses_to_fabricate_when_crypto_is_mocked():
    """
    ACCEPTANCE: with the crypto backend mocked and DEADSAT_ALLOW_MOCK_SIGNING
    unset, /crypto/sign must return 503 SIGNING_UNAVAILABLE rather than a
    fabricated signature that the verification gate then rejects.
    """
    import types
    sys.path.insert(0, str(ROOT / "crypto"))
    import mock_oqs_nacl

    if not mock_oqs_nacl.is_mock_active():
        raise _Skip("real liboqs/PyNaCl installed — mock refusal path not exercised")

    class _HTTPException(Exception):
        def __init__(self, status_code, detail):
            self.status_code, self.detail = status_code, detail

    src = (ROOT / "crypto" / "crypto_routes.py").read_text(encoding="utf-8")
    i = src.index("def _mock_signing_allowed")
    j = src.index("    cmd_bytes = bytes.fromhex")
    body = "\n".join(l for l in src[i:j].split("\n") if not l.strip().startswith("@"))
    body = body.replace("def sign(req: SignRequest, request: Request):",
                        "def sign(req, request):")
    mod = types.ModuleType("_sign_under_test")
    mod.__dict__.update({
        "os": __import__("os"), "mock_oqs_nacl": mock_oqs_nacl,
        "HTTPException": _HTTPException,
        "logger": types.SimpleNamespace(error=lambda *a, **k: None,
                                        info=lambda *a, **k: None),
        "_ensure_init": lambda: None,
    })
    exec(compile(body + "\n    return 'SIGNED'", "_sign_under_test", "exec"), mod.__dict__)

    import os as _os
    prev = _os.environ.pop("DEADSAT_ALLOW_MOCK_SIGNING", None)
    try:
        req = types.SimpleNamespace(command_bytes="4142")
        try:
            mod.sign(req, None)
            raise AssertionError("sign() fabricated a signature with mock crypto "
                                 "and no opt-in")
        except _HTTPException as exc:
            assert exc.status_code == 503, f"expected 503, got {exc.status_code}"
            assert exc.detail["reason"] == "SIGNING_UNAVAILABLE", \
                f"expected SIGNING_UNAVAILABLE, got {exc.detail['reason']}"

        # opt-in permits it
        _os.environ["DEADSAT_ALLOW_MOCK_SIGNING"] = "1"
        assert mod.sign(req, None) == "SIGNED", \
            "DEADSAT_ALLOW_MOCK_SIGNING=1 must still allow bench signing"
    finally:
        _os.environ.pop("DEADSAT_ALLOW_MOCK_SIGNING", None)
        if prev is not None:
            _os.environ["DEADSAT_ALLOW_MOCK_SIGNING"] = prev


@test
def test_agent_verify_matches_the_router_schema():
    """
    The agent's verification gate must send the field names crypto_routes
    .VerifyRequest declares. It sent {command_bytes, ml_dsa_sig, ed25519_sig,
    nonce} — the shape of main.py's removed proxy handler — which against the
    real router is a 422 that surfaces as VERIFY_UNAVAILABLE and looks like the
    crypto service being down rather than a contract mismatch.
    """
    import ast

    # Fields the agent actually puts in the verify POST body.
    agent_tree = ast.parse((ROOT / "agents" / "recovery_agent.py")
                           .read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(agent_tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_verify_command")
    sent = {k.value for node in ast.walk(fn) if isinstance(node, ast.Dict)
            for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}

    # Fields VerifyRequest declares as required (no default).
    router_tree = ast.parse((ROOT / "crypto" / "crypto_routes.py")
                            .read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(router_tree)
               if isinstance(n, ast.ClassDef) and n.name == "VerifyRequest")
    required = {n.target.id for n in cls.body
                if isinstance(n, ast.AnnAssign) and n.value is None}

    missing = required - sent
    assert not missing, (
        f"_verify_command omits required VerifyRequest fields: {sorted(missing)}\n"
        f"  sends:    {sorted(sent)}\n  requires: {sorted(required)}")


@test
def test_check_command_no_longer_rubber_stamps():
    """
    /crypto/check-command reported `valid: true` for any non-empty signature
    string — `is_valid = req.signed and len(req.signature) > 0`. No
    cryptography was involved, on an endpoint named check-command.
    """
    import ast
    for path in ("main.py", "backend/main.py"):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == "check_command"), None)
        assert fn is not None, f"{path}: check_command handler not found"

        # strip the docstring, which quotes the old rubber-stamp for context
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)):
            fn.body.pop(0)
        code = ast.unparse(fn)

        assert "req.signed" not in code, \
            f"{path}: check-command still derives validity from req.signed"
        assert "NOT VERIFIED" in code, \
            f"{path}: check-command does not state that it performs no check"


# ===========================================================================
# Nonce replay protection (Prompt 4.1)
# ===========================================================================

def _nonce_mgr():
    sys.path.insert(0, str(ROOT / "crypto"))
    from nonce import NonceManager
    return NonceManager()


@test
def test_concurrent_identical_nonces_exactly_one_succeeds():
    """
    THE Prompt 4.1 Bug A acceptance test.

    Revert check: restore the get()-then-set() pair and this fails — both
    racers read None and both proceed.
    """
    import threading
    for _ in range(5):                       # repeat: races are probabilistic
        nm = _nonce_mgr()
        nonce = nm.generate_nonce()
        results = []
        barrier = threading.Barrier(64)      # release all threads together

        def claim():
            barrier.wait()
            results.append(nm.use_nonce(nonce))

        threads = [threading.Thread(target=claim) for _ in range(64)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        accepted = sum(1 for r in results if r)
        assert len(results) == 64, f"only {len(results)} threads reported"
        assert accepted == 1, \
            f"{accepted} of 64 concurrent claims accepted the same nonce"


@test
def test_rejected_replay_does_not_overwrite_the_stored_nonce():
    """
    BUG B: a failed compare_digest fell through to an unconditional set(),
    replacing the stored nonce — so the third presentation was accepted again.
    """
    nm = _nonce_mgr()
    nonce = nm.generate_nonce()

    assert nm.use_nonce(nonce) is True, "first use must be accepted"
    assert nm.use_nonce(nonce) is False, "replay must be rejected"
    assert nm.use_nonce(nonce) is False, \
        "third use accepted — the rejected replay overwrote the stored nonce"
    assert nm.is_used(nonce) is True


@test
def test_in_memory_nonce_fallback_works_at_all():
    """
    Not in the prompt: with redis absent, __init__ returned before assigning
    self.redis while use_nonce() called self.redis.get() unconditionally, and
    mock_store was allocated but never read. AttributeError -> 500 from
    /crypto/sign on any machine without redis.
    """
    nm = _nonce_mgr()
    if not getattr(nm, "is_mock", False):
        raise _Skip("redis is available — in-memory fallback not exercised")

    a, b = nm.generate_nonce(), nm.generate_nonce()
    assert nm.use_nonce(a) is True
    assert nm.use_nonce(b) is True, "distinct nonces must both be accepted"
    assert nm.use_nonce(a) is False
    assert nm.is_used(a) is True and nm.is_used(nm.generate_nonce()) is False


@test
def test_nonce_is_consumed_at_verify_not_at_sign():
    """
    BUG C acceptance: /verify must reject a replayed nonce.

    sign() previously consumed it, which only ever caught our own duplicate
    signing calls — a replayed command goes straight to /verify and never
    passes through sign() at all.
    """
    import ast

    routes = (ROOT / "crypto" / "crypto_routes.py").read_text(encoding="utf-8")
    tree = ast.parse(routes)

    def body_of(name):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.unparse(fn)

    assert "use_nonce" not in body_of("sign"), \
        "sign() still consumes the nonce — replay protection is on the wrong " \
        "side of the trust boundary"
    assert "use_nonce" in body_of("verify"), \
        "verify() does not consume the nonce — replays are not detected"

    # VerifyRequest must carry it, and the agent must send it
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "VerifyRequest")
    assert any(isinstance(n, ast.AnnAssign) and n.target.id == "nonce"
               for n in cls.body), "VerifyRequest has no nonce field"

    agent = ast.parse((ROOT / "agents" / "recovery_agent.py")
                      .read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(agent)
              if isinstance(n, ast.FunctionDef) and n.name == "_verify_command")
    sent = {k.value for node in ast.walk(fn) if isinstance(node, ast.Dict)
            for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert "nonce" in sent, \
        "_verify_command does not send the nonce — every verify would be " \
        "MISSING_NONCE"


@test
def test_verify_module_does_not_kill_the_process():
    """
    BUG D: sys.exit(1) inside verify.py on MechanismNotSupportedError killed
    uvicorn mid-request — emulator, agent and every WebSocket client with it,
    and no HTTP response to explain why.
    """
    import ast
    tree = ast.parse((ROOT / "crypto" / "verify.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "verify_command")
    code = ast.unparse(fn)
    assert "sys.exit" not in code, \
        "verify_command() still calls sys.exit() — a library must not kill " \
        "the process that imported it"
    assert "raise RuntimeError" in code, \
        "verify_command() should raise so the caller can return a 503"


@test
def test_no_unbacked_timing_attack_claim():
    """
    BUG E: verify.py claimed "Uses hmac.compare_digest() to prevent timing
    attacks" and imported hmac to say so, but never called it.
    """
    import ast
    src = (ROOT / "crypto" / "verify.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    imports_hmac = any(
        (isinstance(n, ast.Import) and any(a.name == "hmac" for a in n.names))
        or (isinstance(n, ast.ImportFrom) and n.module == "hmac")
        for n in ast.walk(tree))
    # _code_only strips docstrings — the note explaining the removal mentions
    # compare_digest by name, and matching that is how this test first failed.
    calls_compare = "compare_digest(" in _code_only("crypto/verify.py")

    assert calls_compare == imports_hmac, (
        f"hmac imported={imports_hmac} but compare_digest called={calls_compare} "
        f"— one implies a protection the other does not provide")
    if not calls_compare:
        # the claim must not survive in the prose either, except where it is
        # explicitly documented as having been removed
        for line in src.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped.startswith("Uses hmac.compare_digest()"):
                raise AssertionError(
                    "docstring still claims compare_digest is used")


# ===========================================================================
# WebSocket auth, TTL refresh, route uniqueness (Prompt 4.2)
# ===========================================================================

def _ws_auth_fn():
    """Load _ws_authenticate from main.py without importing FastAPI."""
    import asyncio as _a, json as _j, types, pathlib
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    i = src.index("async def _ws_authenticate")
    j = src.index("\n\n# ──────────────────────────────────────────────\n# Request Models", i)
    mod = types.ModuleType("_wsauth")
    mod.__dict__.update({"asyncio": _a, "json": _j, "WebSocket": object,
                         "cfg": types.SimpleNamespace(API_KEY=""),
                         "print": lambda *a, **k: None})
    exec(compile(src[i:j], "_wsauth", "exec"), mod.__dict__)
    return mod


class _FakeWS:
    def __init__(self, qp=None, first=None, hang=False):
        self.query_params = qp or {}
        self._first, self._hang = first, hang
        self.accepted = False
        self.closed = None

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        import asyncio as _a
        if self._hang:
            await _a.sleep(10)
        if self._first is None:
            raise RuntimeError("no message")
        return self._first

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)


@test
def test_websocket_rejects_unauthenticated_when_key_is_set():
    """
    THE Prompt 4.2 Bug A acceptance test.

    require_api_key() is a FastAPI dependency, so it only ever guarded REST.
    /ws/telemetry and /ws/events accepted any connection — with
    DEADSAT_API_KEY set, anyone on the LAN could still stream live telemetry
    and watch every recovery event.
    """
    import asyncio
    mod = _ws_auth_fn()

    def run(key, ws):
        mod.cfg.API_KEY = key
        return asyncio.run(mod._ws_authenticate(ws))

    cases = [
        # (api_key, socket,                              expect_ok, expect_close)
        ("",       _FakeWS(),                            True,  None),
        ("",       _FakeWS({"api_key": "anything"}),     True,  None),
        ("SECRET", _FakeWS({"api_key": "SECRET"}),       True,  None),
        ("SECRET", _FakeWS({"api_key": "wrong"}),        False, 1008),
        ("SECRET", _FakeWS(first=None),                  False, 1008),
        ("SECRET", _FakeWS(first='{"api_key":"SECRET"}'), True, None),
        ("SECRET", _FakeWS(first='{"api_key":"bad"}'),   False, 1008),
        ("SECRET", _FakeWS(first="not json"),            False, 1008),
    ]
    for key, ws, want_ok, want_close in cases:
        got = run(key, ws)
        close = ws.closed[0] if ws.closed else None
        assert got is want_ok, f"key={key!r} qp={ws.query_params}: authed {got}, want {want_ok}"
        assert close == want_close, \
            f"key={key!r}: closed with {close}, want {want_close}"


@test
def test_websocket_auth_times_out_rather_than_hanging():
    """A client that connects and says nothing must not hold the socket open."""
    import asyncio, time
    mod = _ws_auth_fn()
    mod.cfg.API_KEY = "SECRET"
    ws = _FakeWS(hang=True)
    t0 = time.time()
    ok = asyncio.run(mod._ws_authenticate(ws))
    elapsed = time.time() - t0
    assert ok is False and ws.closed and ws.closed[0] == 1008
    assert 4.0 < elapsed < 8.0, f"auth took {elapsed:.1f}s, expected a ~5s timeout"


@test
def test_connection_manager_does_not_double_accept():
    """
    _ws_authenticate() accepts the socket; ConnectionManager.connect_* must
    not accept again (Starlette raises on a second accept).
    """
    import ast
    for path in ("main.py", "backend/main.py"):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        for name in ("connect_telemetry", "connect_events"):
            fn = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            assert "accept()" not in ast.unparse(fn), \
                f"{path}:{name} still calls accept() — double accept raises"


@test
def test_frontend_sends_api_key_on_websocket():
    """
    Enforcing the key server-side locks out the dashboard unless the client
    sends it. A browser cannot set headers on a WebSocket handshake, so it
    must go in the query string.
    """
    src = (ROOT / "frontend" / "api.ts").read_text(encoding="utf-8")
    assert "api_key=" in src, \
        "frontend/api.ts does not send api_key on the WebSocket — with " \
        "DEADSAT_API_KEY set the dashboard cannot connect"


@test
def test_system_links_reports_auth_state():
    """BUG C: a key mismatch must be diagnosable from the dashboard."""
    import ast
    for path in ("main.py", "backend/main.py"):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "system_links")
        code = ast.unparse(fn)
        assert "'auth'" in code or '"auth"' in code, \
            f"{path}: /system/links does not report an auth link"
        args = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        assert "x_api_key" in args, \
            f"{path}: system_links cannot see the supplied key"


@test
def test_signatures_are_refreshed_at_transmission_time():
    """
    BUG B: crypto/sign.py stamps a 120 s TTL, but the agent signs at node 4
    and may then wait at node 5 for a contact window up to 24 h out. Every
    such command expired before transmission and verify_command() rejected it
    as COMMAND_EXPIRED.
    """
    import time, types
    stub = types.ModuleType("httpx")
    calls = {"n": 0}

    class _Resp:
        def __init__(self, payload): self._p = payload
        def raise_for_status(self): pass
        def json(self): return self._p

    def _post(url, json=None, timeout=None):
        calls["n"] += 1
        return _Resp({"ml_dsa_sig": "aa" * 16, "ed25519_sig": "bb" * 16,
                      "nonce": f"fresh{calls['n']}", "ledger_id": calls["n"],
                      "valid_until": int(time.time()) + 120})

    stub.post = _post
    sys.modules.setdefault("httpx", stub)
    sys.modules["httpx"].post = _post

    ra = _agent()
    now = int(time.time())
    state = {
        "signed_commands": [
            {"cmd": "FRESH",    "valid_until": now + 100,  "nonce": "old1"},
            {"cmd": "EXPIRED",  "valid_until": now - 5000, "nonce": "old2"},
            {"cmd": "EXPIRING", "valid_until": now + 3,    "nonce": "old3"},
        ],
        "recovery_log": [],
    }
    before = calls["n"]
    err = ra._refresh_expiring_signatures(state)

    assert err is None, f"re-signing reported an error: {err}"
    assert calls["n"] - before == 2, \
        f"{calls['n'] - before} commands re-signed, expected 2 (EXPIRED + EXPIRING)"

    by_cmd = {c["cmd"]: c for c in state["signed_commands"]}
    assert by_cmd["FRESH"]["nonce"] == "old1", "a valid signature was needlessly reissued"
    for name in ("EXPIRED", "EXPIRING"):
        assert by_cmd[name]["nonce"].startswith("fresh"), f"{name} was not re-signed"
        assert by_cmd[name]["valid_until"] > now, f"{name} still carries a dead TTL"
    assert any(e["step"] == "refresh_signatures" for e in state["recovery_log"])


@test
def test_no_duplicate_route_paths_in_either_tree():
    """
    BUG D as stated ("/crypto/check-command registered twice") does not exist:
    the router defines /crypto/check-rogue. This asserts the property the bug
    was about — no path registered twice in either tree — so a genuine
    duplicate would be caught.
    """
    import collections, re

    for tree, main_f, routes_f in (
            ("root", "main.py", "crypto/crypto_routes.py"),
            ("backend", "backend/main.py", "backend/crypto/crypto_routes.py")):
        src = (ROOT / main_f).read_text(encoding="utf-8")
        paths = re.findall(
            r'@app\.(?:get|post|websocket|put|delete)\("([^"]+)"', src)
        rsrc = (ROOT / routes_f).read_text(encoding="utf-8")
        prefix = re.search(r"APIRouter\(prefix='([^']+)'", rsrc).group(1)
        paths += [prefix + m
                  for m in re.findall(r"@router\.(?:get|post)\('([^']+)'", rsrc)]

        dupes = {p: n for p, n in collections.Counter(paths).items() if n > 1}
        assert not dupes, f"{tree}: duplicate route registrations {dupes}"


# ===========================================================================
# Claims reconciliation (Prompt 5.1)
# ===========================================================================

@test
def test_readme_feature_count_matches_feature_spec():
    """
    README claimed "13 telemetry features" describing V1's subsystem telemetry.
    V2 — the model actually trained and shipped — uses 11 orbital elements.
    """
    _need("pandas")
    sys.path.insert(0, str(ROOT / "models"))
    from feature_spec import FEATURE_COLS

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    n = len(FEATURE_COLS)
    assert f"{n} orbital-element features" in readme, \
        f"README does not state the real feature count ({n})"
    assert "13 telemetry features" not in readme.split("Corrected.")[0], \
        "the uncorrected '13 telemetry features' claim is still in the README body"


@test
def test_readme_setup_paths_exist():
    """
    README told readers to `cd frontend/dashboard` and `cd frontend/operator`.
    Neither directory exists — anyone following it verbatim could not start the
    project.
    """
    import re
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for path in re.findall(r"cd (frontend/[\w/-]+)", readme):
        assert (ROOT / path).is_dir(), f"README says `cd {path}` but it does not exist"


@test
def test_agent_header_makes_no_blanket_fixed_claim():
    """
    The module header opened "All bugs fixed, all improvements applied" while
    two of its listed improvements were false of the code beneath it.
    """
    import ast
    tree = ast.parse((ROOT / "agents" / "recovery_agent.py").read_text(encoding="utf-8"))
    header = ast.get_docstring(tree) or ""

    # The claim must not be made in the module's identity block (the title
    # lines). It may still appear below as a quotation explaining what was
    # corrected — which is how this test first failed, on the very text
    # documenting the fix.
    opening = "\n".join(header.splitlines()[:3])
    assert "All bugs fixed" not in opening, \
        f"agent header still opens with a blanket 'all fixed' claim:\n{opening}"
    assert "Outstanding" in header, \
        "agent header does not distinguish outstanding work from completed work"


@test
def test_frontend_declares_no_unused_dependencies():
    """
    package.json declared @google/genai, express and dotenv; nothing imported
    them. An unused AI SDK in a project whose thesis is deterministic on-board
    recovery invites exactly the wrong question.
    """
    import json
    pkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    sources = "\n".join(
        p.read_text(encoding="utf-8")
        for p in list((ROOT / "frontend").glob("*.ts"))
        + list((ROOT / "frontend").glob("*.tsx"))
        + list((ROOT / "frontend" / "components").glob("*.tsx")))

    for dep in ("@google/genai", "express", "dotenv"):
        assert dep not in pkg.get("dependencies", {}), \
            f"{dep} is declared but nothing imports it"
        assert dep not in sources, f"{dep} is imported but no longer declared"


@test
def test_simulated_telemetry_fields_are_labelled():
    """
    frameToTelemetryState() fabricates lat/lng/altitude/velocity — the emulator
    models subsystems, not orbital position. They must be identifiable rather
    than presented with the same authority as measured values.
    """
    src = (ROOT / "frontend" / "api.ts").read_text(encoding="utf-8")
    assert "SIMULATED_TELEMETRY_FIELDS" in src, \
        "api.ts does not expose which telemetry fields are fabricated"
    for field in ("lat", "lng", "altitude", "velocity"):
        assert f"'{field}'" in src.split("SIMULATED_TELEMETRY_FIELDS")[1][:300], \
            f"{field} missing from SIMULATED_TELEMETRY_FIELDS"


def _ts_code_only(rel_path: str) -> str:
    """
    TypeScript/TSX source with // and /* */ comments removed.

    The TS counterpart of _code_only(). Assertions about what a component DOES
    must not match the comment explaining what was removed — the comment
    naturally quotes the offending string, and several of these tests failed
    on their own documentation before this existed.
    """
    import re
    src = (ROOT / rel_path).read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)      # block comments
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)      # whole-line //
    return src


@test
def test_readme_claims_no_fabricated_boot_transcript():
    """
    "Live System Proof" opened "here's what actually prints on boot" above
    transcripts the code does not produce: `[CY-1]` banners (actual: `[CRYPTO]`),
    four self-check lines that exist nowhere, a recovery JSON using four field
    names the log writer never emits, and "baseline seeded from SatNOGS" when
    main.py disables SatNOGS seeding.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    i = readme.index("Live System Proof", readme.index("## ", readme.index("Live System Proof")))
    section = readme[i:i + 6000]

    for phantom in ("[CY-1] SYSTEM SELF-CHECK",
                    "[EMULATOR] Digital twin initialized",
                    "[API] FastAPI server running on"):
        assert phantom not in section, \
            f"README still shows a banner the code never prints: {phantom!r}"

    # the recovery JSON must use the real schema
    assert "procedure_used" in section, "README shows a recovery log with invented keys"
    for invented in ('"subsystem"', '"signature_algorithms"', '"ledger_verified"'):
        assert invented not in section, \
            f"README recovery JSON still contains {invented}, which _persist_log never writes"


@test
def test_no_grpc_claim_without_grpc():
    """LandingPage advertised a "secure gRPC gateway". There is no gRPC here."""
    src = (ROOT / "frontend" / "components" / "LandingPage.tsx").read_text(encoding="utf-8")
    assert "gRPC" not in src, "LandingPage claims gRPC; the transport is REST + WebSockets"


@test
def test_ui_does_not_fabricate_crypto_confirmations():
    """
    Two components wrote log lines asserting cryptographic operations that had
    not happened — with live timestamps, indistinguishable from real output.
    """
    dash = _ts_code_only("frontend/components/SatelliteDashboard.tsx")
    assert "Verified response signature index key" not in dash, \
        "SatelliteDashboard still fabricates a crypto verification log line"
    assert "/api/recovery/trigger" not in dash, \
        "SatelliteDashboard logs a route that does not exist"

    panel = _ts_code_only("frontend/components/OperatorControlPanel.tsx")
    assert "signatures verified with high-entropy lattice seed" not in panel, \
        "OperatorControlPanel still seeds a fabricated CY-1 verification alert"


@test
def test_ground_station_location_is_consistent():
    """
    The dashboard header read "NEW DELHI_HQ / 28.61 / 77.20" while every AOS
    window is computed from GROUND_STATION in contact_calculator.py.
    """
    import re
    calc = (ROOT / "emulator" / "contact_calculator.py").read_text(encoding="utf-8")
    lat = float(re.search(r'"lat_deg":\s*([\d.]+)', calc).group(1))
    lon = float(re.search(r'"lon_deg":\s*([\d.]+)', calc).group(1))

    app = (ROOT / "frontend" / "App.tsx").read_text(encoding="utf-8")
    m = re.search(r"LAT:\s*([\d.]+)\s*/\s*LNG:\s*([\d.]+)", app)
    assert m, "App.tsx no longer displays a station location"
    ui_lat, ui_lon = float(m.group(1)), float(m.group(2))

    assert abs(ui_lat - lat) < 0.1 and abs(ui_lon - lon) < 0.1, \
        f"dashboard shows {ui_lat}/{ui_lon} but contact windows use {lat}/{lon}"


@test
def test_security_console_badge_is_not_hardcoded_hardened():
    """
    "PQC STATUS: HARDENED" was a static badge, displayed even when the crypto
    backend was the development shim — i.e. when nothing was hardened.
    """
    src = (ROOT / "frontend" / "components" / "SecurityConsole.tsx").read_text(encoding="utf-8")
    i = src.index("PQC STATUS")
    window = src[max(0, i - 700):i + 300]
    assert "cyOnline" in window, \
        "the PQC status badge is not driven by the live crypto state"


# ===========================================================================
# CORS misconfiguration guard (Prompt 0.5)
# ===========================================================================

@test
def test_cors_lan_bind_with_loopback_origins_is_detected():
    import importlib
    import config as cfg

    cases = [
        ("0.0.0.0", ["http://localhost:3000"], True),
        ("0.0.0.0", ["http://127.0.0.1:3000", "http://localhost:5173"], True),
        ("127.0.0.1", ["http://localhost:3000"], False),
        ("0.0.0.0", ["http://192.168.1.60:3000"], False),
        ("0.0.0.0", ["http://localhost:3000", "http://192.168.1.60:3000"], False),
        ("0.0.0.0", ["*"], False),
    ]
    orig_host, orig_cors = cfg.API_HOST, cfg.CORS_ORIGINS
    try:
        for host, origins, expected in cases:
            cfg.API_HOST, cfg.CORS_ORIGINS = host, origins
            got = cfg.cors_is_unreachable_from_lan()
            assert got == expected, \
                f"host={host} origins={origins}: expected {expected}, got {got}"
    finally:
        cfg.API_HOST, cfg.CORS_ORIGINS = orig_host, orig_cors
        importlib.reload(cfg)


# ===========================================================================

def main() -> int:
    print("=" * 72)
    print("test_units.py")
    print("=" * 72)

    p = sum(1 for _, s, _ in _results if s == "pass")
    f = sum(1 for _, s, _ in _results if s == "fail")
    s = sum(1 for _, s, _ in _results if s == "skip")

    print("=" * 72)
    print(f"Results: {p} passed, {f} failed, {s} skipped")
    if s:
        print("\nSkipped tests did NOT run. Install torch + scikit-learn to")
        print("execute them:  pip install -r requirements.txt")
    print("=" * 72)
    return 1 if f else 0


if __name__ == "__main__":
    raise SystemExit(main())
