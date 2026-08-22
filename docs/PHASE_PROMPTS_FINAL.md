# DeadSat Resurrection — Complete Phase-wise Prompt List (6.8 → 9.5)

Single consolidated source. Supersedes `docs/PHASE_PROMPTS.md` and
`docs/PHASE_PROMPTS_v2.md`. Every prompt below is self-contained and
copy-pasteable: it states the verified problem, the constraints, and the
acceptance criteria.

Findings are traced to file and line against the checkout at
`D:\faraway\DEADSAT-RESURRECTION`, audited 2026-08-15. Supporting evidence is in
`docs/WIRING_AUDIT_2026-08-15.md`, `docs/BUG_AUDIT.md` and `docs/CRASH_REPORT.md`.

**32 prompts across 9 phases. ≈7–8 working days.**

---

## Contents

| Phase | Prompts | Effort | Score after |
|---|---|---|---|
| [0 — Boot & render](#phase-0--boot--render) | 0.0 – 0.5 | 4 h | 6.8 |
| [1 — Dataset foundation](#phase-1--dataset-foundation) | 1.1 | 1 d | 7.4 |
| [2 — ML pipeline correctness](#phase-2--ml-pipeline-correctness) | 2.1 – 2.3 | 6 h | 8.2 |
| [3 — Recovery & emulator](#phase-3--recovery--emulator-correctness) | 3.1 – 3.3 | 1 d | 8.6 |
| [4 — Security](#phase-4--security) | 4.0 – 4.2 | 1.5 d | 9.0 |
| [5 — Claims reconciliation](#phase-5--claims-reconciliation) | 5.1 – 5.2 | 4 h | 9.1 |
| [6 — Frontend (fix-only)](#phase-6--frontend-fix-only) | 6.0 – 6.4 | 6 h | 9.2 |
| [7 — Tests & CI](#phase-7--tests--ci) | 7.1 – 7.3 | 1 d | 9.4 |
| [8 — Reproducibility & proof](#phase-8--reproducibility--deployment-proof) | 8.1 – 8.3 | 1 d | 9.5 |

---

## Global constraints — paste at the top of every session

```
PROJECT: D:\faraway\DEADSAT-RESURRECTION

HARD CONSTRAINTS (apply to every task):

1. DO NOT change the folder structure. No moving, renaming, merging or deleting
   existing files or directories. New files may only be added inside directories
   that already exist. No new folders without an explicit exception (see
   "Constraint exceptions" below).

2. DO NOT push to or modify the GitHub repo. Work locally only. Local commits
   are encouraged (see Prompt 5.2 D).

3. The `backend/` tree is a parallel copy that must stay in sync. Every change to
   a shared module must be mirrored:
     emulator/ <-> backend/emulator/
     agents/   <-> backend/agents/
     models/   <-> backend/pipeline/     (note the different directory name)
     crypto/   <-> backend/crypto/
     main.py   <-> backend/main.py
     config.py <-> backend/config.py
   Note: `ml/` at repo root is a compatibility shim that re-exports from
   `models/`. It has no backend counterpart and needs none — do not add logic
   to it.

4. FRONTEND IS FIX-ONLY. Do not redesign, restructure, re-style or rewrite any
   component. Change the minimum number of lines needed to fix the specific
   defect described. Preserve all existing markup, class names and layout.

5. After every change run:
     python test_integration.py        (must not regress — see exception C3)
     python -m py_compile <every touched file>
     cd frontend && npx tsc --noEmit   (after Prompt 0.0)

6. Read docs/WIRING_AUDIT_2026-08-15.md, docs/BUG_AUDIT.md and
   docs/CRASH_REPORT.md first for context.
```

### Constraint exceptions — decide these before starting

| # | Conflict | Blocks | Options |
|---|---|---|---|
| **C1** | ~~`train_classifier.py:78` writes to `ROOT / "model_artifacts"` — a **new folder**.~~ **RESOLVED 2026-08-15: exception granted.** `model_artifacts/` is permitted as a build output. Artifacts are **built on first run and never committed** — added to `.gitignore`, documented in README. | Phase 2 | *(closed)* |
| **C2** | CI needs `.github/workflows/` — a new folder. | Phase 7 | (a) Grant the exception. (b) Root-level `pre_commit.py` runner instead. |
| **C3** | Constraint 5 says "109+ passed, 0 failed", but `test_integration.py` has not been re-run since the wiring pass. Do not treat 109 as verified. | Everything | Run it once at the end of Phase 0, record the real number, write it into constraint 5. |

---

# PHASE 0 — Boot & render

*~4 hours. The system does not currently start or display anything. Nothing
below Phase 0 is testable until 0.0–0.3 pass.*

---

### Prompt 0.0 — The frontend serves a blank page

```
VERIFIED BLOCKER 1: frontend/index.html line 12 is:

    <script type="module" src="/src/main.tsx"></script>

There is no frontend/src/ directory. The entry file is frontend/main.tsx.
Vite resolves module paths from the project root, gets a 404, and renders an
empty <div id="root"></div>. The dashboard has never displayed anything.

VERIFIED BLOCKER 2: frontend/node_modules is a half-finished install — 198
packages present, but no `vite`, no `typescript`, and no node_modules/.bin/
directory. `npm run dev`, `npm run build` and `npm run lint` all fail before
doing anything.

TASK:
  1. frontend/index.html — change the src to "/main.tsx".
  2. cd frontend && rm -rf node_modules package-lock.json && npm install
  3. Confirm `npm run dev` serves the app and the React tree mounts.

CONSTRAINT: FIX-ONLY. One character in index.html. Do NOT create a src/ folder
and move files into it — folder structure is frozen, and the one-line path fix
is the correct minimal change.

ACCEPTANCE: `npm run dev` serves on :3000 and the LandingPage renders. Browser
console shows no module-resolution errors.
```

---

### Prompt 0.1 — Type-check the frontend

```
The frontend has never been type-checked; npm install could not reach the
registry in the environment where it was written, so every TypeScript change is
structurally verified only. Prompt 0.0 must be done first — tsc cannot run at
all until typescript is installed.

TASK: Run the TypeScript compiler and fix every error it reports.
  cd frontend
  npm run lint          # runs: tsc --noEmit

CONSTRAINT: FIX-ONLY. Fix type errors with the smallest possible change. Do not
refactor, do not restructure components, do not change JSX or styling. If a fix
would require a redesign, report it instead of doing it.

Files most likely to have errors (recently modified):
  frontend/api.ts
  frontend/useDeadsat.ts
  frontend/App.tsx
  frontend/index.html          <- changed in 0.0
  frontend/main.tsx            <- imports './App.tsx' with an explicit .tsx
                                  extension; legal only because tsconfig sets
                                  allowImportingTsExtensions. Confirm tsc accepts it.
  frontend/components/SatelliteDashboard.tsx
  frontend/components/AiDiagnostics.tsx
  frontend/components/SecurityConsole.tsx
  frontend/components/OperatorPanel.tsx
  frontend/components/OperatorControlPanel.tsx

ACCEPTANCE: `npx tsc --noEmit` exits 0. Report every error found and how it was
fixed. Do not suppress errors with `any` or `@ts-ignore` unless you explain why.
```

---

### Prompt 0.2 — Confirm both servers actually boot

```
VERIFIED: backend/main.py's missing-import problem (it used cfg., httpx. and
Depends() without importing them) is fixed. A different one is not:

  backend/main.py:48    from crypto_routes import router as crypto_router,
                                                  startup_crypto, limiter
  backend/main.py:60-61 from slowapi import Limiter, _rate_limit_exceeded_handler

crypto_routes pulls in `oqs`, `redis` and `nacl`.

NONE of slowapi, redis, pynacl, liboqs-python appear in requirements.txt OR
backend/requirements.txt. `import backend.main` therefore fails with
ModuleNotFoundError before the app object is constructed. The backend tree still
cannot start.

Root main.py is unaffected — it never imports the crypto package. That is a
problem in its own right; see Prompt 4.0.

TASK:
  1. Add slowapi, redis, pynacl to backend/requirements.txt. For liboqs-python,
     note that backend/crypto/mock_oqs_nacl.py exists as a shim — determine
     whether real liboqs is required or the shim is the intended path, and
     document the answer in backend/crypto/README.md.
  2. Then run:
       pip install -r requirements.txt
       python -c "import main"
       python -c "import sys; sys.path.insert(0,'backend'); import backend.main"
       uvicorn main:app --port 8000
       curl localhost:8000/health
       curl localhost:8000/system/links
       curl localhost:8000/system/config
  3. Run `python test_integration.py` and RECORD THE BASELINE NUMBER (see
     constraint exception C3). Write it into the global constraints block.

ALSO REPORT (do not fix — that is Prompt 5.2 A): both main.py files place
`if __name__ == "__main__": uvicorn.run("main:app")` mid-file — main.py:688 with
~340 more lines of route definitions after it, backend/main.py:510 likewise.

ACCEPTANCE: both trees import without error; /system/links returns JSON listing
emulator, ai1_classifier, ai2_agent, crypto, rf_station, websocket_clients.
Expect ai1_classifier and crypto to report DOWN at this stage — correct, and
addressed by Prompts 0.3 and 2.3.
```

---

### Prompt 0.3 — Make CY-1 startable, or the security thesis is untestable

```
VERIFIED BLOCKER: nothing in the repository can serve :8001. CY-1 is referenced
by config.CY1_BASE and proxied by six endpoints in main.py, but:

  crypto/sign.py:1    ->  import oqs
  crypto/verify.py:1  ->  import oqs

backend/crypto/ has a mock_oqs_nacl.py shim imported as the FIRST line of its
copies of both files. The root crypto/ copy does not have the shim file at all,
and root crypto/ is also missing __init__.py (backend/crypto/ has one).

So `python crypto/crypto_routes.py` — the only entry point that would listen on
:8001 — dies on ImportError unless real liboqs is installed system-wide.

This is why every crypto link reports DOWN and why Phase 4's acceptance criteria
cannot currently be evaluated.

TASK:
  1. Copy backend/crypto/mock_oqs_nacl.py and backend/crypto/__init__.py into
     crypto/. (Adding files inside an existing directory is permitted.)
  2. Add `import mock_oqs_nacl` as the first line of crypto/sign.py and
     crypto/verify.py, matching backend/crypto/ exactly. This also resolves the
     only remaining root<->backend drift in those two files.
  3. Verify:
       python crypto/crypto_routes.py     # serves :8001
       curl localhost:8001/health
  4. Re-run curl localhost:8000/system/links — crypto must flip to connected.

CONSTRAINT: the shim must make it OBVIOUS when mock crypto is in use. If
mock_oqs_nacl is active, /health must say so, and it must be impossible to
present a mock-signed command as verified (see Prompt 4.0).

ACCEPTANCE: CY-1 starts from the root tree; /system/links reports crypto
connected; docs/WIRING.md's example JSON matches reality.
```

---

### Prompt 0.4 — Reconcile root ↔ backend drift before the guard is written

```
CONTEXT: Prompt 7.3 adds test_backend_sync.py asserting byte-identical pairs.
Written today it fails immediately — the trees have ALREADY drifted:

  MEASURED (differing line counts):
    real_data_fetcher.py           <-> backend/real_data_fetcher.py            22
    models/classifier_inference.py <-> backend/pipeline/classifier_inference.py  6
    crypto/sign.py                 <-> backend/crypto/sign.py                    1  (the shim)
    crypto/verify.py               <-> backend/crypto/verify.py                  1  (the shim)

  VERIFIED IN SYNC:
    agents/recovery_agent.py, agents/procedure_library.json,
    emulator/satellite_emulator.py, satellite_catalog.py, config.py,
    requirements.txt

TASK: For each drifted pair, determine which side is correct — do not blindly
copy one over the other, read the diff and decide. Prompt 0.3 resolves the two
crypto pairs as a side effect.

Report, for each pair, which side won and why. This is the last moment where the
drift is small enough to reason about.

ACCEPTANCE: all pairs in Prompt 7.3's list are byte-identical (ignoring line
endings) before any Phase 1 work begins.
```

---

### Prompt 0.5 — CORS: why half the dashboard will be blank on the demo LAN

```
VERIFIED PROBLEM — surfaces the moment the frontend renders (Prompt 0.0):

  frontend/package.json dev script:  vite --port=3000 --host=0.0.0.0
  config.py:139 CORS default:  http://localhost:3000, http://127.0.0.1:3000,
                               http://localhost:5173

On the two-Pi demo the operator opens http://<LAN-IP>:3000. That origin is not
in the allow-list.

WebSockets are exempt from CORS. So telemetry streams fine and the header badge
reads "LIVE TM" with a healthy link count — while EVERY REST panel fails
silently inside its .catch(): TLE/orbital elements, catalog, crypto status,
ledger, alerts, pipeline status, /system/links detail, RF spectrum.

This is the failure mode most likely to appear for the first time in front of
judges, and it looks like a frontend bug when it is a config default.

TASK:
  1. Document it prominently in docs/WIRING.md's setup section and in
     .env.example: DEADSAT_CORS_ORIGINS must list the operator's real origin.
  2. Add a startup warning in config.print_banner() when API_HOST is 0.0.0.0
     (LAN-facing) but every CORS origin is a loopback address — that combination
     is always a misconfiguration.
  3. Do NOT "fix" it with allow_origins=["*"]. The API exposes fault injection,
     recovery and reset.

ACCEPTANCE: starting the API bound to 0.0.0.0 with loopback-only CORS prints a
visible warning naming the variable to set.
```

---

# PHASE 1 — Dataset foundation

*~1 day. The single highest-leverage change in the project.*

---

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
change folder structure. generate_dataset.py must run with NO network access —
the three input CSVs are local and sgp4 propagation is local. If any code path
reaches for CelesTrak or N2YO, gate it behind an explicit --refresh flag that
defaults off, or Phase 8.1's reproducibility claim cannot hold.

ACCEPTANCE:
  - every satellite has >= CONFIG["seq_len"] (8) distinct epochs
  - `python generate_dataset.py --verify` shows >= 300 rows of EACH of the four
    classes, all arising from injected series (not _generate_synthetic_class)
  - ecc_delta and REV_DELTA are non-zero for > 90% of rows
  - assign_fault_labels() agrees with ground_truth_fault for > 95% of rows
  - runs offline
```

---

# PHASE 2 — ML pipeline correctness

*~6 hours. Without this, no accuracy number from this project can be quoted.*

---

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

---

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

### Prompt 2.3 — Ship the artifacts, and make their absence honest

```
VERIFIED: models/classifier_inference.py:138-145 requires
  model_artifacts/transformer_encoder.pt
  model_artifacts/isolation_forest.pkl
  model_artifacts/scaler.pkl

There is no model_artifacts/ directory and no .pt/.pkl/.joblib file anywhere in
the repository. AI-1 has never been trained on this checkout.

MEASURED CONSEQUENCES IN THE RUNNING SYSTEM:
  /system/links               -> ai1_classifier permanently DOWN; the header
                                 link count can never reach n/n
  /pipeline/status            -> artifacts_ready: false
  /pipeline/classify          -> HTTP 503, always
  AiDiagnostics "recalibrate" -> "classification failed — 503" every time
                                 (AiDiagnostics.tsx:78)
  OperatorControlPanel        -> accuracy and truePos pinned at 0, re-polled
                                 every 15 s forever (OperatorControlPanel.tsx:141-152)
  /pipeline/run without skip_classifier -> fails

TASK (after 2.1 and 2.2):
  1. Resolve constraint exception C1 (the model_artifacts/ folder) with the
     project owner.
  2. Run train_classifier.py and produce the three artifacts.
  3. Decide and document whether artifacts are committed to the repo or built on
     first run. If built: add the command to README setup and make
     /pipeline/status's `hint` field say exactly what to run — it already does;
     confirm the frontend surfaces it (Prompt 6.2).
  4. Add a .gitignore entry if they are build outputs.

ACCEPTANCE: from a clean checkout, a documented command produces artifacts and
/system/links reports ai1_classifier connected.
```

---

# PHASE 3 — Recovery & emulator correctness

*~1 day.*

---

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

---

### Prompt 3.2 — Make success_criteria authoritative

```
FILE: agents/recovery_agent.py  (mirror to backend/agents/)

NOTE: do Prompt 4.0 FIRST. The verification gate currently fails every command
before the fallback logic is ever consulted, so this prompt's acceptance test
cannot pass until 4.0 is done.

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

---

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

*~1.5 days.*

---

### Prompt 4.0 — Every recovery currently fails, and the real crypto is dead code

*The single largest functional gap in the project. Do this before 4.1 and 4.2,
and before 3.2.*

```
TWO VERIFIED PROBLEMS, one cause.

PROBLEM A — the hybrid crypto implementation is never mounted on the tree you run.

  backend/main.py:211  ->  app.include_router(crypto_router)
  main.py              ->  NO include_router call anywhere

Root main.py instead hand-rolls /crypto/* at lines 431-637. Those handlers
either proxy to CY-1 or return mocks. The actual hybrid Ed25519 + ML-DSA-65
implementation — crypto/sign.py, verify.py, ledger.py, nonce.py,
rogue_detector.py — is never imported by the root server.

So the two trees have materially different security behaviour, and the one that
boots is the mock one. Every claim in README about post-quantum signing is, on
the root tree, describing unreachable code.

PROBLEM B — with CY-1 down, recovery is guaranteed to fail. Traced end to end:

  1. config.py:151   REQUIRE_COMMAND_VERIFICATION = True   (default)
  2. config.py:158   ALLOW_MOCK_SIGNING = False            (default)
  3. main.py:447-458 /crypto/sign catches the CY-1 connection failure and
                     returns a fabricated "MOCK_ML_DSA_..." signature with
                     "mock": true
  4. agents/recovery_agent.py:301  carries `mock` onto the signed command
  5. agents/recovery_agent.py:363  if cmd.get("mock"):
                                       return (False, "MOCK_SIGNATURE")
  6. agents/recovery_agent.py:455-465  verification gate refuses the uplink and
                                       sets state["error"]
  7. useDeadsat.ts:78-85  renders "RECOVERY FAILED: ..." in the console

The gate is behaving CORRECTLY — an unverifiable signature must not execute. The
defect is that step 3 manufactures a fake signature at all, and that with no
runnable CY-1 (Prompt 0.3) there is no path where recovery can ever succeed.

TASK:
  1. Decide the canonical crypto path: mount crypto_router on root main.py so
     both trees behave identically, OR delete the hand-rolled handlers and make
     root main.py proxy-only. Mounting the router is preferred — it is the code
     the project's thesis depends on. Mirror to backend/.
  2. /crypto/sign must NOT fabricate a signature when CY-1 is unreachable.
     Return 503 with a clear reason. A mock signature that is immediately
     rejected downstream is worse than an honest failure — it burns a recovery
     attempt and produces a misleading log line.
  3. If a mock path is kept for bench demos, it must be opt-in via
     DEADSAT_ALLOW_MOCK_SIGNING=1 (the flag already exists and already defaults
     off — honour it at the SIGNING endpoint, not only in the agent), and the
     response and the UI must both say MOCK in plain language.
  4. After 0.3 gives you a runnable CY-1, prove the full path: inject ->
     classify -> sign -> verify -> uplink -> recovery success.

ACCEPTANCE:
  - with CY-1 running: a recovery completes and /crypto/ledger has a new entry
    signed by the real hybrid path, not a mock
  - with CY-1 down and DEADSAT_ALLOW_MOCK_SIGNING unset: /crypto/sign returns
    503, the agent reports SIGNING_UNAVAILABLE (not MOCK_SIGNATURE), and the UI
    says the crypto service is offline
  - root main.py and backend/main.py expose the same /crypto/* route set
```

---

### Prompt 4.1 — Fix nonce replay protection

```
FILE: crypto/nonce.py, crypto/crypto_routes.py  (mirror to backend/crypto/)
Requires Prompt 0.3 — CY-1 must be runnable before any of this is testable.

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

---

### Prompt 4.2 — Authenticate WebSockets, reconcile the TTL, kill the duplicate route

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

BUG D — backend/main.py registers /crypto/check-command TWICE. The crypto router
is mounted at line 211 and defines it; @app.post("/crypto/check-command") at
line 413 defines it again. FastAPI serves the first match, so the 60-line
handler at 413 is unreachable dead code. Two divergent implementations of a
security endpoint, one of which never runs, is exactly the kind of thing that
gets "fixed" later by someone editing the dead one.
FIX: delete one. Given Prompt 4.0 makes the router canonical, delete the @app
handler and mirror the result to root main.py.

ACCEPTANCE: with DEADSAT_API_KEY set, an unauthenticated WebSocket connect is
refused; with it unset, everything works as before. /crypto/check-command has
exactly one implementation per tree.
```

---

# PHASE 5 — Claims reconciliation

*~4 hours. The cheapest points in the project.*

---

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

  6. README.md:467-468 setup instructions say:
         cd frontend/dashboard && npm install && npm run dev
         cd frontend/operator  && npm install && npm run dev
     NEITHER DIRECTORY EXISTS. There is one frontend/. Anyone following the
     README verbatim cannot start the project. Fix the paths and add the
     VITE_API_BASE step from frontend/.env.example.

  7. docs/FIX_PRIORITY.md states SatelliteDashboard.tsx is "1760 lines, 30
     Math.random() calls, still simulated." Measured today: 1858 lines, 5
     Math.random() calls, all five in the decorative starfield geometry
     (lines 698-706). The component IS wired to the backend. That status table
     is stale and understates the work already done — update it, or a reviewer
     reads it and assumes the dashboard is fake.

  8. api.ts frameToTelemetryState() still fabricates three values: lat and lng
     advance by a fixed +0.002 / +0.005 per frame, and altitude/velocity fall
     back to the constants 402.18 / 7.672. The globe is not showing the
     satellite's real position. The catalog and ContactCalculator already have
     what is needed to compute it. Either compute it, or label the globe as an
     illustration. Do not leave it presented as telemetry.

  9. frontend/package.json declares @google/genai, express and dotenv as
     dependencies. Nothing in the frontend imports any of them. Remove them —
     an unused AI SDK in a project whose thesis is deterministic on-board
     recovery invites exactly the wrong question.

ACCEPTANCE: grep the repo for "fixed", "improvement", "hardened", "secure",
"verified" — every remaining instance is backed by a test or a line of code you
can point to.
```

---

### Prompt 5.2 — Structural fragility that has no functional symptom yet

```
Low severity, cheap, and each one is a trap for the next person.

  A. main.py:688 and backend/main.py:510 — `if __name__ == "__main__":
     uvicorn.run("main:app")` sits in the middle of the file with hundreds of
     lines of route definitions after it. It works only because uvicorn
     re-imports the module under the name "main", executing the file a second
     time. Running `python main.py` therefore constructs two SatelliteEmulator
     instances and runs every module-level side effect twice.
     FIX: move the __main__ block to the end of both files. Nothing else moves.

  B. main.py:733-741 — /catalog/search reaches into private attributes
     cat._loaded and cat._catalog, and manually calls cat.load(). Every other
     catalog endpoint uses the public API. Add a public search method to
     satellite_catalog.py and use it. Mirror to backend/.

  C. SatelliteDashboard.tsx:347 — dead line:
         const noradId = 0; // replaced below by the live frame's norad_id
     Nothing reads it. Delete.

  D. The repository is entirely uncommitted against git HEAD (every tracked file
     shows modified). Whatever the reason, the wiring pass is not recoverable if
     anything goes wrong in Phases 1-8. Commit a baseline locally before
     starting. (Local commit only — the global constraint forbids pushing.)

  E. D:\faraway\deadsat_resurrectuion nodel pipeline\ is a stale double-nested
     extraction of an older zip (dated 2026-08-07), containing smaller earlier
     versions of 8 files already in the project. It is outside the repo, so it
     breaks no constraint, but it is a live source of "which file am I editing"
     confusion. Delete it and the accompanying .zip, or move both out of
     D:\faraway.

ACCEPTANCE: py_compile clean, tsc clean, test_integration.py unchanged, and
`python main.py` starts exactly one emulator.
```

---

# PHASE 6 — Frontend (fix-only)

*~6 hours. No redesign. Minimum lines changed.*

---

### Prompt 6.0 — The WebSocket history envelope is parsed as a telemetry frame

*The most visible wrong-data bug in the UI. Do this before 6.1–6.4.*

```
VERIFIED BUG. main.py:653-658 — the FIRST message on /ws/telemetry is not a
frame, it is an envelope:

    {"type": "history", "frames": [...up to 60 frames...], "count": 60}

api.ts subscribe() JSON.parses every message and passes it straight to the frame
callback. Nothing checks `type`. Three components receive an object with no
telemetry fields on every connect AND every reconnect:

  SatelliteDashboard.tsx:477
      Math.round(undefined) -> NaN -> the OBC register pane renders literally
      SP: 0x1FFF00NaN
  SatelliteDashboard.tsx:461
      a zeroed junk point is pushed onto the chart series
  SatelliteDashboard.tsx:487
      log line reads  "WS frame undefined — health=n/a"
  AiDiagnostics.tsx:52-68
      all five diagnostic channels flash 0.0 / "DOWNLINK OFF" in red CRITICAL
      for ~1 second on every connect
  useDeadsat.ts:139
      `frame` state briefly holds the envelope

AND: the 60 backfilled frames are silently discarded. The history message exists
specifically so charts fill instantly on connect (per the docstring at
main.py:646-649) — that has never worked.

TASK — FIX-ONLY, minimum lines, in frontend/api.ts:

  Give subscribeTelemetry an optional onHistory callback and branch on type:

      const msg = JSON.parse(ev.data);
      if (msg?.type === 'history') { onHistory?.(msg.frames ?? []); return; }
      onMessage(msg as TelemetryFrame);

  Then in SatelliteDashboard.tsx, pass onHistory to seed setHistoryData via the
  existing frameToPoint mapper. This also makes the separate api.history(20)
  fetch at SatelliteDashboard.tsx:371-383 redundant — remove it only if the
  socket path is confirmed working, not before.

CONSTRAINT: do not restructure the subscribe() helper, do not change the
reconnect logic, do not touch markup or styling.

ACCEPTANCE: no NaN in the OBC register pane; AiDiagnostics does not flash
CRITICAL on connect; the chart is populated with real history within one second
of the socket opening.
```

---

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
Raspberry Pi 4, for a number displayed in a corner. Prompt 2.3 removes the 503
storm but the timer should still go.
FIX: call /pipeline/status once on mount, and only classify on demand or on a
fault transition.

ACCEPTANCE: tsc clean; a cold start shows real TLE data within ~5 s;
/pipeline/classify is not called on a timer.
```

---

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
rather than silent. Two concrete cases already returning useful text that is
being thrown away:
  /pipeline/status  -> `hint`: "Train with: python train_classifier.py"
  /crypto/status    -> `message`: "CY-1 not running — signatures cannot be verified"
A dashboard that says "AI-1 artifacts missing — run train_classifier.py" is
worth more than one that silently shows 0.00%.

ACCEPTANCE: tsc clean; with CY-1 running the ledger populates; with CY-1 down
the panel says so instead of showing an empty box.
```

---

### Prompt 6.3 — Collapse duplicate WebSocket connections

```
FILES: frontend/useDeadsat.ts, frontend/components/SatelliteDashboard.tsx,
       frontend/components/AiDiagnostics.tsx,
       frontend/components/OperatorControlPanel.tsx

Do this AFTER Prompt 6.0 — fixing the envelope bug in one shared socket is
simpler than in three.

CONSTRAINT: FIX-ONLY. Do not restructure components or introduce a state
management library. The simplest correct approach is a small React context in
the EXISTING frontend/ directory (no new folders), or passing the existing
useDeadsat values down as props.

PROBLEM: the dashboard opens 6 WebSocket connections where 2 would do:
  subscribeTelemetry: useDeadsat, AiDiagnostics, SatelliteDashboard       (3)
  subscribeEvents:    useDeadsat, OperatorControlPanel, SatelliteDashboard (3)
Functionally harmless, but it triples per-frame serialisation work on a Pi 4.

FIX: keep exactly one telemetry socket and one events socket in useDeadsat and
share them.

ACCEPTANCE: browser devtools Network > WS shows exactly 2 connections; all
panels still update.
```

---

### Prompt 6.4 — Reconcile the 5-fault UI with the 4-fault emulator

```
FILES: frontend/api.ts (UI_FAULT_TO_BACKEND), emulator/satellite_emulator.py

CONSTRAINT: FIX-ONLY on the frontend. Prefer the backend fix.

PROBLEM: the UI offers 5 faults, the emulator models 4, so battery_fail maps to
firmware_corruption and adcs_fail maps to SEU. Selecting either shows a
diagnosis that contradicts the label the operator picked. The code already
admits this in a comment: "The emulator has no dedicated battery fault;
firmware_corruption is the closest available analogue."

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

---

### Prompt 7.1 — Unit tests for the logic that keeps breaking

```
TASK: Add unit tests. test_integration.py covers the seams; these cover logic.

CONSTRAINT: put them in the EXISTING repo root as test_units.py, or in an
existing directory. Do not create a new tests/ folder (folder structure is
frozen).

Cover:
  - _check_criteria: <, >, <=, >=, ==, string, bool, and a MISSING key
    (must fail closed, not pass)                                      [3.2]
  - normalise_fault_key: every classifier output maps to a
    procedure_library key
  - _emulator_frame_to_orbital_window: each fault signature crosses its
    threshold AND is not shadowed by a higher-priority rule           [1.1]
  - procedure selection: min_confidence skip does not uplink a stale
    procedure                                                          [3.2 E]
  - apply_recovery: wrong procedure for the active fault returns False [3.1]
  - emulator: 5000 ticks keeps every field in bounds; start() is
    idempotent                                                         [3.3]
  - nonce: concurrent identical nonces -> exactly one succeeds         [4.1]
  - ML: no NORAD_CAT_ID in two splits; no window spans a satellite
    boundary                                                           [2.1]
  - api.ts message routing: a {type:'history'} payload must NOT reach the
    frame handler. Testable without a browser by exporting the message
    dispatch as a pure function.                                       [6.0]
  - /crypto/sign with CY-1 unreachable and DEADSAT_ALLOW_MOCK_SIGNING
    unset returns 503, not a MOCK_ signature                           [4.0]
  - root main.py and backend/main.py expose identical /crypto/* route
    sets                                                               [4.0]
  - config: API_HOST=0.0.0.0 with loopback-only CORS_ORIGINS triggers
    the warning                                                        [0.5]
  - emulator.get_latest_frame() returns {} before the first tick — assert
    every consumer handles it. /telemetry currently does
    frame["overall_health"] = ... on that empty dict and returns a
    one-key object; confirm that is intentional and the frontend tolerates it.

ACCEPTANCE: all tests pass; each test fails if you revert its corresponding fix
(verify this — a test that cannot fail is not a test).
```

---

### Prompt 7.2 — Continuous integration

```
TASK: Add CI that runs on every push.

NOTE: this needs .github/workflows/, which is a NEW FOLDER — resolve constraint
exception C2 with the project owner first. If not permitted, add a pre-commit
hook script in the repo root instead.

CI must run:
  python test_integration.py
  python test_units.py
  cd frontend && npm ci && npx tsc --noEmit
      (npm ci, not npm install — the broken node_modules state found in Prompt
       0.0 is exactly what CI exists to catch)
  python -m py_compile on every .py file
  python test_backend_sync.py   (see Prompt 7.3)

ACCEPTANCE: a green badge in README.md that reflects a real run.
```

---

### Prompt 7.3 — backend/ drift guard

```
TASK: Create test_backend_sync.py in the repo root (new file, existing directory).

Run Prompt 0.4 FIRST. Written today this test fails on four pairs, because the
trees have already drifted — the exact scenario the test exists to prevent,
which rather makes the case for it.

The backend/ tree stays duplicated by constraint, so make divergence impossible
to miss. This has already bitten once: backend/main.py used cfg., httpx. and
Depends() without importing any of them and could not import at all — the whole
backend server was dead and nothing caught it.

Assert these pairs are byte-identical, ignoring line endings:
  emulator/satellite_emulator.py   <-> backend/emulator/satellite_emulator.py
  emulator/contact_calculator.py   <-> backend/emulator/contact_calculator.py
  emulator/__init__.py             <-> backend/emulator/__init__.py
  agents/recovery_agent.py         <-> backend/agents/recovery_agent.py
  agents/procedure_library.json    <-> backend/agents/procedure_library.json
  agents/__init__.py               <-> backend/agents/__init__.py
  models/feature_spec.py           <-> backend/pipeline/feature_spec.py
  models/classifier_inference.py   <-> backend/pipeline/classifier_inference.py
  models/satellite_fault_classifier_V2.py <-> backend/pipeline/satellite_fault_classifier_V2.py
  satellite_catalog.py             <-> backend/satellite_catalog.py
  config.py                        <-> backend/config.py
  real_data_fetcher.py             <-> backend/real_data_fetcher.py
  crypto/sign.py                   <-> backend/crypto/sign.py
  crypto/verify.py                 <-> backend/crypto/verify.py
  crypto/nonce.py                  <-> backend/crypto/nonce.py
  crypto/ledger.py                 <-> backend/crypto/ledger.py
  crypto/keygen.py                 <-> backend/crypto/keygen.py
  crypto/rogue_detector.py         <-> backend/crypto/rogue_detector.py
  crypto/crypto_routes.py          <-> backend/crypto/crypto_routes.py

  (root crypto/ is currently missing __init__.py and mock_oqs_nacl.py entirely —
   Prompt 0.3 adds them. Until then the crypto pairs cannot pass.)

For main.py <-> backend/main.py (which legitimately differ), assert the ROUTE
SETS match instead, using ast to extract @app.get/@app.post/@app.websocket paths
plus the crypto router's prefixed routes. Account for the /crypto prefix and for
backend/main.py's duplicate /crypto/check-command registration (Prompt 4.2 D) —
after that fix the sets should match exactly.

ACCEPTANCE: the test fails if you edit one side of any pair.
```

---

# PHASE 8 — Reproducibility & deployment proof

*~1 day. The last 0.5 points.*

---

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
    (Note: Prompt 0.2 adds slowapi, redis and pynacl — include them.)

TASK: thread a seeded Generator through all augmentation, give each class a
distinct derived seed, and add a lockfile (pip freeze > requirements.lock).

ACCEPTANCE: two clean training runs produce identical meta.json and matching
evaluation metrics.
```

---

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

ALSO — import hygiene, documentation only, do not restructure folders:
  contact_calculator.py is one of four modules that main.py adds to sys.path by
  DIRECTORY (main.py:37-39) rather than importing as a package. The same applies
  to satellite_emulator, recovery_agent and the models/ modules. This works, but
  module identity then depends on import order: `from pipeline import
  run_pipeline` at main.py:993 resolves to the root pipeline.py MODULE at root,
  but would resolve to the backend/pipeline/ PACKAGE if the working directory
  were backend/. Document the required working directory in README, or convert
  to explicit package imports.

ACCEPTANCE: a contact-window calculation completes in < 1 s; a malformed TLE
raises a clear error instead of propagating garbage.
```

---

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
    itself verify signatures (the ground segment does), what that would mean on
    real hardware, and the mock-signing path from Prompt 4.0 if it was retained

ACCEPTANCE: every mitigation named in the document points to a specific file and
function, or is explicitly listed as future work.
```

---

## Sequence & expected score

| Phase | Effort | Score after |
|---|---|---|
| 0 — boot & render | 4 h | 6.8 |
| 1 — dataset | 1 d | 7.4 |
| 2 — ML correctness | 6 h | 8.2 |
| 3 — recovery & emulator | 1 d | 8.6 |
| 4 — security | 1.5 d | 9.0 |
| 5 — claims | 4 h | 9.1 |
| 6 — frontend fixes | 6 h | 9.2 |
| 7 — tests & CI | 1 d | 9.4 |
| 8 — reproducibility & proof | 1 d | 9.5 |

**Total ≈ 7–8 working days**, spread over ~2 weeks allowing for training runs
and debugging.

### Dependency order — these cannot be reordered

```
0.0 ──> 0.1 ──> 6.x            (no tsc, no frontend work, until the app builds)
0.2 ──> everything backend
0.3 ──> 4.0 ──> 4.1, 3.2       (no runnable CY-1 = no testable crypto or fallback)
0.4 ──> 7.3                    (guard fails on existing drift)
1.1 ──> 2.1 ──> 2.2, 2.3       (no leak-free splits without a real time axis)
2.3 ──> 6.1 bug B              (503 storm has to stop before the timer matters)
6.0 ──> 6.3                    (fix the envelope once, in one socket)
```

### If you only have three days

**Phase 0 (all of it), Phase 1, Phase 2, Phase 5.**

That fixes the invalid evaluation and the false claims — the two things a
careful reviewer finds first — and lands around 8.2. Phase 0 is not optional
triage: without 0.0 the frontend never renders, and without 0.3 and 4.0 the
security demo cannot succeed on stage.

**The single highest-value hour in the whole list** is Prompt 0.0: one character
in `index.html`, plus `npm install`. Everything in Phase 6, and every visual
claim in the README, depends on it.
