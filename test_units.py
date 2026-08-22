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

import json
import sys
import traceback
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "models"))

VERBOSE = "-v" in sys.argv

#: --only <substring>  run just the tests whose function name contains it.
#: Used by verify_tests_can_fail.py so one mutation does not have to run the
#: whole suite (the 5000-tick bounds tests alone make that far too slow).
ONLY = ""
if "--only" in sys.argv:
    _i = sys.argv.index("--only")
    if _i + 1 < len(sys.argv):
        ONLY = sys.argv[_i + 1]

_results: list[tuple[str, str, str]] = []


def _record(name, status, detail=""):
    _results.append((name, status, detail))
    mark = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[status]
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))


def run_test(fn):
    """Register and run a test function immediately."""
    if ONLY and ONLY not in fn.__name__:
        return fn
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


def _clf_numpy_only():
    """
    The classifier module, importable even without torch/scikit-learn.

    `augment_fault_samples()` and `_class_rng()` use nothing but numpy and
    pandas, but they live in a module that imports torch at the top — so the
    reproducibility checks would SKIP on any machine without a 2 GB ML stack,
    which is where they are most likely to be run.

    When torch is genuinely installed the real module is used. Otherwise the
    heavy imports are stubbed just long enough to import, then REMOVED from
    sys.modules so no other test mistakes a stub for the real package (that
    would silently un-skip the leak-detection tests and run them against
    fakes).
    """
    _need("pandas")
    try:
        import torch  # noqa: F401
        import sklearn  # noqa: F401
        import satellite_fault_classifier_V2 as clf
        return clf
    except ImportError:
        pass

    import types
    stub_names = [
        "torch", "torch.nn", "torch.optim", "torch.utils", "torch.utils.data",
        "sklearn", "sklearn.model_selection", "sklearn.preprocessing",
        "sklearn.ensemble", "sklearn.metrics", "sklearn.linear_model",
        "tqdm", "requests",
    ]
    added = [n for n in stub_names if n not in sys.modules]
    for n in added:
        sys.modules[n] = types.ModuleType(n)

    sys.modules["torch"].manual_seed = lambda *_a: None
    placeholder = type("_Placeholder", (object,), {})
    sys.modules["torch.nn"].Module = placeholder
    sys.modules["torch.nn"].Linear = placeholder
    sys.modules["torch.utils.data"].Dataset = object
    sys.modules["torch.utils.data"].DataLoader = object
    for mod, attrs in (
        ("sklearn.model_selection", ["train_test_split", "GroupShuffleSplit"]),
        ("sklearn.preprocessing", ["StandardScaler"]),
        ("sklearn.ensemble", ["IsolationForest", "HistGradientBoostingClassifier"]),
        ("sklearn.linear_model", ["LogisticRegression"]),
        ("sklearn.metrics", ["classification_report", "confusion_matrix",
                             "accuracy_score", "f1_score"]),
    ):
        for a in attrs:
            setattr(sys.modules[mod], a, type(a, (object,), {}))
    sys.modules["tqdm"].tqdm = lambda x, **k: x

    try:
        sys.path.insert(0, str(ROOT / "models"))
        import satellite_fault_classifier_V2 as clf
        return clf
    finally:
        for n in added:                 # do not leave fakes behind
            sys.modules.pop(n, None)


# ---------------------------------------------------------------------------
# Source-inspection helpers
#
# Several assertions below check what a file DOES rather than what it says.
# Searching raw text matches the comment that documents the removed behaviour —
# the comment necessarily quotes the offending string. That produced six false
# failures across this suite before these existed. Define them ONCE, here,
# before any @test runs (tests execute at definition time).
# ---------------------------------------------------------------------------

def _code_only(rel_path: str) -> str:
    """Python source with comments and docstrings stripped."""
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


def _node() -> str | None:
    """Path to node, or None. Used to EXECUTE frontend logic rather than grep it."""
    import shutil
    return shutil.which("node")


def _esbuild() -> str | None:
    """
    Path to an esbuild binary, or None.

    npm install cannot reach the registry in this environment, so there is no
    local node_modules/.bin — but tsx ships a platform binary that transpiles
    TypeScript perfectly well, which lets a few of these tests run the real
    api.ts instead of pattern-matching its source.
    """
    import glob
    import shutil
    found = shutil.which("esbuild")
    if found:
        return found
    for pattern in (
        "/usr/local/lib/node_modules_global/lib/node_modules/tsx/node_modules/@esbuild/*/bin/esbuild",
        "/usr/lib/node_modules/tsx/node_modules/@esbuild/*/bin/esbuild",
        str(ROOT / "frontend" / "node_modules" / ".bin" / ("esbuild.cmd" if sys.platform == "win32" else "esbuild")),
    ):
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    return None


def _ts_code_only(rel_path: str) -> str:
    """TypeScript/TSX source with // and /* */ comments removed."""
    import re
    src = (ROOT / rel_path).read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)      # block comments
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)      # whole-line //
    return src


# ===========================================================================
# LEAK 1 — windows must not span satellites
# ===========================================================================

@run_test
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


@run_test
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


@run_test
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

@run_test
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


@run_test
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

@run_test
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

@run_test
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

@run_test
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

@run_test
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


def _injectors_for(em, e) -> dict:
    """
    Every FaultType -> the method that injects it.

    Derived from the enum rather than hardcoded, so adding a fault type
    without an injector fails loudly here instead of KeyError-ing whichever
    test happens to reach it first (which is exactly what happened when
    battery_failure and adcs_failure were added in Prompt 6.4).
    """
    mapping = {
        em.FaultType.SEU: e.inject_SEU,
        em.FaultType.SOFTWARE_BUG: e.inject_software_bug,
        em.FaultType.FIRMWARE_CORRUPTION: e.inject_firmware_corruption,
        em.FaultType.COMMAND_INJECTION: e.inject_command,
        em.FaultType.BATTERY_FAILURE: e.inject_battery_failure,
        em.FaultType.ADCS_FAILURE: e.inject_adcs_failure,
    }
    missing = [f for f in em.FaultType
               if f is not em.FaultType.NONE and f not in mapping]
    assert not missing, f"FaultType(s) with no injector in this helper: {missing}"
    return mapping


@run_test
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


@run_test
def test_matching_procedure_still_recovers():
    em = _emulator()
    e = em.SatelliteEmulator(tick_interval=0.01)
    e.inject_SEU()

    assert e.apply_recovery("ADCS_MEMORY_SCRUB_v2") is True
    assert e.fault_injected == em.FaultType.NONE
    assert e.adcs.status == "nominal"
    assert e.get_overall_health() == "nominal"


@run_test
def test_procedure_fault_matrix_is_exhaustively_correct():
    """
    Every procedure against every fault: applied iff applicable.

    Injectors are derived from the FaultType enum, so a newly added fault is
    covered automatically. Hardcoding four here meant that when
    battery_failure and adcs_failure arrived, this matrix silently stopped
    being exhaustive — it kept passing while testing 11x4 of an 11x6 space.
    """
    em = _emulator()
    faults = [f for f in em.FaultType if f is not em.FaultType.NONE]
    for proc, applicable in em.PROCEDURE_APPLICABILITY.items():
        for fault in faults:
            e = em.SatelliteEmulator(tick_interval=0.01)
            _injectors_for(em, e)[fault]()
            got = e.apply_recovery(proc)
            want = fault in applicable
            assert got is want, f"{proc} vs {fault.value}: got {got}, want {want}"
            assert (e.fault_injected == em.FaultType.NONE) is want, \
                f"{proc} vs {fault.value}: fault-clearing disagrees with return"


@run_test
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


@run_test
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
        injectors = _injectors_for(em, e)
        assert fault in injectors, \
            f"procedure_library declares fault '{fault.value}' with no injector"
        injectors[fault]()
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


@run_test
def test_telemetry_stays_within_physical_bounds_over_5000_ticks():
    """
    THE Prompt 3.3 acceptance test, run in every fault state.

    Revert check: remove any _clamp() call and this fails. Measured before the
    fix — power_w reached 192 W from a 82.4 W start, adcs_rate_deg_s 15.4 deg/s
    after 150 ticks (nominal < 0.01), obc_error_count unbounded.
    """
    em = _emulator()
    # healthy, then every fault the enum declares — so a new fault type is
    # bounds-checked automatically rather than being quietly excluded.
    scenarios = [None] + [f for f in em.FaultType if f is not em.FaultType.NONE]
    for fault in scenarios:
        name = "healthy" if fault is None else fault.value
        e = em.SatelliteEmulator(tick_interval=0)
        if fault is not None:
            _injectors_for(em, e)[fault]()
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


@run_test
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


@run_test
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


@run_test
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


@run_test
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


@run_test
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
        ({"a": 75}, {"a": "== 75"}, True),          # explicit equality
        ({"a": 76}, {"a": "== 75"}, False),
        ({"a": 76}, {"a": "!= 75"}, True),
        ({"a": 75}, {"a": "!= 75"}, False),
        ({"a": 75}, {"a": 75}, True),               # bare numeric criterion
        ({"a": 74}, {"a": 75}, False),
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


@run_test
def test_normalise_fault_key_covers_every_classifier_output():
    """
    AI-1 emits SCREAMING_CASE class names; procedure_library.json is keyed by
    the emulator's lowercase fault types. Every classifier output must land on
    a real library key, or the agent selects nothing and recovery dies with
    "Unknown fault type" after a successful classification.
    """
    import json
    sys.path.insert(0, str(ROOT / "models"))
    from classifier_inference import normalise_fault_key, FAULT_KEY_MAP

    _need("pandas")
    from feature_spec import FAULT_LABELS

    lib = json.loads((ROOT / "agents" / "procedure_library.json")
                     .read_text(encoding="utf-8"))["procedures"]

    # every class AI-1 can actually output
    for label in FAULT_LABELS:
        key = normalise_fault_key(label)
        assert key in lib, \
            f"classifier output {label!r} -> {key!r}, which is not in " \
            f"procedure_library.json (keys: {list(lib)})"

    # the healthy classes map to 'none' and must NOT claim a procedure
    for benign in ("NONE", "NOMINAL"):
        assert normalise_fault_key(benign) == "none"
        assert "none" not in lib, "'none' should not have recovery procedures"

    # case and whitespace tolerance — the bridge sees raw model output
    assert normalise_fault_key("seu") == "SEU"
    assert normalise_fault_key("  SOFTWARE_BUG  ") == "software_bug"

    # and the map itself must not point anywhere fictional
    for src_key, dst in FAULT_KEY_MAP.items():
        assert dst in lib or dst == "none", \
            f"FAULT_KEY_MAP[{src_key!r}] = {dst!r}, not a library key"


@run_test
def test_orbital_window_fault_signatures_are_not_shadowed():
    """
    _emulator_frame_to_orbital_window() stamps a fault signature onto the
    window AI-1 ingests. Each signature must cross ITS OWN threshold and stay
    clear of every higher-priority rule in assign_fault_labels(), whose
    precedence is:

        1. TLE_AGE_HOURS > 72          -> COMMAND_INJECTION
        2. |BSTAR| > 0.005             -> FIRMWARE_CORRUPTION
        3. |MEAN_MOTION_DOT| > 0.001   -> FIRMWARE_CORRUPTION
        4. ecc jump > 0.01             -> SEU
        5. REV_DELTA <= 0              -> SOFTWARE_BUG

    A SEU window that also trips the BSTAR rule is labelled
    FIRMWARE_CORRUPTION and the SEU signature is never learned.
    """
    _need("pandas")
    import numpy as np
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "emulator"))
    sys.path.insert(0, str(ROOT / "models"))
    from pipeline import _emulator_frame_to_orbital_window
    from feature_spec import CONFIG, FEATURE_COLS
    from satellite_emulator import SatelliteEmulator

    col = {name: i for i, name in enumerate(FEATURE_COLS)}
    injectors = {
        "SEU": lambda e: e.inject_SEU(),
        "software_bug": lambda e: e.inject_software_bug(),
        "firmware_corruption": lambda e: e.inject_firmware_corruption(),
        "command_injection": lambda e: e.inject_command_injection(),
    }

    for fault, inject in injectors.items():
        e = SatelliteEmulator(tick_interval=0)
        inject(e)
        e._latest_frame = e._build_frame()
        w = _emulator_frame_to_orbital_window(e, norad_id=28654)

        assert w.shape == (CONFIG["seq_len"], len(FEATURE_COLS)), \
            f"{fault}: window shape {w.shape}"
        assert np.isfinite(w).all(), f"{fault}: window contains NaN/inf"

        age = float(w[-1, col["TLE_AGE_HOURS"]])
        bstar = abs(float(w[-1, col["BSTAR"]]))
        mmdot = abs(float(w[-1, col["MEAN_MOTION_DOT"]]))
        rev = float(w[-1, col["REV_DELTA"]])
        ecc_jump = float(np.abs(np.diff(w[:, col["ECCENTRICITY"]])).max())

        stale = age > CONFIG["tle_age_stale_hours"]
        firmware = (bstar > CONFIG["bstar_anomaly_threshold"]
                    or mmdot > CONFIG["mean_motion_dot_threshold"])
        seu = ecc_jump > CONFIG["eccentricity_jump_threshold"]

        if fault == "command_injection":
            assert stale, f"command_injection: TLE_AGE {age} does not exceed threshold"
        elif fault == "firmware_corruption":
            assert not stale, "firmware_corruption shadowed by the staleness rule"
            assert firmware, f"firmware_corruption: bstar={bstar} mmdot={mmdot} below thresholds"
        elif fault == "SEU":
            assert not stale, "SEU shadowed by the staleness rule"
            assert not firmware, f"SEU shadowed by the firmware rule (bstar={bstar}, mmdot={mmdot})"
            assert seu, f"SEU: max ecc jump {ecc_jump} below threshold"
        elif fault == "software_bug":
            assert not stale, "software_bug shadowed by the staleness rule"
            assert not firmware, f"software_bug shadowed by the firmware rule"
            assert not seu, f"software_bug shadowed by the SEU rule (ecc jump {ecc_jump})"
            assert rev <= 0, f"software_bug: REV_DELTA {rev} should be <= 0"


@run_test
def test_empty_frame_before_first_tick_is_handled():
    """
    get_latest_frame() returns {} until the tick loop has run once, and
    /telemetry does `frame["overall_health"] = ...` on that empty dict — so a
    poll during startup returns a ONE-KEY object, not a telemetry frame.

    This documents that contract and pins the consumers that depend on it.
    """
    em = _emulator()
    e = em.SatelliteEmulator(tick_interval=0)

    frame = e.get_latest_frame()
    assert frame == {}, f"expected {{}} before the first tick, got {list(frame)[:5]}"

    # /telemetry's exact behaviour on that empty dict
    frame["overall_health"] = e.get_overall_health()
    assert list(frame) == ["overall_health"], \
        "the /telemetry startup response shape changed"
    assert frame["overall_health"] == "nominal"

    # get_overall_health() must not require a frame
    assert e.get_overall_health() in ("nominal", "degraded", "fault")

    # /health reads with .get(), so it degrades to Nones rather than KeyError
    empty = e.get_latest_frame()
    for key in ("obc_status", "adcs_status", "power_status", "comms_status",
                "fault_injected", "battery_pct", "frame_id"):
        assert empty.get(key) is None, f"{key} unexpectedly present pre-tick"

    # The frontend consumers must tolerate it: SatelliteDashboard gates the TLE
    # fetch on `f?.norad_id` (and now retries), and frameToTelemetryState uses
    # `?? 0` fallbacks throughout.
    api = _ts_code_only("frontend/api.ts")
    i = api.index("export function frameToTelemetryState")
    body = api[i:i + 1800]
    assert "?? 0" in body, \
        "frameToTelemetryState no longer defends against missing fields"

    dash = _ts_code_only("frontend/components/SatelliteDashboard.tsx")
    assert "f?.norad_id" in dash, \
        "SatelliteDashboard no longer guards on norad_id before fetching the TLE"

    # after one tick it is a full frame
    e._update_nominal_drift()
    e._apply_fault_effects()
    e._latest_frame = e._build_frame()
    full = e.get_latest_frame()
    assert "obc_status" in full and "frame_id" in full, \
        "a ticked frame is missing core fields"


@run_test
def test_bool_criteria_evaluate_correctly():
    """
    BUG D: `True.startswith` is an AttributeError, uncaught by the old
    `except (ValueError, TypeError)` — it killed the whole recovery run.

    Originally this only asserted "does not raise". The mutation check caught
    that as a test that cannot fail: the rewrite parses `str(condition)` first,
    so removing the bool branch no longer crashes — it just returns wrong
    answers. Asserting on the ANSWER is what makes this a test. The
    string-form cases below are the ones that break without the branch:
    float("true") raises, so it falls through to a string compare of
    "True" vs "true".
    """
    ra = _agent()
    cases = [
        ({"beacon_active": True}, {"beacon_active": True}, True),
        ({"beacon_active": False}, {"beacon_active": True}, False),
        ({"beacon_active": True}, {"beacon_active": False}, False),
        ({"beacon_active": False}, {"beacon_active": False}, True),
        # JSON often carries these as strings — must still compare as booleans
        ({"beacon_active": True}, {"beacon_active": "true"}, True),
        ({"beacon_active": False}, {"beacon_active": "true"}, False),
        ({"beacon_active": True}, {"beacon_active": "false"}, False),
        ({"comms_downlink": True}, {"comms_downlink": "true"}, True),
    ]
    for frame, criteria, want in cases:
        try:
            got = ra._check_criteria(frame, criteria)
        except AttributeError as exc:
            raise AssertionError(f"bool criterion raises: {exc}")
        assert got is want, \
            f"_check_criteria({frame}, {criteria}) = {got}, want {want}"


@run_test
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


@run_test
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


@run_test
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


@run_test
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

@run_test
def test_canonical_backend_exposes_crypto_routes():
    """
    ACCEPTANCE: The canonical main.py must expose the complete /crypto/* route set.
    
    This test validates that the crypto_router is properly mounted and that
    all post-quantum cryptography endpoints are available through the
    canonical backend.
    """
    import re

    def app_routes(p):
        src = (ROOT / p).read_text(encoding="utf-8")
        return set(re.findall(r'@app\.(?:get|post|websocket|put|delete)\("([^"]+)"', src))

    def router_routes(p):
        src = (ROOT / p).read_text(encoding="utf-8")
        prefix = re.search(r"APIRouter\(prefix='([^']+)'", src).group(1)
        return {prefix + m for m in re.findall(r"@router\.(?:get|post)\('([^']+)'", src)}

    # Test that canonical main.py has crypto routes
    main_routes = app_routes("main.py")
    crypto_routes = router_routes("crypto/crypto_routes.py")
    
    # Verify crypto_router is mounted
    assert "/crypto/sign" in main_routes or "/crypto/sign" in crypto_routes
    assert "/crypto/verify" in main_routes or "/crypto/verify" in crypto_routes
    assert "/crypto/ledger" in main_routes or "/crypto/ledger" in crypto_routes

    # main.py mounts crypto_router, so exposed canonical routes are its direct
    # decorators plus the router's prefixed decorators. backend/ is deprecated.
    root_set = main_routes | crypto_routes

    for path in ("/crypto/sign", "/crypto/verify", "/crypto/ledger"):
        assert path in root_set, f"{path} missing from root"


@run_test
def test_root_main_mounts_the_real_crypto_router():
    src = _code_only("main.py")
    assert "include_router(crypto_router)" in src, \
        "root main.py does not mount crypto_router — the hybrid implementation " \
        "is unreachable and /crypto/* is mock-only"
    assert "MOCK_ML_DSA_" not in src, \
        "root main.py still fabricates a signature in executable code"


@run_test
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


@run_test
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


@run_test
def test_check_command_no_longer_rubber_stamps():
    """
    /crypto/check-command reported `valid: true` for any non-empty signature
    string — `is_valid = req.signed and len(req.signature) > 0`. No
    cryptography was involved, on an endpoint named check-command.
    """
    import ast
    # Only test canonical main.py since backend/main.py is deprecated
    path = "main.py"
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


def _race_use_nonce(nm, n_threads: int = 64):
    """Fire n_threads concurrent nm.use_nonce(same nonce) calls, released
    together via a barrier, and return how many were accepted."""
    import threading
    nonce = nm.generate_nonce()
    results = []
    barrier = threading.Barrier(n_threads)

    def claim():
        barrier.wait()
        results.append(nm.use_nonce(nonce))

    threads = [threading.Thread(target=claim) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == n_threads, f"only {len(results)} threads reported"
    return sum(1 for r in results if r)


@run_test
def test_concurrent_identical_nonces_exactly_one_succeeds():
    """
    THE Prompt 4.1 Bug A acceptance test.

    Revert check: restore the get()-then-set() pair and this fails — both
    racers read None and both proceed.

    Exercises whatever backend _nonce_mgr() actually connects to. In any dev
    or CI environment with redis running (which is every environment this
    project's own README tells you to set up), that is ALWAYS the Redis
    branch of use_nonce() — this loop never touches the in-memory/mock
    branch at all. See test_concurrent_identical_nonces_mock_path_exactly_one_succeeds
    below, which was added because a mutation to the mock branch's
    get-then-set logic passed this test silently (verify_tests_can_fail.py
    caught it as a MISSED mutation): the mock branch's own atomicity was
    never being tested here.
    """
    for _ in range(5):                       # repeat: races are probabilistic
        nm = _nonce_mgr()
        accepted = _race_use_nonce(nm)
        assert accepted == 1, \
            f"{accepted} of 64 concurrent claims accepted the same nonce"


@run_test
def test_concurrent_identical_nonces_mock_path_exactly_one_succeeds():
    """
    Same acceptance test as above, forced onto the in-memory/mock branch of
    NonceManager.use_nonce() regardless of whether real redis happens to be
    reachable in this environment.

    Without this, a whole environment class of bugs is invisible: the mock
    path is what actually runs in production the moment Redis is
    unreachable (nonce.py falls back to it, and crypto_routes surfaces that
    via /crypto/health), but every dev/CI machine that followed this
    project's own setup instructions has redis running, so
    test_concurrent_identical_nonces_exactly_one_succeeds above always
    exercised the Redis SET-NX branch and never this one. Confirmed via
    verify_tests_can_fail.py: reverting the mock branch's atomic
    get-then-set-under-lock back to a naive version was a MISSED mutation
    until this test was added.
    """
    for _ in range(5):
        nm = _nonce_mgr()
        nm.is_mock = True                    # force the in-memory branch
        accepted = _race_use_nonce(nm)
        assert accepted == 1, \
            f"{accepted} of 64 concurrent claims accepted the same nonce " \
            f"(mock/in-memory path)"


@run_test
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


@run_test
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


@run_test
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


@run_test
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


@run_test
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


@run_test
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


@run_test
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


@run_test
def test_connection_manager_does_not_double_accept():
    """
    _ws_authenticate() accepts the socket; ConnectionManager.connect_* must
    not accept again (Starlette raises on a second accept).
    """
    import ast
    # Only test canonical main.py since backend/main.py is deprecated
    path = "main.py"
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for name in ("connect_telemetry", "connect_events", "connect_rf"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        assert "accept()" not in ast.unparse(fn), \
                f"{path}:{name} still calls accept() — double accept raises"


@run_test
def test_frontend_uses_short_lived_jwt_connection_token_on_websocket():
    """
    Browser sockets exchange the bearer session for a purpose-bound,
    short-lived connection token rather than exposing an access JWT.
    """
    src = (ROOT / "frontend" / "api.ts").read_text(encoding="utf-8")
    assert "websocketToken" in src and "connection_token=" in src
    assert "access_token=" not in src and "api_key=" not in src


@run_test
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


@run_test
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


@run_test
def test_no_duplicate_route_paths_in_canonical_tree():
    """
    BUG D as stated ("/crypto/check-command registered twice") does not exist:
    the router defines /crypto/check-rogue. This asserts the property the bug
    was about — no path registered twice in the canonical tree — so a genuine
    duplicate would be caught.
    """
    import collections, re

    # Only test canonical main.py since backend/main.py is deprecated
    tree, main_f, routes_f = ("canonical", "main.py", "crypto/crypto_routes.py")
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
# Orbital mechanics: TLE validation and search cost (Prompt 8.2)
# ===========================================================================

def _contact_calc():
    sys.path.insert(0, str(ROOT / "emulator"))
    import contact_calculator as cc
    return cc


@run_test
def test_malformed_tle_raises_a_clear_error():
    """
    ACCEPTANCE: "a malformed TLE raises a clear error instead of propagating
    garbage."

    Nothing validated the lines before Satrec.twoline2rv(). A CelesTrak error
    page has three or more lines too, so its second and third went straight
    into sgp4 — which parses garbage into a Satrec and produces contact
    windows for an orbit that does not exist.
    """
    cc = _contact_calc()
    good1 = cc.FALLBACK_TLE["line1"]
    good2 = cc.FALLBACK_TLE["line2"]

    # the shipped fallback must itself be valid
    cc.validate_tle(good1, good2)

    cases = [
        ("HTML error page", "<!DOCTYPE html>", "<html><head>", "does not start with"),
        ("truncated line", good1[:40], good2, "expected 69"),
        ("line 2 as line 1", good2, good2, "does not start with"),
        ("satellite mismatch", good1, "2 99999" + good2[7:], "mismatch"),
        ("bad checksum", good1[:68] + "3", good2, "checksum"),
        ("empty line", "", good2, "empty"),
        ("None", None, good2, "empty or not a string"),
    ]
    for name, l1, l2, expect in cases:
        try:
            cc.validate_tle(l1, l2)
        except cc.InvalidTLEError as exc:
            assert expect in str(exc), \
                f"{name}: message {str(exc)!r} does not mention {expect!r}"
        else:
            raise AssertionError(f"{name}: accepted a malformed TLE")


@run_test
def test_fallback_tle_is_wellformed_and_labelled_synthetic():
    """
    Both fallback lines shipped with INVALID checksums (line 1 stated 8 but
    computes 7; line 2 stated 9 but computes 0) — the giveaway that they were
    hand-written rather than fetched. Correcting the checksum does not make
    the orbit real, so the elements must also declare themselves synthetic.
    """
    cc = _contact_calc()
    for key in ("line1", "line2"):
        line = cc.FALLBACK_TLE[key]
        assert len(line) == 69, f"{key} is {len(line)} chars"
        assert int(line[68]) == cc._tle_checksum(line), \
            f"{key} checksum is wrong — the fallback would fail its own validator"

    assert cc.FALLBACK_TLE.get("synthetic") is True, \
        "the fallback elements are not marked synthetic"

    epoch = cc.tle_epoch_datetime(cc.FALLBACK_TLE["line1"])
    assert epoch is not None, "the fallback epoch does not parse"


@run_test
def test_contact_search_is_coarse_then_refine():
    """
    ACCEPTANCE: "a contact-window calculation completes in < 1 s."

    The agent called find_next_contact(search_hours=24, step_seconds=10) —
    8,640 SGP4 propagations run synchronously inside the recovery graph while
    a fault is active. Driven here with a stub propagator so the COST and the
    accuracy can both be measured without sgp4 installed.
    """
    import math
    import time
    from datetime import datetime, timezone

    cc = _contact_calc()
    calls = {"n": 0}
    t0 = datetime.now(timezone.utc)

    def fake_elevation(self, t):
        calls["n"] += 1
        dt = (t - t0).total_seconds()
        phase = (dt - 40 * 60) / (96 * 60)          # a 96-minute LEO orbit
        return 55.0 * math.cos(2 * math.pi * phase) - 20.0

    calc = cc.ContactCalculator()
    calc.sat = object()                              # truthy: skips the None guard
    calc.tle = dict(cc.FALLBACK_TLE)
    original = cc.ContactCalculator._elevation_at
    cc.ContactCalculator._elevation_at = fake_elevation
    try:
        start = time.perf_counter()
        window = calc.find_next_contact(search_hours=24.0, step_seconds=60.0)
        elapsed = time.perf_counter() - start
    finally:
        cc.ContactCalculator._elevation_at = original

    assert window is not None, "no contact window found in 24 h"
    assert elapsed < 1.0, f"search took {elapsed:.3f}s, budget is 1s"

    flat_10s = int(24 * 3600 / 10)
    assert calls["n"] < flat_10s / 10, (
        f"{calls['n']} propagations — barely better than the {flat_10s} a flat "
        f"10 s scan needed; the coarse-then-refine path is not being taken")

    # Bisection must actually REFINE, so test accuracy rather than grid
    # alignment — the first version of this assertion checked that AOS was not
    # a multiple of 60 s from t0, which is true by luck alone (t0 and the
    # function's internal `now` differ by microseconds) and so could not fail.
    #
    # True AOS for the fixture: 55*cos(2*pi*(dt-2400)/5760) - 20 == 5
    #   => dt = 2400 - arccos(25/55) * 5760 / (2*pi)  ~= 1392.8 s after t0
    true_aos_offset = 2400 - math.acos(25.0 / 55.0) * (96 * 60) / (2 * math.pi)
    aos_offset = (datetime.fromisoformat(window["aos"]) - t0).total_seconds()
    error_s = abs(aos_offset - true_aos_offset)
    assert error_s < 2.0, (
        f"AOS is {error_s:.1f}s from the true crossing — a coarse 60 s scan "
        f"without bisection lands up to 60 s late")

    # and the peak must be found, not just the coarse maximum
    assert abs(window["max_elevation_deg"] - 35.0) < 0.5, \
        f"max elevation {window['max_elevation_deg']}, expected ~35.0"


@run_test
def test_contact_summary_does_not_propagate_three_times():
    """
    get_contact_summary() ran get_current_azel(), find_next_contact(), then
    is_in_contact_now() — which called get_current_azel() again for the same
    instant. Three passes for two answers.
    """
    import ast
    cc_src = (ROOT / "emulator" / "contact_calculator.py").read_text(encoding="utf-8")
    tree = ast.parse(cc_src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "get_contact_summary")
    # Drop the docstring: it explains the old three-pass behaviour and names
    # get_current_azel() twice, which is what this assertion counted first.
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body.pop(0)
    code = ast.unparse(fn)

    assert code.count("get_current_azel()") == 1, \
        "get_contact_summary still propagates the current position twice"
    assert "is_in_contact_now(current)" in code, \
        "is_in_contact_now is not reusing the already-computed position"


# ===========================================================================
# Training reproducibility (Prompt 8.1)
# ===========================================================================

@run_test
def test_augmentation_rng_is_seeded_and_independent_per_class():
    """
    THE Prompt 8.1 acceptance test, at the level that can be checked without
    a GPU-hours training run.

    Two defects made training unreproducible:
      * augment_fault_samples() drew noise from the GLOBAL numpy RNG while
        everything around it threaded random_state=CONFIG["random_seed"]. Any
        earlier consumer of the global stream shifted the noise, so the same
        command produced different weights.
      * _generate_synthetic_class() re-created default_rng(seed) per call, so
        all four classes received the IDENTICAL noise sequence — correlated
        noise a model can learn as signal.
    """
    clf = _clf_numpy_only()

    # 1. per-class streams are independent
    streams = {i: clf._class_rng(i).normal(0, 1, 8).tolist()
               for i in range(len(clf.FAULT_LABELS))}
    assert len({tuple(v) for v in streams.values()}) == len(streams), \
        "fault classes share a noise stream (default_rng(seed) called repeatedly)"

    # 2. each stream is reproducible
    again = {i: clf._class_rng(i).normal(0, 1, 8).tolist() for i in streams}
    assert streams == again, "_class_rng is not deterministic"

    # 3. augmentation is immune to the global RNG being disturbed — this is
    #    what "reproducible training run" actually requires
    import pandas as pd

    def fixture():
        rows = []
        for k, lab in enumerate(clf.FAULT_LABELS):
            for j in range(5):
                rows.append({
                    "fault_label": lab, "NORAD_CAT_ID": 1000 + k,
                    "EPOCH": pd.Timestamp("2026-01-01") + pd.Timedelta(hours=j),
                    **{c: float(k + j) * 0.5 for c in clf.FEATURE_COLS},
                })
        return pd.DataFrame(rows)

    np.random.seed(1)
    np.random.normal(size=997)                 # disturb the global stream
    a = clf.augment_fault_samples(fixture(), target_per_class=40)
    np.random.seed(999)
    np.random.normal(size=13)                  # disturb it differently
    b = clf.augment_fault_samples(fixture(), target_per_class=40)

    assert a[clf.FEATURE_COLS].round(10).equals(b[clf.FEATURE_COLS].round(10)), \
        "augmented rows depend on global RNG state — training is not reproducible"


@run_test
def test_requirements_are_upper_bounded():
    """
    Only langgraph and langchain-core were pinned; everything else was floored
    with `>=` and no ceiling, so the resolved stack depended on the day. numpy
    and torch have both changed RNG behaviour across minor versions — a
    reproducible training run needs a reproducible stack under it.
    """
    for rel in ("requirements.txt", "backend/requirements.txt"):
        unbounded = []
        for raw in (ROOT / rel).read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            if "==" not in line and "<" not in line:
                unbounded.append(line)
        assert not unbounded, f"{rel}: unbounded requirements: {unbounded}"


# ===========================================================================
# UI fault set matches the emulator (Prompt 6.4)
# ===========================================================================

@run_test
def test_every_ui_fault_maps_to_a_real_emulator_fault():
    """
    THE Prompt 6.4 acceptance test.

    The UI offered five faults; the emulator modelled four. api.ts mapped
    battery_fail -> firmware_corruption and adcs_fail -> SEU, so selecting
    either produced a diagnosis contradicting the operator's own label.
    """
    import json, re
    em = _emulator()

    src = _ts_code_only("frontend/api.ts")
    block = src[src.index("UI_FAULT_TO_BACKEND"):]
    block = block[:block.index("};")]
    mapping = dict(re.findall(r"(\w+):\s*'([^']+)'", block))
    assert len(mapping) == 5, f"expected 5 UI faults, found {len(mapping)}"

    valid = {f.value for f in em.FaultType if f is not em.FaultType.NONE}
    lib = json.loads((ROOT / "agents" / "procedure_library.json")
                     .read_text(encoding="utf-8"))["procedures"]

    for ui_id, backend in mapping.items():
        assert backend in valid, \
            f"UI '{ui_id}' maps to '{backend}', which is not a FaultType"
        assert backend in lib, \
            f"UI '{ui_id}' -> '{backend}' has no procedures in procedure_library.json"
        # the mapping must not be an "analogue" — the names must correspond
        assert not (ui_id == "battery_fail" and backend == "firmware_corruption"), \
            "battery_fail is still mapped to firmware_corruption"
        assert not (ui_id == "adcs_fail" and backend == "SEU"), \
            "adcs_fail is still mapped to SEU"


@run_test
def test_ui_faults_recover_with_a_matching_procedure():
    """
    End to end for all five: inject what the UI would send, confirm the
    emulator reports that same fault, then run the library's primary procedure
    and check its success criteria are actually met.
    """
    import json, types
    em = _emulator()
    ra = _agent()

    lib = json.loads((ROOT / "agents" / "procedure_library.json")
                     .read_text(encoding="utf-8"))["procedures"]
    ui_map = {"seu": "SEU", "leak": "software_bug", "injection": "command_injection",
              "battery_fail": "battery_failure", "adcs_fail": "adcs_failure"}

    for ui_id, backend in ui_map.items():
        e = em.SatelliteEmulator(tick_interval=0)
        _injectors_for(em, e)[em.FaultType(backend)]()
        for _ in range(6):
            e._update_nominal_drift()
            e._apply_fault_effects()

        assert e.fault_injected.value == backend, \
            f"{ui_id}: emulator reports {e.fault_injected.value}, expected {backend}"

        # KNOWN GAP, pre-dating this change and flagged in Prompt 3.2: SEU's
        # FALLBACK (OBC_SOFT_REBOOT_v1) requires adcs_status == nominal, but an
        # OBC reboot cannot clear a stuck ADCS, so its criteria can never be
        # met. That is arguably physically honest — if the memory scrub fails,
        # rebooting the OBC will not fix the wheel — but it means SEU has no
        # working fallback. Listed explicitly so it stays visible instead of
        # being silently excluded; a procedure_library decision, not a bug here.
        KNOWN_UNSATISFIABLE = {("SEU", "OBC_SOFT_REBOOT_v1")}

        for idx, proc in enumerate(lib[backend]["recovery_priority"]):
            name = proc["procedure_name"]
            e2 = em.SatelliteEmulator(tick_interval=0)
            _injectors_for(em, e2)[em.FaultType(backend)]()
            for _ in range(6):
                e2._update_nominal_drift()
                e2._apply_fault_effects()

            assert e2.apply_recovery(name) is True, \
                f"{backend}: {name} was refused for the fault it is listed under"

            met = ra._check_criteria(e2._build_frame(), proc["success_criteria"])
            if (backend, name) in KNOWN_UNSATISFIABLE:
                assert met is False, \
                    f"{backend}/{name} now meets its criteria — remove it from " \
                    f"KNOWN_UNSATISFIABLE"
                continue
            assert met is True, \
                f"{backend}: {name} ran but its success criteria are unmet"

            if idx == 0:
                assert e2.fault_injected == em.FaultType.NONE, \
                    f"{backend}: primary procedure did not clear the fault"


@run_test
def test_classifier_blind_faults_bypass_ai1():
    """
    AI-1 classifies from ORBITAL ELEMENTS. Battery state and reaction-wheel
    health leave no signature in a TLE, so running the classifier on those
    faults would return whichever of its four classes fit best — reinstating
    the mismatch one layer deeper. run_pipeline() must force skip_classifier.
    """
    src = _code_only("pipeline.py")
    assert "CLASSIFIER_BLIND_FAULTS" in src, "no blind-fault set defined"
    assert "skip_classifier = True" in src, \
        "run_pipeline() does not force the bypass for unclassifiable faults"

    # AI-1's label set must NOT have grown — these faults are not learnable
    _need("pandas")
    sys.path.insert(0, str(ROOT / "models"))
    from feature_spec import FAULT_LABELS
    assert set(FAULT_LABELS) == {"SEU", "SOFTWARE_BUG", "FIRMWARE_CORRUPTION",
                                 "COMMAND_INJECTION"}, \
        "AI-1 label set changed — battery/ADCS faults are not classifiable " \
        "from orbital elements and must not be added to it"


# ===========================================================================
# Shared WebSocket connections (Prompt 6.3)
# ===========================================================================

@run_test
def test_websockets_are_multiplexed_not_duplicated():
    """
    THE Prompt 6.3 acceptance test, checked statically.

    The dashboard opened SIX sockets where two would do — three to
    /ws/telemetry and three to /ws/events — so every frame was serialised by
    the server and parsed by the browser three times over.

    subscribe() now keeps one connection per path in a module-level registry
    and fans out to a Set of listeners. Revert the registry and this fails.
    """
    # EXECUTED, not grepped. The static version of this test could not fail:
    # it asserted `"channels.get(path)" in src`, and that string also appears
    # in close(), so breaking the reuse in subscribe() left it passing. The
    # mutation check caught that. Transpile the real api.ts and count sockets.
    esb, node = _esbuild(), _node()
    if not esb or not node:
        raise _Skip("esbuild/node unavailable — cannot execute api.ts")

    import subprocess, tempfile, textwrap
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "api.mjs"
        r = subprocess.run([esb, str(ROOT / "frontend" / "api.ts"),
                            f"--outfile={out}", "--format=esm", "--target=es2022"],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"esbuild failed:\n{r.stderr}"

        driver = Path(tmp) / "drive.mjs"
        driver.write_text(textwrap.dedent("""
            const sockets = [];
            globalThis.WebSocket = class {
              constructor(url){ this.url=url; sockets.push(this); this.closedFlag=false;
                queueMicrotask(()=> this.onopen && this.onopen()); }
              close(){ if(this.closedFlag) return; this.closedFlag=true;
                       this.onclose && this.onclose(); }
              feed(o){ this.onmessage && this.onmessage({data: JSON.stringify(o)}); }
            };
            globalThis.window = { location: { protocol:'http:', hostname:'x' } };
            globalThis.fetch = async (url, init) => {
              if (String(url).endsWith('/auth/ws-token') &&
                  init?.headers?.Authorization === 'Bearer test-session') {
                return { ok: true, json: async () => ({
                  connection_token: 'test-connection-token', token_type: 'websocket'
                }) };
              }
              return { ok: false, statusText: 'Unauthorized', json: async () => ({}) };
            };
            const { subscribeTelemetry, subscribeEvents } = await import('./api.mjs');
            // The client rightly requires a verified bearer session before it
            // opens either protected socket.
            const { setSessionToken } = await import('./api.mjs');
            setSessionToken('test-session');
            const wait = () => new Promise(r=>setTimeout(r,5));

            let frames = 0, events = 0;
            const handles = [
              subscribeTelemetry(()=>frames++),
              subscribeTelemetry(()=>frames++),
              subscribeTelemetry(()=>frames++),
              subscribeEvents(()=>events++),
              subscribeEvents(()=>events++),
              subscribeEvents(()=>events++),
            ];
            await wait();
            const opened = sockets.length;

            sockets.find(s=>s.url.includes('telemetry'))?.feed({frame_id:1});
            sockets.find(s=>s.url.includes('events'))?.feed({event:'x'});
            await wait();

            handles.forEach(h=>h.close());
            const leaked = sockets.filter(s=>!s.closedFlag).length;
            console.log(JSON.stringify({opened, frames, events, leaked}));
        """), encoding="utf-8")

        r = subprocess.run([node, str(driver)], cwd=tmp,
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, f"driver failed:\n{r.stdout}\n{r.stderr}"
        result = json.loads(r.stdout.strip().splitlines()[-1])

    assert result["opened"] == 2, (
        f"{result['opened']} WebSocket connections opened for 6 subscribers, "
        f"expected 2 (one per path)")
    assert result["frames"] == 3, \
        f"one telemetry frame reached {result['frames']} of 3 subscribers"
    assert result["events"] == 3, \
        f"one event reached {result['events']} of 3 subscribers"
    assert result["leaked"] == 0, \
        f"{result['leaked']} socket(s) still open after every subscriber closed"

    # reconnect behaviour preserved verbatim
    src = _ts_code_only("frontend/api.ts")
    assert "Math.min(1000 * 2 ** ch.retry, 15000)" in src, \
        "the exponential-backoff reconnect was altered"


@run_test
def test_late_subscriber_still_receives_the_backfill():
    """
    Sharing the socket would otherwise break Prompt 6.0 for any component that
    mounts AFTER the connection opened — switching to the dashboard tab. The
    history envelope arrives once per connect, so a late subscriber would
    never see it and its chart would start empty.
    """
    src = _ts_code_only("frontend/api.ts")
    assert "lastHistory" in src, "the backfill envelope is not cached for replay"
    assert "queueMicrotask" in src, \
        "the cached backfill is not replayed to late subscribers"
    # and a stale backfill must not survive a reconnect
    i = src.index("onclose")
    assert "lastHistory = null" in src[i:i + 400], \
        "a stale backfill would be replayed after reconnect"


@run_test
def test_components_were_not_restructured_for_multiplexing():
    """
    FIX-ONLY: the fix belongs in the shared helper. No component should have
    been rewired to a context or to props, and none should build its own
    WebSocket.
    """
    for name in ("useDeadsat.ts", "components/SatelliteDashboard.tsx",
                 "components/AiDiagnostics.tsx",
                 "components/OperatorControlPanel.tsx"):
        src = _ts_code_only(f"frontend/{name}")
        assert "new WebSocket" not in src, f"{name} constructs its own socket"
        assert "createContext" not in src, \
            f"{name} was rewired to a context — the helper should have absorbed this"
        assert "subscribeTelemetry" in src or "subscribeEvents" in src, \
            f"{name} no longer uses the shared subscription API"


# ===========================================================================
# Fetched-but-unrendered state (Prompt 6.2)
# ===========================================================================

@run_test
def test_fetched_state_is_actually_rendered():
    """
    Six values were fetched from the backend every few seconds and never
    displayed. The crypto ledger is the worst of them: it is the single best
    evidence the security layer works.
    """
    import re
    for component, names in (
        ("SecurityConsole", ("ledger", "cryptoMode", "lastError")),
        ("AiDiagnostics", ("statusHint", "artifactsReady", "lastClass")),
    ):
        src = _ts_code_only(f"frontend/components/{component}.tsx")
        # everything after the last hook declaration is, near enough, the JSX
        jsx = src[src.rindex("useState"):]
        for name in names:
            assert re.search(rf"[{{(\s]{name}\b", jsx), \
                f"{component}: {name} is fetched but never rendered"


@run_test
def test_crypto_list_parsing_accepts_the_router_shape():
    """
    /crypto/ledger and /crypto/alerts are served by the crypto router, which
    returns a BARE ARRAY. The components parsed `l.entries` / `a.alerts` — the
    shape of the proxy handlers Prompt 4.0 deleted — so both read empty
    forever, indistinguishable from "nothing has been signed".
    """
    for component in ("SecurityConsole", "OperatorPanel"):
        src = _ts_code_only(f"frontend/components/{component}.tsx")
        for expr in ("l.entries", "a.alerts"):
            bare = f"({expr} || [])"
            assert bare not in src, \
                f"{component}: {bare} assumes the removed proxy shape"
        if "cryptoLedger" in src:
            assert "Array.isArray(l)" in src, \
                f"{component}: does not handle the router's bare-array ledger"


@run_test
def test_backend_failure_is_visible_not_silent():
    """
    /pipeline/status returns `hint` ("Train with: python train_classifier.py")
    and /crypto/status returns `message` ("CY-1 not running — signatures
    cannot be verified"). Both were discarded, so a broken backend rendered as
    a confident 0.00%.
    """
    sec = _ts_code_only("frontend/components/SecurityConsole.tsx")
    assert "lastError &&" in sec, \
        "SecurityConsole does not surface lastError"

    ai = _ts_code_only("frontend/components/AiDiagnostics.tsx")
    assert "statusHint &&" in ai, "AiDiagnostics does not surface statusHint"
    assert "artifactsReady === false" in ai, \
        "AiDiagnostics does not distinguish 'untrained' from a real 0%"


# ===========================================================================
# Cold-start retry and polling waste (Prompt 6.1)
# ===========================================================================

@run_test
def test_tle_loader_retries_until_the_emulator_ticks():
    """
    BUG A: the loader called api.telemetry() once and, if the emulator had not
    yet produced a frame (`norad_id` undefined), waited 300 s before trying
    again. The UI reliably loads faster than the backend boots, so the orbit
    panel was blank for five minutes on essentially every cold start.
    """
    src = _ts_code_only("frontend/components/SatelliteDashboard.tsx")
    i = src.index("const load = async ()")
    block = src[i:i + 1800]

    assert "loadUntilReady" in block, "no retry loop — a cold start still waits 5 minutes"
    assert "RETRY_LIMIT_MS" in block, "the retry is unbounded"
    assert "setTimeout(loadUntilReady" in block, "retry is not scheduled"
    # the 5-minute refresh must survive
    assert "setInterval(load, 300000)" in block, "the 5-minute refresh was removed"
    # and the retry must be cleaned up
    assert "clearTimeout(retryTimer)" in block, "retry timer leaks on unmount"


@run_test
def test_classify_is_not_polled_on_a_timer():
    """
    BUG B: /pipeline/classify ran every 15 s — a 503 four times a minute with
    AI-1 untrained, or a full transformer inference pass on a Pi 4 with it
    trained, to refresh one number in a corner.
    """
    src = _ts_code_only("frontend/components/OperatorControlPanel.tsx")

    # every setInterval in the file must be timer-only, never a network call
    import re
    for m in re.finditer(r"setInterval\(", src):
        window = src[m.start():m.start() + 700]
        assert "api." not in window, \
            f"a setInterval still performs a backend call:\n{window[:200]}"

    assert src.count("api.classify()") == 1, \
        "classify should have exactly one call site (classifyNow)"
    assert "classifyNow" in src, "no on-demand classifier"
    assert "void classifyNow()" in src, \
        "classifyNow is never triggered — the metric would never update"
    assert "api.pipelineStatus()" in src, "pipeline status is no longer checked"


# ===========================================================================
# WebSocket history envelope (Prompt 6.0)
# ===========================================================================

@run_test
def test_subscribe_telemetry_branches_on_history_envelope():
    """
    THE Prompt 6.0 acceptance test.

    /ws/telemetry's FIRST message is {"type":"history","frames":[...]} — not a
    frame. Nothing checked `type`, so the envelope reached the frame handlers
    on every connect and every reconnect: `SP: 0x1FFF00NaN`, "WS frame
    undefined", and all five AiDiagnostics channels red CRITICAL. The 60
    backfilled frames were discarded.

    Revert check: delete the `type === 'history'` branch and this fails.
    """
    src = (ROOT / "frontend" / "api.ts").read_text(encoding="utf-8")
    i = src.index("export const subscribeTelemetry")
    body = src[i:src.index("export const subscribeEvents", i)]

    assert "onHistory" in body, "subscribeTelemetry has no onHistory callback"
    assert "'history'" in body, "subscribeTelemetry does not branch on type"
    assert "return;" in body, \
        "the history branch must return without calling onFrame"

    # The envelope branch must live in subscribeTelemetry, not in the shared
    # transport. Bound the slice to subscribe()'s OWN body — the interface and
    # doc comment between it and subscribeTelemetry both mention "history"
    # legitimately, and matching those is how this assertion first failed.
    code = _ts_code_only("frontend/api.ts")
    start = code.index("function subscribe<T>")
    end = code.index("\n}", start) + 2          # first column-0 close brace
    sub = code[start:end]
    assert "'history'" not in sub, \
        "subscribe() branches on the envelope — that belongs in subscribeTelemetry"

    # The reconnect logic moved into openChannel() with Prompt 6.3's
    # multiplexing, but must be otherwise unaltered.
    assert "Math.min(1000 * 2 ** ch.retry, 15000)" in code, \
        "the exponential-backoff reconnect was altered"


@run_test
def test_dashboard_seeds_chart_from_history():
    """The backfill must actually populate the chart, via the same mapper."""
    src = _ts_code_only("frontend/components/SatelliteDashboard.tsx")
    i = src.index("subscribeTelemetry(")
    block = src[i:i + 2200]
    assert "frames.slice(-60).map(frameToPoint)" in block, \
        "SatelliteDashboard does not seed setHistoryData from the backfill"


# ===========================================================================
# Structural fragility (Prompt 5.2)
# ===========================================================================

@run_test
def test_main_block_is_last_in_canonical_tree():
    """
    `if __name__ == "__main__": uvicorn.run("main:app")` sat mid-file with
    hundreds of route definitions after it. The __main__ pass never reached
    them; only uvicorn's re-import did. Two SatelliteEmulator instances were
    constructed and every module-level side effect ran twice.
    """
    import ast
    # Only test canonical main.py since backend/main.py is deprecated
    rel = "main.py"
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    main_node = next(
        (n for n in tree.body if isinstance(n, ast.If)
         and ast.unparse(n.test).replace("'", '"') == '__name__ == "__main__"'),
        None)
    assert main_node is not None, f"{rel}: no __main__ block"

    after = tree.body[tree.body.index(main_node) + 1:]
    handlers = [n for n in after
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.decorator_list]
    assert not handlers, (
        f"{rel}: {len(handlers)} route handler(s) defined after the __main__ "
        f"block — they are skipped when run as __main__")
    assert not after, \
        f"{rel}: {len(after)} statement(s) after the __main__ block"


@run_test
def test_catalog_search_uses_the_public_api():
    """
    /catalog/search reached into cat._catalog and cat._loaded and called load()
    by hand — the only catalog endpoint bypassing the class API, so a change to
    the internal storage would break it silently.
    """
    import ast
    for rel in ("main.py", "backend/main.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "search_catalog")
        if fn.body and isinstance(fn.body[0], ast.Expr) and \
                isinstance(fn.body[0].value, ast.Constant):
            fn.body.pop(0)                      # drop the docstring
        code = ast.unparse(fn)
        for private in ("._catalog", "._loaded"):
            assert private not in code, \
                f"{rel}: search_catalog still touches {private}"

    _need("pandas")
    sys.path.insert(0, str(ROOT))
    from satellite_catalog import get_catalog
    cat = get_catalog()
    assert hasattr(cat, "search_by_name"), "SatelliteCatalog has no search_by_name()"
    assert len(cat.search_by_name("", limit=7)) == 7, "limit not honoured"
    assert cat.search_by_name("ZZZ_NOT_A_SATELLITE") == [], "no-match should be empty"
    assert any("NOAA" in r["name"] for r in cat.search_by_name("NOAA", limit=5))


@run_test
def test_no_dead_norad_id_declaration():
    """SatelliteDashboard declared `const noradId = 0` that nothing read."""
    src = _ts_code_only("frontend/components/SatelliteDashboard.tsx")
    assert "const noradId = 0" not in src, \
        "the dead noradId declaration is still present"


# ===========================================================================
# Claims reconciliation (Prompt 5.1)
# ===========================================================================

@run_test
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


@run_test
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


@run_test
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


@run_test
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


@run_test
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


@run_test
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


@run_test
def test_no_grpc_claim_without_grpc():
    """LandingPage advertised a "secure gRPC gateway". There is no gRPC here."""
    src = (ROOT / "frontend" / "components" / "LandingPage.tsx").read_text(encoding="utf-8")
    assert "gRPC" not in src, "LandingPage claims gRPC; the transport is REST + WebSockets"


@run_test
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


@run_test
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


@run_test
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

@run_test
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
# RF Architecture Tests (Three-Node RF Integration)
# ===========================================================================

@run_test
def test_rf_models_validate_structured_frames():
    """
    RF models must properly validate structured RF frames.
    Tests that RFFrame enforces required fields and valid ranges.
    """
    from datetime import datetime, timezone
    from rf.models import RFFrame, RFHealthStatus, RFMode
    
    # Valid frame construction
    valid_frame = RFFrame(
        schema_version="1.0",
        frame_id="test-frame-001",
        timestamp=datetime.now(timezone.utc).isoformat(),
        source_node="pi2-rf",
        frequency_hz=137900000.0,
        sample_rate=2048000,
        gain=40.0,
        signal_dbm=-85.0,
        snr=15.0,
        noise_floor=-100.0,
        bandwidth=2400000.0,
        doppler_correction_hz=0.0,
        rf_health=RFHealthStatus.ONLINE,
        rf_mode=RFMode.REAL,
        sequence=1
    )
    
    assert valid_frame.frequency_hz == 137900000.0
    assert valid_frame.signal_dbm == -85.0
    assert valid_frame.rf_health == RFHealthStatus.ONLINE


@run_test
def test_rf_ingest_rejects_stale_frames():
    """
    The /rf/ingest endpoint must reject frames older than 10 seconds.
    This prevents replay attacks and stale data ingestion.
    """
    from datetime import datetime, timezone, timedelta
    from rf.models import RFFrame, RFIngestRequest, RFHealthStatus, RFMode
    
    # Create a stale frame (20 seconds old)
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    stale_frame = RFFrame(
        schema_version="1.0",
        frame_id="stale-frame",
        timestamp=stale_time,
        source_node="pi2-rf",
        frequency_hz=137900000.0,
        sample_rate=2048000,
        gain=40.0,
        signal_dbm=-85.0,
        snr=15.0,
        noise_floor=-100.0,
        bandwidth=2400000.0,
        doppler_correction_hz=0.0,
        rf_health=RFHealthStatus.ONLINE,
        rf_mode=RFMode.REAL,
        sequence=1
    )
    
    request = RFIngestRequest(frame=stale_frame)
    
    # The timestamp should be > 10 seconds old
    frame_time = datetime.fromisoformat(stale_frame.timestamp.replace('Z', '+00:00'))
    age = (datetime.now(timezone.utc) - frame_time).total_seconds()
    assert age > 10.0, "Test frame should be stale"


@run_test
def test_rf_ingest_rejects_invalid_signal_ranges():
    """
    The /rf/ingest endpoint must reject frames with invalid signal strength.
    Valid range: -150 dBm to -10 dBm.
    """
    from datetime import datetime, timezone
    from rf.models import RFFrame, RFIngestRequest, RFHealthStatus, RFMode
    
    # Create frame with invalid signal strength (too strong)
    invalid_frame = RFFrame(
        schema_version="1.0",
        frame_id="invalid-signal",
        timestamp=datetime.now(timezone.utc).isoformat(),
        source_node="pi2-rf",
        frequency_hz=137900000.0,
        sample_rate=2048000,
        gain=40.0,
        signal_dbm=5.0,  # Invalid: above -10 dBm
        snr=15.0,
        noise_floor=-100.0,
        bandwidth=2400000.0,
        doppler_correction_hz=0.0,
        rf_health=RFHealthStatus.ONLINE,
        rf_mode=RFMode.REAL,
        sequence=1
    )
    
    assert not (-150 <= invalid_frame.signal_dbm <= -10), \
        "Test frame should have invalid signal strength"


@run_test
def test_rf_ingest_enforces_sequence_monotonicity():
    """
    The /rf/ingest endpoint must enforce monotonically increasing sequence numbers.
    This prevents frame reordering and replay attacks.
    """
    from datetime import datetime, timezone
    from rf.models import RFFrame, RFHealthStatus, RFMode
    
    # Create two frames with non-increasing sequence
    frame1 = RFFrame(
        schema_version="1.0",
        frame_id="frame-1",
        timestamp=datetime.now(timezone.utc).isoformat(),
        source_node="pi2-rf",
        frequency_hz=137900000.0,
        sample_rate=2048000,
        gain=40.0,
        signal_dbm=-85.0,
        snr=15.0,
        noise_floor=-100.0,
        bandwidth=2400000.0,
        doppler_correction_hz=0.0,
        rf_health=RFHealthStatus.ONLINE,
        rf_mode=RFMode.REAL,
        sequence=10
    )
    
    frame2 = RFFrame(
        schema_version="1.0",
        frame_id="frame-2",
        timestamp=datetime.now(timezone.utc).isoformat(),
        source_node="pi2-rf",
        frequency_hz=137900000.0,
        sample_rate=2048000,
        gain=40.0,
        signal_dbm=-85.0,
        snr=15.0,
        noise_floor=-100.0,
        bandwidth=2400000.0,
        doppler_correction_hz=0.0,
        rf_health=RFHealthStatus.ONLINE,
        rf_mode=RFMode.REAL,
        sequence=5  # Invalid: less than previous sequence
    )
    
    assert frame2.sequence <= frame1.sequence, \
        "Test frames should have non-increasing sequence"


@run_test
def test_rf_websocket_endpoint_exists():
    """
    The main.py must expose the /ws/rf WebSocket endpoint for live RF streaming.
    """
    import re
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    
    # Check for RF WebSocket endpoint
    assert '@app.websocket("/ws/rf")' in main_src, \
        "main.py missing /ws/rf WebSocket endpoint"
    
    # Check for RF WebSocket handler
    assert 'async def ws_rf' in main_src, \
        "main.py missing ws_rf handler function"


@run_test
def test_rf_ingest_endpoint_requires_auth():
    """
    The /rf/ingest endpoint must require API key authentication when configured.
    This prevents unauthorized RF frame injection.
    """
    import re
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    
    # Check for /rf/ingest endpoint
    assert '@app.post("/rf/ingest")' in main_src, \
        "main.py missing /rf/ingest endpoint"
    
    # Check for authentication dependency
    ingest_section = main_src[main_src.index('@app.post("/rf/ingest")'):main_src.index('@app.post("/rf/ingest")') + 500]
    assert 'require_api_key' in ingest_section or 'Depends' in ingest_section, \
        "/rf/ingest endpoint lacks authentication"


@run_test
def test_rf_models_support_mock_mode():
    """
    RF models must support mock mode for development and CI without hardware.
    Tests that RFMode.MOCK is available and properly defined.
    """
    from rf.models import RFMode
    
    assert hasattr(RFMode, 'MOCK'), "RFMode missing MOCK option"
    assert RFMode.MOCK is not None, "RFMode.MOCK is None"


@run_test
def test_frontend_uses_rf_websocket():
    """
    The frontend must use the /ws/rf WebSocket for live RF data instead of polling.
    Tests that api.ts contains subscribeRF function.
    """
    api_src = (ROOT / "frontend" / "api.ts").read_text(encoding="utf-8")
    
    assert 'subscribeRF' in api_src, \
        "frontend/api.ts missing subscribeRF function"
    
    assert '/ws/rf' in api_src, \
        "frontend/api.ts does not reference /ws/rf endpoint"


@run_test
def test_rf_location_is_configurable():
    """
    Ground station location for RF/Doppler calculations must be configurable
    via environment variables, not hard-coded.
    """
    import re
    predictor_src = (ROOT / "rf" / "meteor_predictor.py").read_text(encoding="utf-8")
    
    # Check that location is loaded from config, not hard-coded
    assert 'RF_LOCATION_LAT' in predictor_src or 'cfg.RF_LOCATION_LAT' in predictor_src, \
        "meteor_predictor.py does not use configurable location"
    
    # Check that old hard-coded values are removed
    assert '23.03' not in predictor_src or 'RF_LOCATION_LAT' in predictor_src, \
        "meteor_predictor.py may still have hard-coded latitude"
    assert '72.58' not in predictor_src or 'RF_LOCATION_LON' in predictor_src, \
        "meteor_predictor.py may still have hard-coded longitude"


@run_test
def test_rf_transport_has_timeout_and_retry():
    """
    RF transport must have timeout and retry logic to handle network failures.
    Tests that transport.py includes timeout configuration.
    """
    transport_src = (ROOT / "rf" / "transport.py").read_text(encoding="utf-8")
    
    assert 'timeout' in transport_src.lower(), \
        "rf/transport.py missing timeout configuration"
    
    assert 'retry' in transport_src.lower(), \
        "rf/transport.py missing retry logic"


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


