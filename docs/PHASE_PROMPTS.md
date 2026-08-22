# DeadSat Resurrection — Phase-wise Prompt List (6.8 → 9.5)

Copy-paste prompts for an AI coding assistant. Each is self-contained: it states
the verified problem, the constraints, and the acceptance criteria.

## Global constraints — paste at the top of every session

```
PROJECT: D:\faraway\DEADSAT-RESURRECTION

HARD CONSTRAINTS (apply to every task):
1. DO NOT change the folder structure. No moving, renaming, merging or deleting
   existing files or directories. New files may only be added inside directories
   that already exist. No new folders.
2. DO NOT push to or modify the GitHub repo. Work locally only.
3. The `backend/` tree is a parallel copy that must stay in sync. Every change to
   a shared module must be mirrored:
     emulator/ <-> backend/emulator/
     agents/   <-> backend/agents/
     models/   <-> backend/pipeline/     (note the different directory name)
     crypto/   <-> backend/crypto/
     main.py   <-> backend/main.py
     config.py <-> backend/config.py
4. FRONTEND IS FIX-ONLY. Do not redesign, restructure, re-style or rewrite any
   component. Change the minimum number of lines needed to fix the specific
   defect described. Preserve all existing markup, class names and layout.
5. After every change run:  python test_integration.py   (must stay 109+ passed,
   0 failed) and  python -m py_compile  on every touched file.
6. Read docs/BUG_AUDIT.md and docs/CRASH_REPORT.md first for context.
```

---

# PHASE 0 — Establish a trustworthy baseline
*~1 hour. Nothing downstream is verifiable until this passes.*

### Prompt 0.1 — Type-check the frontend

```
The frontend has never been type-checked; npm install could not reach the
registry in the environment where it was written, so every TypeScript change is
structurally verified only.

TASK: Run the TypeScript compiler and fix every error it reports.

  cd frontend
  npm install
  npm run lint          # runs: tsc --noEmit

CONSTRAINT: FIX-ONLY. Fix type errors with the smallest possible change. Do not
refactor, do not restructure components, do not change JSX or styling. If a fix
would require a redesign, report it instead of doing it.

Files most likely to have errors (recently modified):
  frontend/api.ts
  frontend/useDeadsat.ts
  frontend/App.tsx
  frontend/components/SatelliteDashboard.tsx
  frontend/components/AiDiagnostics.tsx
  frontend/components/SecurityConsole.tsx
  frontend/components/OperatorPanel.tsx
  frontend/components/OperatorControlPanel.tsx

ACCEPTANCE: `npx tsc --noEmit` exits 0. Report every error found and how it was
fixed. Do not suppress errors with `any` or `@ts-ignore` unless you explain why.
```

### Prompt 0.2 — Confirm both servers actually boot

```
TASK: Verify both FastAPI trees start cleanly.

  pip install -r requirements.txt
  python -c "import main"                 # must not raise
  python -c "import sys; sys.path.insert(0,'backend'); import backend.main"

Then start each and hit the health routes:
  uvicorn main:app --port 8000
  curl localhost:8000/health
  curl localhost:8000/system/links
  curl localhost:8000/system/config

CONTEXT: backend/main.py previously could not import at all — it used cfg.,
httpx. and Depends() without importing them. That is fixed, but has never been
run. Confirm both trees boot and report any remaining ImportError/NameError.

ACCEPTANCE: both import without error; /system/links returns JSON listing
emulator, ai1_classifier, ai2_agent, crypto, rf_station, websocket_clients.
```

---

# PHASE 1 — Dataset foundation
*~1 day. This is the single highest-leverage change in the project.*

### Prompt 1.1 — Build the propagation-based dataset generator

```
VERIFIED PROBLEM: data/input.csv + input__1_.csv + input__2_.csv are a snapshot —
854 rows, 712 unique satellites, and NO satellite has two distinct epochs. The
251 repeated rows are 141 EXACT duplicates caused by the three CSVs overlapping.

Measured consequence:
  REV_DELTA == 0 for 100% of rows
  ecc_delta == 0 for 100% of rows
  Label distribution: SOFTWARE_BUG 807 | FIRMWARE_CORRUPTION 23 |
                      COMMAND_INJECTION 9 | SEU 0 | NORMAL 0

Three of the four fault rules in assign_fault_labels() are defined as CHANGES
BETWEEN CONSECUTIVE EPOCHS, so with one epoch per satellite they can never fire.
`rev_delta <= 0` then absorbs 95% of the dataset as SOFTWARE_BUG.

Real fault-labelled telemetry is restricted, so synthetic fault injection is the
correct approach — the problem is the SHAPE of what is generated, not that it is
synthetic.

TASK: Create generate_dataset.py in the repo ROOT (new file, existing directory).

  1. Load and DEDUPLICATE the three CSVs on (NORAD_CAT_ID, EPOCH).
  2. For each satellite, build a TLE via satellite_catalog.build_tle_from_gp()
     and propagate forward with sgp4 (already in requirements.txt) for N epochs
     (default 20) at ~90-minute steps. Re-derive the osculating orbital elements
     at each step so the series contains real orbital dynamics.
  3. Advance REV_AT_EPOCH realistically across epochs.
  4. Inject faults as perturbations to the SERIES, matching the label rules in
     models/satellite_fault_classifier_V2.py:assign_fault_labels() exactly,
     including their precedence order:
       SEU                 -> one-epoch step in ECCENTRICITY (> 0.01) and
                              MEAN_ANOMALY that REVERTS the next epoch
       SOFTWARE_BUG        -> freeze or roll back REV_AT_EPOCH for a run
       FIRMWARE_CORRUPTION -> ramp BSTAR (> 0.005) / MEAN_MOTION_DOT (> 0.001)
       COMMAND_INJECTION   -> withhold epochs so TLE_AGE_HOURS exceeds 72
     Keep each fault clear of the higher-priority rules so it is not shadowed.
  5. Write data/synthetic_orbital_series.csv with the same columns as input.csv
     plus a ground_truth_fault column.
  6. Add a --verify flag that prints the class distribution after running
     assign_fault_labels() on the output.

CONSTRAINTS: New file in repo root only. Do not modify the existing CSVs. Do not
change folder structure.

ACCEPTANCE:
  - every satellite has >= CONFIG["seq_len"] (8) distinct epochs
  - `python generate_dataset.py --verify` shows >= 300 rows of EACH of the four
    classes, all arising from injected series (not _generate_synthetic_class)
  - ecc_delta and REV_DELTA are non-zero for > 90% of rows
  - assign_fault_labels() agrees with ground_truth_fault for > 95% of rows
```

---

# PHASE 2 — ML pipeline correctness
*~4 hours. Without this, no accuracy number from this project can be quoted.*

### Prompt 2.1 — Eliminate the three data-leakage bugs

```
FILE: models/satellite_fault_classifier_V2.py  (mirror to backend/pipeline/)

THREE VERIFIED LEAKS. All are independent of the data source.

LEAK 1 — shuffle before windowing. build_dataloaders() does:
    X_tv, X_test, y_tv, y_test = train_test_split(X_scaled, y_raw, ...)  # shuffle=True by default
    train_ds = OrbitalSequenceDataset(X_train, y_train, seq)             # windows the shuffled array
Every 8-step "sequence" is 8 UNRELATED satellites stitched together. The
Transformer's positional encoding has nothing real to learn, and since the label
is y[i + seq_len - 1] the model collapses to single-row classification.
FIX: split by satellite using GroupShuffleSplit(groups=NORAD_CAT_ID), then build
windows WITHIN each satellite sorted by EPOCH. Windows must never straddle a
satellite boundary.

LEAK 2 — augmentation before the split. augment_fault_samples() oversamples with
replacement and adds noise of only 0.05 * class_std, producing near-duplicates,
BEFORE build_dataloaders() splits. FIRMWARE_CORRUPTION's 23 real rows become 400
that appear in train, val AND test.
FIX: split first, augment the training split only.

LEAK 3 — scaler fitted on everything. train_isolation_forest(df_clean) fits the
StandardScaler on ALL rows and returns it; build_dataloaders() then transforms
the split data with it.
FIX: fit the scaler on the training split only; transform val/test.

ALSO: fix the SEU contradiction. assign_fault_labels() defines SEU as a JUMP in
eccentricity between epochs; _generate_synthetic_class() defines it as a
CONSTANT ECCENTRICITY=0.05. After Phase 1 supplies real SEU sequences, remove
the call path to _generate_synthetic_class (keep the function, mark it
deprecated — do not delete files).

ALSO: train the Isolation Forest on NORMAL rows only. It currently fits on all
rows including faults, and its reported anomaly rate is circular (contamination
=0.05 forces ~5% by construction).

ACCEPTANCE:
  - assert in a test: no NORAD_CAT_ID appears in more than one split
  - assert in a test: no window spans two satellites
  - scaler.fit() is called exactly once, on training data only
  - TEST ACCURACY SHOULD DROP. If it does not, a leak remains — investigate
    before declaring done.
  - report before/after metrics honestly in the commit message
```

### Prompt 2.2 — Model card with a baseline comparison

```
TASK: Create docs/MODEL_CARD.md (new file, existing docs/ directory).

A transformer is only justified if it beats something simpler. Train a logistic
regression and a gradient-boosted tree (sklearn) on the SAME leak-free splits
from Prompt 2.1 and report all three side by side.

The model card must state:
  - Dataset provenance: real CelesTrak GP elements (712 satellites) with SGP4
    propagation and synthetic fault injection. Explain WHY synthetic: labelled
    satellite fault telemetry is not publicly available.
  - Split strategy (GroupShuffleSplit by NORAD_CAT_ID) and why.
  - Per-class precision / recall / F1 and a confusion matrix.
  - Baseline comparison table: logistic regression vs GBT vs transformer.
  - An explicit LIMITATIONS section naming the absence of real fault-labelled
    telemetry and what that means for generalisation claims.

ACCEPTANCE: every number in the card is reproducible by running
train_classifier.py. If the transformer does NOT beat the baselines, say so —
that is a legitimate and more credible finding than an unsupported claim.
```

---

# PHASE 3 — Recovery & emulator correctness
*~1 day.*

### Prompt 3.1 — Make apply_recovery() fault-aware

```
FILE: emulator/satellite_emulator.py  (mirror to backend/emulator/)

VERIFIED BUG: apply_recovery() ends with an unconditional
    self.fault_injected = FaultType.NONE
and prints "Recovery SUCCESS — satellite nominal" for ANY recognised procedure
name, regardless of whether it addresses the active fault.

Reproduced:
    inject_SEU()  -> health=fault, adcs=fault
    apply_recovery("LOCKDOWN_REGEN_v1")   # comms procedure, wrong for an SEU
    -> returns True, prints "Recovery SUCCESS", clears fault_injected,
       while adcs_status is STILL "fault"

TASK: Add a procedure -> applicable-faults map. Return False and leave state
untouched when the procedure cannot remedy the active fault. Only clear
fault_injected when the procedure genuinely applies.

ACCEPTANCE: a test injects SEU, applies LOCKDOWN_REGEN_v1, asserts the call
returns False and fault_injected is still SEU. The matching procedure still works.
```

### Prompt 3.2 — Make success_criteria authoritative

```
FILE: agents/recovery_agent.py  (mirror to backend/agents/)

BUG A — node_monitor_recovery contains:
    if passed or health == "nominal":
The `or` makes success_criteria advisory: because apply_recovery() resets
subsystem statuses, health flips to nominal immediately and recovery is declared
successful on the first poll. The fallback path is therefore almost unreachable,
which means the project's headline claim — "automatically falls back to an
alternate procedure" — has never actually been exercised.
FIX: drop `or health == "nominal"`.

BUG B — _check_criteria treats a missing key as PASS:
    val = frame.get(key)
    if val is None: continue
Live instance: SAFE_MODE_HOLD requires `beacon_active`, which the emulator never
emits, so that criterion is silently skipped.
FIX: fail closed on a missing key, AND add beacon_active to the emulator frame.

BUG C — '<=' and '>=' silently always fail. condition.startswith("<") then
float(condition[1:]) turns "<= 0.01" into float("= 0.01") -> ValueError -> falls
through to a string compare that returns False.
FIX: parse >=, <=, >, <, == properly.

BUG D — a JSON boolean in success_criteria raises an uncaught AttributeError
(condition.startswith on a bool is not caught by `except (ValueError, TypeError)`).
FIX: handle bool criteria explicitly.

BUG E — the min_confidence skip corrupts state. node_select_procedure increments
priority_index and returns WITHOUT setting selected_procedure, error or
next_step. route_after_select then routes to generate_commands, which reads a
STALE or MISSING selected_procedure and can uplink the wrong procedure.
Live trigger: software_bug has min_confidence 0.70 then 0.80, so any confidence
in [0.70, 0.80) hits this.
FIX: add a "reselect" route back into select_procedure.

ACCEPTANCE: a test forces the primary procedure to fail and asserts the FALLBACK
procedure actually runs and is recorded in recovery_log. A test at confidence
0.75 on software_bug asserts no stale procedure is uplinked.
```

### Prompt 3.3 — Clamp emulator state

```
FILE: emulator/satellite_emulator.py  (mirror to backend/emulator/)

VERIFIED FAILURES (measured, not theoretical):
  - power_w drifts to 46.6 W after ~2000 ticks (~33 min at the API's 1 s tick).
    The command_injection success criterion requires power_w > 75, so a demo
    left running fails on its own. solar_output_w, bus_voltage_v and
    reaction_wheel_rpm are unbounded random walks; battery_pct is correctly
    clamped — follow that pattern.
  - adcs_rate_deg_s reaches 14.68 deg/s after ~150 ticks (nominal < 0.01) and
    obc_error_count grows without limit, because _apply_fault_effects() adds to
    the same fields every tick with no ceiling.
  - start() is not idempotent: calling it twice leaves an orphaned ticking
    thread that is never joined (self._thread is overwritten).
  - _update_nominal_drift() returns early during a fault, so unaffected
    subsystems FREEZE completely — contradicting the documented "Improvement 2:
    fault state telemetry has noise on top of fault effects".

TASK: clamp all drifting fields, add per-fault ceilings, guard start() on
self._running, and either implement the documented noise-during-faults or remove
the claim.

ACCEPTANCE: a test runs 5000 ticks and asserts every telemetry field stays
within physical bounds; calling start() twice results in exactly one live thread.
```

---

# PHASE 4 — Security
*~1 day.*

### Prompt 4.1 — Fix nonce replay protection

```
FILE: crypto/nonce.py, crypto/crypto_routes.py  (mirror to backend/crypto/)

BUG A — TOCTOU race. The comment claims "SET NX — atomic, only sets if key
doesn't exist" but the code does a separate get() then set():
    existing = self.redis.get(key)
    if existing is not None: ...
    self.redis.set(key, nonce, ...)
Two concurrent requests with the same nonce both see None and both proceed. The
threading.Lock only serialises within one process — with multiple uvicorn workers
it provides no protection.
FIX: use redis.set(key, nonce, nx=True, ex=...) and treat a False return as a
replay.

BUG B — a failed comparison OVERWRITES the nonce. If
hmac.compare_digest(nonce_bytes, existing.encode()) returns False, control falls
through to the unconditional set(), replacing the stored nonce and permitting
the replay it was meant to block.

BUG C — the nonce is consumed at SIGNING time, not verification time.
crypto_routes.sign() calls use_nonce(); /verify never checks it. Replay
protection is on the wrong side of the trust boundary.
FIX: move the nonce check into /crypto/verify.

BUG D — sys.exit(1) inside crypto/verify.py on oqs.MechanismNotSupportedError.
A library function must not kill the API process. Raise instead.

BUG E — crypto/verify.py documents "Uses hmac.compare_digest() to prevent timing
attacks" and carries a comment saying so, but compare_digest is never called and
`import hmac` is unused. Either use it or remove the claim (PyNaCl's verify is
already constant-time, so the honest fix is to correct the documentation).

ACCEPTANCE: a test fires two concurrent requests with the same nonce and asserts
exactly one succeeds. A test asserts /verify rejects a replayed nonce.
```

### Prompt 4.2 — Authenticate WebSockets and reconcile the TTL

```
FILES: main.py, backend/main.py

BUG A — /ws/telemetry and /ws/events accept ANY connection. require_api_key
guards REST routes only, so with DEADSAT_API_KEY set, anyone on the LAN can
still stream live telemetry and watch recovery events. This is also why an
API-key mismatch is currently invisible: the dashboard looks healthy because
telemetry keeps flowing while every control returns 401.
FIX: accept the key as a query parameter or first message; close unauthenticated
sockets with code 1008.

BUG B — signature TTL is 120 s (crypto/sign.py TTL_SECONDS) but the agent may
schedule an uplink for a contact window up to 24 hours away. Any command
genuinely held for a real window expires before transmission.
FIX: either sign at transmission time rather than at planning time, or make the
TTL a function of the scheduled AOS. Document the choice.

BUG C — make /system/links report authentication state so a key mismatch is
diagnosable from the dashboard.

ACCEPTANCE: with DEADSAT_API_KEY set, an unauthenticated WebSocket connect is
refused; with it unset, everything works as before.
```

---

# PHASE 5 — Claims reconciliation
*~2 hours. The cheapest points in the project.*

### Prompt 5.1 — Make every claim verifiable

```
TASK: The codebase asserts things the code does not do. A reviewer who finds one
stops trusting the rest. Either implement each claim or delete it.

  1. agents/recovery_agent.py header: "All bugs fixed, all improvements applied"
     - "Improvement 2 — Fault state telemetry has noise on top of fault effects"
       FALSE: _update_nominal_drift() returns early during faults; telemetry
       freezes completely (verified: 1 distinct obc_temp_c value across 20 samples)
     - "Improvement 4 — Fallback TLE updated to recent epoch"
       FALSE: emulator/contact_calculator.py FALLBACK_TLE epoch is 24163 =
       day 163 of 2024, two years stale against the project's 2026 timeline.
       LEO TLEs degrade in days; every offline contact window is fiction.
     - "Bug Fix 4 — Contact calculator step size reduced to 10s"
       This made it 3x MORE expensive: 24 h at 10 s = 8,640 SGP4 propagations
       running synchronously inside the recovery graph.

  2. crypto/nonce.py: "SET NX — atomic" — it is a get-then-set race.

  3. crypto/verify.py: "Uses hmac.compare_digest() to prevent timing attacks" —
     never called.

  4. README.md AI Layer section: "13 telemetry features" — V2 uses 11 orbital
     elements (see models/feature_spec.py FEATURE_COLS).

  5. README.md: verify the "Live System Proof" section describes what the system
     actually does now that the frontend is connected.

ACCEPTANCE: grep the repo for "fixed", "improvement", "hardened", "secure",
"verified" — every remaining instance is backed by a test or a line of code you
can point to.
```

---

# PHASE 6 — Frontend (FIX-ONLY)
*~4 hours. No redesign. Minimum lines changed.*

### Prompt 6.1 — Fix the cold-start blank panel and the polling waste

```
FILES: frontend/components/SatelliteDashboard.tsx,
       frontend/components/OperatorControlPanel.tsx

CONSTRAINT: FIX-ONLY. Do not redesign, restyle or restructure anything. Change
the minimum number of lines. Preserve all markup and class names.

BUG A — TLE/orbit panel is blank for 5 minutes on every cold start.
In SatelliteDashboard the loader calls api.telemetry() and only proceeds
`if (f?.norad_id)`. Before the emulator's first tick get_latest_frame() returns
{}, so norad_id is undefined, the TLE fetch is skipped, and the next attempt is
300 seconds later. The UI almost always loads faster than the backend boots.
FIX: retry on a short backoff (e.g. 1 s, up to ~30 s) until norad_id resolves,
then fall back to the existing 5-minute refresh.

BUG B — /pipeline/classify polled every 15 s in OperatorControlPanel.
With artifacts untrained that is a 503 four times a minute forever; with
artifacts present it is a full transformer inference pass every 15 s on a
Raspberry Pi 4, for a number displayed in a corner.
FIX: call /pipeline/status once on mount, and only classify on demand or on a
fault transition.

ACCEPTANCE: tsc clean; a cold start shows real TLE data within ~5 s;
/pipeline/classify is not called on a timer.
```

### Prompt 6.2 — Render the data that is already being fetched

```
FILES: frontend/components/SecurityConsole.tsx, frontend/components/AiDiagnostics.tsx

CONSTRAINT: FIX-ONLY. Add display of existing state into the EXISTING layout.
Do not redesign the panels, do not add new sections if an existing one fits, do
not change styling conventions — match the surrounding markup exactly.

Six values are fetched from the backend and never rendered:
  SecurityConsole : ledger, cryptoMode, lastError
  AiDiagnostics   : statusHint, artifactsReady, lastClass

The crypto ledger matters most — it is the single best evidence that the
security layer works, it is already being fetched from /crypto/ledger, and it is
currently discarded. Render the most recent entries (id, timestamp, truncated
cmd_hash, operator).

Also surface lastError / statusHint so a backend failure is visible in the panel
rather than silent.

ACCEPTANCE: tsc clean; with CY-1 running the ledger populates; with CY-1 down
the panel says so instead of showing an empty box.
```

### Prompt 6.3 — Collapse duplicate WebSocket connections

```
FILES: frontend/useDeadsat.ts, frontend/components/SatelliteDashboard.tsx,
       frontend/components/AiDiagnostics.tsx,
       frontend/components/OperatorControlPanel.tsx

CONSTRAINT: FIX-ONLY. Do not restructure components or introduce a state
management library. The simplest correct approach is a small React context in
the EXISTING frontend/ directory (no new folders), or passing the existing
useDeadsat values down as props.

PROBLEM: the dashboard opens 6 WebSocket connections where 2 would do:
  subscribeTelemetry: useDeadsat, AiDiagnostics, SatelliteDashboard   (3)
  subscribeEvents:    useDeadsat, OperatorControlPanel, SatelliteDashboard (3)
Functionally harmless, but it triples per-frame serialisation work on a Pi 4.

FIX: keep exactly one telemetry socket and one events socket in useDeadsat and
share them.

ACCEPTANCE: browser devtools Network > WS shows exactly 2 connections; all
panels still update.
```

### Prompt 6.4 — Reconcile the 5-fault UI with the 4-fault emulator

```
FILES: frontend/api.ts (UI_FAULT_TO_BACKEND), emulator/satellite_emulator.py

CONSTRAINT: FIX-ONLY on the frontend. Prefer the backend fix.

PROBLEM: the UI offers 5 faults, the emulator models 4, so battery_fail maps to
firmware_corruption and adcs_fail maps to SEU. Selecting either shows a
diagnosis that contradicts the label the operator picked.

CHOOSE ONE:
  (a) PREFERRED — add battery_fail and adcs_fail as real fault types in
      emulator/satellite_emulator.py (FaultType enum, inject_* methods,
      _apply_fault_effects, apply_recovery handlers) and add matching entries to
      agents/procedure_library.json. Mirror to backend/.
  (b) Remove the two unsupported options from the UI dropdown.

Do not leave the current silent mismatch.

ACCEPTANCE: every fault selectable in the UI produces a diagnosis and recovery
procedure that matches its label.
```

---

# PHASE 7 — Tests & CI
*~1 day. This is what converts claims into evidence.*

### Prompt 7.1 — Unit tests for the logic that keeps breaking

```
TASK: Add unit tests. test_integration.py covers the seams; these cover logic.

CONSTRAINT: put them in the EXISTING repo root as test_units.py, or in an
existing directory. Do not create a new tests/ folder (folder structure is
frozen).

Cover:
  - _check_criteria: <, >, <=, >=, ==, string, bool, and a MISSING key
    (must fail closed, not pass)
  - normalise_fault_key: every classifier output maps to a procedure_library key
  - _emulator_frame_to_orbital_window: each fault signature crosses its
    threshold AND is not shadowed by a higher-priority rule
  - procedure selection: min_confidence skip does not uplink a stale procedure
  - apply_recovery: wrong procedure for the active fault returns False
  - emulator: 5000 ticks keeps every field in bounds; start() is idempotent
  - nonce: concurrent identical nonces -> exactly one succeeds
  - ML: no NORAD_CAT_ID in two splits; no window spans a satellite boundary

ACCEPTANCE: all tests pass; each test fails if you revert its corresponding fix
(verify this — a test that cannot fail is not a test).
```

### Prompt 7.2 — Continuous integration

```
TASK: Add CI that runs on every push.

NOTE: this needs .github/workflows/, which is a NEW FOLDER. Folder structure is
otherwise frozen — confirm with the project owner before creating it. If not
permitted, add a pre-commit hook script in the repo root instead.

CI must run:
  python test_integration.py
  python test_units.py
  cd frontend && npx tsc --noEmit
  python -m py_compile on every .py file
  test_backend_sync.py  (see Prompt 7.3)

ACCEPTANCE: a green badge in README.md that reflects a real run.
```

### Prompt 7.3 — backend/ drift guard

```
TASK: Create test_backend_sync.py in the repo root (new file, existing directory).

The backend/ tree stays duplicated by constraint, so make divergence impossible
to miss. This has already bitten once: backend/main.py used cfg., httpx. and
Depends() without importing any of them and could not import at all — the whole
backend server was dead and nothing caught it.

Assert these pairs are byte-identical, ignoring line endings:
  emulator/satellite_emulator.py   <-> backend/emulator/satellite_emulator.py
  emulator/contact_calculator.py   <-> backend/emulator/contact_calculator.py
  agents/recovery_agent.py         <-> backend/agents/recovery_agent.py
  agents/procedure_library.json    <-> backend/agents/procedure_library.json
  models/feature_spec.py           <-> backend/pipeline/feature_spec.py
  models/classifier_inference.py   <-> backend/pipeline/classifier_inference.py
  models/satellite_fault_classifier_V2.py <-> backend/pipeline/satellite_fault_classifier_V2.py
  satellite_catalog.py             <-> backend/satellite_catalog.py
  config.py                        <-> backend/config.py

For main.py <-> backend/main.py (which legitimately differ), assert the ROUTE
SETS match instead, using ast to extract @app.get/@app.post/@app.websocket paths
plus the crypto router's prefixed routes.

ACCEPTANCE: the test fails if you edit one side of any pair.
```

---

# PHASE 8 — Reproducibility & deployment proof
*~1 day. The last 0.5 points.*

### Prompt 8.1 — Make training reproducible

```
FILES: models/satellite_fault_classifier_V2.py, requirements.txt

  - augment_fault_samples() uses the GLOBAL numpy RNG
    (np.random.normal(0, std, n_needed)) while everything around it uses
    random_state=CONFIG["random_seed"]. Training is not reproducible.
  - _generate_synthetic_class() re-creates default_rng(seed) on every call, so
    all four classes draw the SAME noise sequence — correlated noise across
    classes.
  - requirements.txt pins langgraph==1.2.4 and langchain-core==1.4.2 exactly but
    floors everything else with >= and no ceiling, and there is no lockfile.

TASK: thread a seeded Generator through all augmentation, give each class a
distinct derived seed, and add a lockfile (pip freeze > requirements.lock).

ACCEPTANCE: two clean training runs produce identical meta.json and matching
evaluation metrics.
```

### Prompt 8.2 — Orbital mechanics cleanup

```
FILE: emulator/contact_calculator.py  (mirror to backend/emulator/)

  - FALLBACK_TLE epoch 24163 is June 2024 — refresh it, and add a startup
    warning when the TLE in use is older than ~30 days.
  - find_next_contact(search_hours=24, step_seconds=10) performs 8,640 SGP4
    propagations synchronously inside the recovery graph. Replace with
    coarse-then-refine (e.g. 60 s scan, then bisect near the crossing) — roughly
    50x cheaper, which matters on a Pi 4.
  - get_contact_summary() runs THREE propagation passes (get_current_azel,
    find_next_contact, is_in_contact_now which calls get_current_azel again).
    Reduce to one.
  - Fetched TLE lines are never validated before Satrec.twoline2rv — check the
    "1 "/"2 " prefixes, 69-char length and checksum. A CelesTrak error page
    currently produces unpredictable behaviour.
  - GROUND_STATION["alt_m"] is metres but the _eci_to_azel docstring says km.
    The maths is right for metres; fix the docstring.

ACCEPTANCE: a contact-window calculation completes in < 1 s; a malformed TLE
raises a clear error instead of propagating garbage.
```

### Prompt 8.3 — Threat model

```
TASK: Create docs/THREAT_MODEL.md (new file, existing docs/ directory).

The project's thesis is authenticated satellite command. A written threat model
is what separates "we used post-quantum crypto" from "we understood why."

Cover:
  - Adversary capabilities: passive RF eavesdropper, active uplink forger,
    replay attacker, future quantum-capable attacker (harvest-now-decrypt-later)
  - Trust boundaries: operator browser | Pi #1 API | CY-1 signer | Pi #2 RF |
    the spacecraft itself
  - IN SCOPE: uplink command forgery, replay, signature downgrade, stale-command
    execution
  - OUT OF SCOPE: physical access to either Pi, supply-chain compromise,
    ground-station RF jamming
  - Why hybrid Ed25519 + ML-DSA-65 rather than either alone
  - Residual risks, stated honestly — including that the emulator does not
    itself verify signatures (the ground segment does), and what that would mean
    on real hardware

ACCEPTANCE: every mitigation named in the document points to a specific file and
function, or is explicitly listed as future work.
```

---

## Sequence & expected score

| Phase | Effort | Score after |
|---|---|---|
| 0 — baseline | 1 h | 6.8 |
| 1 — dataset | 1 d | 7.4 |
| 2 — ML correctness | 4 h | 8.2 |
| 3 — recovery & emulator | 1 d | 8.6 |
| 4 — security | 1 d | 9.0 |
| 5 — claims | 2 h | 9.1 |
| 6 — frontend fixes | 4 h | 9.2 |
| 7 — tests & CI | 1 d | 9.4 |
| 8 — reproducibility & proof | 1 d | 9.5 |

**Total ≈ 6–7 working days**, spread over ~2 weeks allowing for the training
runs and debugging.

**If you only have three days:** Phase 0, Phase 1, Phase 2, Phase 5. That fixes
the invalid evaluation and the false claims — the two things a careful reviewer
finds first — and lands around 8.2.
