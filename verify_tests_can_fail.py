#!/usr/bin/env python3
"""
verify_tests_can_fail.py — mutation check for test_units.py
============================================================

    "each test fails if you revert its corresponding fix (verify this —
     a test that cannot fail is not a test)."

A test suite that passes is evidence of nothing until you have watched it
fail for the right reason. This script reverts each fix in the source, runs
ONLY the test that guards it, and asserts that the test goes red.

Each mutation is the smallest edit that undoes the fix — usually restoring the
exact line the audit found. Files are patched on disk, the single test is run
in a subprocess, and the original bytes are restored in a `finally` so an
interrupt cannot leave the tree modified.

    python verify_tests_can_fail.py            # run every mutation
    python verify_tests_can_fail.py -v         # show the failure message
    python verify_tests_can_fail.py nonce      # only mutations matching "nonce"

Exit code 0 means every mutation was caught.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERBOSE = "-v" in sys.argv
FILTER = next((a for a in sys.argv[1:] if not a.startswith("-")), "")


# ---------------------------------------------------------------------------
# Mutations: (name, guarding test, file, find, replace)
#
# `find` must appear EXACTLY ONCE in the file — a mutation that silently
# matches nothing, or matches twice, would make this whole script lie.
# ---------------------------------------------------------------------------

MUTATIONS: list[tuple[str, str, str, str, str]] = [

    # ---- Prompt 3.2 — success criteria -----------------------------------
    (
        "criteria: missing key passes instead of failing closed",
        "test_check_criteria_operators_and_missing_keys",
        "agents/recovery_agent.py",
        "        if key not in frame:",
        "        if False:",
    ),
    (
        "criteria: '<=' shadowed by '<' (the original operator bug)",
        "test_check_criteria_operators_and_missing_keys",
        "agents/recovery_agent.py",
        'for op in ("<=", ">=", "==", "!=", "<", ">"):',
        'for op in ("<", ">", "<=", ">=", "==", "!="):',
    ),
    (
        "criteria: bool criterion reaches .startswith() again",
        "test_bool_criteria_evaluate_correctly",
        "agents/recovery_agent.py",
        "if isinstance(condition, bool) or isinstance(val, bool):",
        "if False:",
    ),
    (
        "monitor: 'or health == nominal' shortcut restored",
        "test_monitor_does_not_accept_nominal_health_as_success",
        "agents/recovery_agent.py",
        "        if passed:",
        '        if passed or health == "nominal":',
    ),
    (
        "select: min_confidence skip leaves a stale procedure",
        "test_min_confidence_skip_does_not_uplink_a_stale_procedure",
        "agents/recovery_agent.py",
        '            state["next_step"]       = "reselect"',
        '            state["next_step"]       = None',
    ),

    # ---- Prompt 3.1 — fault-aware recovery -------------------------------
    (
        "apply_recovery: applicability gate removed",
        "test_wrong_procedure_is_refused_and_leaves_state_untouched",
        "emulator/satellite_emulator.py",
        "            if active not in applicable:",
        "            if False:",
    ),

    # ---- Prompt 3.3 — bounded telemetry ----------------------------------
    (
        "emulator: power_w clamp removed (unbounded random walk)",
        "test_healthy_drift_stays_inside_nominal_bands",
        "emulator/satellite_emulator.py",
        "        self.power.solar_output_w = _clamp(\n"
        "            self.power.solar_output_w + random.uniform(-1.0, 1.0), \"power_w\", faulted)",
        "        self.power.solar_output_w = (\n"
        "            self.power.solar_output_w + random.uniform(-1.0, 1.0))",
    ),
    (
        "emulator: start() not idempotent",
        "test_start_is_idempotent",
        "emulator/satellite_emulator.py",
        "        if self._running and self._thread and self._thread.is_alive():",
        "        if False:",
    ),
    (
        "emulator: drift frozen during faults again",
        "test_telemetry_has_noise_during_faults",
        "emulator/satellite_emulator.py",
        "        faulted = self.fault_injected not in (None, FaultType.NONE)",
        "        faulted = self.fault_injected not in (None, FaultType.NONE)\n"
        "        if faulted: return",
    ),

    # ---- Prompt 4.1 — nonce replay protection ----------------------------
    (
        "nonce: atomic SET NX -> get-then-set race (mock/in-memory path)",
        # Substring match (see test_units.py's --only): this mutation is in
        # the mock branch's get-then-set logic, which the *_exactly_one_
        # succeeds test only exercises when NonceManager falls back to it
        # (e.g. no redis reachable). test_concurrent_identical_nonces_
        # mock_path_exactly_one_succeeds forces that branch explicitly so
        # the mutation is caught even in environments (like CI, and this
        # project's own documented setup) where redis is always running.
        "test_concurrent_identical_nonces",
        "crypto/nonce.py",
        "                expiry = self.mock_store.get(key)\n"
        "                if expiry is not None and expiry > now:",
        "                expiry = self.mock_store.get(key)\n"
        "                if False:",
    ),
    (
        "verify.py: sys.exit() restored in a library function",
        "test_verify_module_does_not_kill_the_process",
        "crypto/verify.py",
        "        raise RuntimeError(",
        "        sys.exit(1)\n        raise RuntimeError(",
    ),
    (
        "nonce consumed at sign time instead of verify",
        "test_nonce_is_consumed_at_verify_not_at_sign",
        "crypto/crypto_routes.py",
        "        elif not _nonce_manager.use_nonce(req.nonce):",
        "        elif not True:",
    ),

    # ---- Prompt 4.0 — no fabricated signatures ---------------------------
    (
        "sign: mock-crypto refusal removed",
        "test_sign_refuses_to_fabricate_when_crypto_is_mocked",
        "crypto/crypto_routes.py",
        "    if mock_oqs_nacl.is_mock_active() and not _mock_signing_allowed():",
        "    if False:",
    ),

    # ---- Prompt 0.5 — CORS guard -----------------------------------------
    (
        "config: LAN-bind + loopback-CORS warning disabled",
        "test_cors_lan_bind_with_loopback_origins_is_detected",
        "config.py",
        "    lan_facing = API_HOST not in (\"127.0.0.1\", \"localhost\", \"::1\")",
        "    lan_facing = False",
    ),

    # ---- Prompt 6.0 — history envelope -----------------------------------
    (
        "api.ts: history envelope reaches the frame handler again",
        "test_subscribe_telemetry_branches_on_history_envelope",
        "frontend/api.ts",
        "      if (envelope?.type === 'history') {",
        "      if (false) {",
    ),

    # ---- Prompt 6.3 — multiplexed sockets --------------------------------
    (
        "api.ts: channel reuse removed (back to 6 sockets)",
        "test_websockets_are_multiplexed_not_duplicated",
        "frontend/api.ts",
        "  let ch = channels.get(path);",
        "  let ch = undefined;",
    ),

    # ---- Prompt 6.4 — UI/emulator fault parity ---------------------------
    (
        "api.ts: battery_fail mapped back to firmware_corruption",
        "test_every_ui_fault_maps_to_a_real_emulator_fault",
        "frontend/api.ts",
        "  battery_fail: 'battery_failure',",
        "  battery_fail: 'firmware_corruption',",
    ),

    # ---- Prompt 8.2 — orbital mechanics ----------------------------------
    (
        "contact: TLE validation removed (garbage reaches sgp4)",
        "test_malformed_tle_raises_a_clear_error",
        "emulator/contact_calculator.py",
        "        if not line.startswith(expect):",
        "        if False:",
    ),
    (
        "contact: coarse-then-refine replaced by a flat scan",
        "test_contact_search_is_coarse_then_refine",
        "emulator/contact_calculator.py",
        "                aos_time = self._bisect_crossing(prev_t, t, rising=True)",
        "                aos_time = t",
    ),
    (
        "contact: summary propagates the current position twice again",
        "test_contact_summary_does_not_propagate_three_times",
        "emulator/contact_calculator.py",
        '"in_contact_now": self.is_in_contact_now(current),',
        '"in_contact_now": self.is_in_contact_now(),',
    ),

    # ---- Prompt 5.2 — structural -----------------------------------------
    (
        "main.py: /catalog/search reaches into private attrs again",
        "test_catalog_search_uses_the_public_api",
        "main.py",
        "    results = get_catalog().search_by_name(name, limit)",
        "    cat = get_catalog()\n"
        "    if not cat._loaded:\n"
        "        cat.load()\n"
        "    results = list(cat._catalog)[:limit]",
    ),
]


def run_single_test(test_name: str) -> tuple[bool, str]:
    """
    Run ONE test in a subprocess via test_units.py --only.

    Running the whole suite per mutation was far too slow — the 5000-tick
    bounds tests alone put an 18-mutation sweep past any sane timeout, and a
    timeout kill is what left a source file mutated the first time this ran.
    """
    proc = subprocess.run(
        [sys.executable, "test_units.py", "--only", test_name],
        cwd=ROOT, capture_output=True, text=True, timeout=180)
    out = proc.stdout + proc.stderr
    lines = [l for l in out.splitlines() if "[PASS]" in l or "[FAIL]" in l or "[SKIP]" in l]
    passed = any("[PASS]" in l for l in lines) and not any("[FAIL]" in l for l in lines)
    return passed, "\n".join(lines).strip()


def main() -> int:
    print("=" * 74)
    print("MUTATION CHECK — does each test actually fail when its fix is reverted?")
    print("=" * 74)

    selected = [m for m in MUTATIONS if FILTER.lower() in m[0].lower()]
    if not selected:
        print(f"no mutations match {FILTER!r}")
        return 1

    # SAFETY: snapshot every file any mutation touches BEFORE starting, and
    # restore all of them on every exit path — normal, exception, or signal.
    #
    # The first run of this script was killed by an outer timeout mid-mutation
    # and left emulator/satellite_emulator.py patched with `if False:`. A
    # try/finally around one file is not enough when the process can be killed
    # outright; the atexit + signal handlers below are.
    touched = sorted({m[2] for m in selected})
    snapshot: dict[str, bytes] = {p: (ROOT / p).read_bytes() for p in touched}

    def restore_all(*_args):
        for rel, data in snapshot.items():
            target = ROOT / rel
            if target.read_bytes() != data:
                target.write_bytes(data)
                print(f"  [restored] {rel}")

    import atexit
    import signal
    atexit.register(restore_all)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *a: (restore_all(), sys.exit(130)))
        except (ValueError, OSError):     # not on the main thread
            pass

    caught = missed = broken = 0

    for name, test_name, rel_path, find, replace in selected:
        path = ROOT / rel_path
        original = snapshot[rel_path]
        text = original.decode("utf-8")

        # normalise for CRLF files
        nl = "\r\n" if "\r\n" in text[:400] else "\n"
        needle = find.replace("\n", nl)
        sub = replace.replace("\n", nl)

        occurrences = text.count(needle)
        if occurrences != 1:
            print(f"  [BROKEN ] {name}")
            print(f"            anchor found {occurrences} times in {rel_path} "
                  f"— mutation cannot be trusted")
            broken += 1
            continue

        try:
            path.write_bytes(text.replace(needle, sub, 1).encode("utf-8"))
            passed, out = run_single_test(test_name)
        finally:
            path.write_bytes(original)          # always restore

        if passed:
            print(f"  [MISSED ] {name}")
            print(f"            {test_name} still PASSED with the fix reverted")
            missed += 1
        else:
            print(f"  [CAUGHT ] {name}")
            if VERBOSE and out:
                print(f"            {out.splitlines()[0][:110]}")
            caught += 1

    print("=" * 74)
    print(f"caught {caught} · missed {missed} · broken anchors {broken}")
    if missed or broken:
        print("\nA MISSED mutation means that test cannot fail — it is not testing "
              "what it claims.\nA BROKEN anchor means the mutation did not apply; "
              "fix the anchor and re-run.")
    else:
        print("\nEvery reverted fix was caught by its guarding test.")
    print("=" * 74)
    return 1 if (missed or broken) else 0


if __name__ == "__main__":
    raise SystemExit(main())
