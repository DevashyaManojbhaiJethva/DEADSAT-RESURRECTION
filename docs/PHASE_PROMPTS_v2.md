# DeadSat Resurrection — Phase-wise Prompt List v2 (6.8 → 9.5)

Revision of `docs/PHASE_PROMPTS.md`, reconciled against the wiring/crash audit in
`docs/WIRING_AUDIT_2026-08-15.md`.

**What changed:**

- **7 new prompts** for verified defects that had no prompt at all — four of them
  are hard blockers that stop the original Phase 0 on its first step.
- **4 amended prompts** where the original text asserts something the code
  contradicts.
- **3 constraint conflicts** surfaced that need an owner decision before Phase 2
  and Phase 7 can be completed as written.

Markers: **[NEW]** = not in v1 · **[AMENDED]** = corrected v1 prompt ·
unmarked = unchanged from v1.

---

## Read this before Phase 0 — three constraint conflicts

The global constraints freeze the folder structure and require
`test_integration.py` to keep passing. Three tasks in the list cannot be done
under those rules. Decide now, not mid-phase.

| # | Conflict | Where it bites | Options |
|---|---|---|---|
| C1 | `train_classifier.py:78` writes to `ROOT / "model_artifacts"` — a **new folder**. It does not exist yet, so AI-1 has never been trained on this machine. | Phase 2, and F2 in the audit | (a) Grant an exception for `model_artifacts/` — recommended, it is a build output, not source. (b) Run with `--out_dir` pointed at an existing dir, e.g. `models/`. |
| C2 | CI needs `.github/workflows/` — a new folder. v1 Prompt 7.2 already flags this. | Phase 7 | (a) Grant the exception. (b) Root-level `pre_commit.py` runner instead. |
| C3 | Acceptance says "109+ passed, 0 failed" but `test_integration.py` imports FastAPI and the emulator. Confirm the current baseline number **before** Phase 0 changes anything, and record it. Do not treat 109 as verified — it has not been re-run since the wiring pass. | Every phase | Run it once, write the real number into the constraints block. |

Also correct in the global constraints block: the mirror map says
`models/ <-> backend/pipeline/`. That is right, but note **`ml/` also exists** at
root as a compatibility shim (`ml/classifier_inference.py`, `ml/__init__.py`
re-export from `models/`). It has no `backend/` counterpart and needs none —
just don't add logic to it.

---

# PHASE 0 — Get the system to boot and render at all

*~2 hours. v1's Phase 0 assumed this already worked. It does not. Nothing below
Phase 0 is testable until 0.0–0.3 pass.*

### Prompt 0.0 [NEW] — The frontend serves a blank page

```
VERIFIED BLOCKER: frontend/index.html line 12 is:

    <script type="module" src="/src/main.tsx"></script>

There is no frontend/src/ directory. The entry file is frontend/main.tsx.
Vite resolves module paths from the project root, gets a 404, and renders an
empty <div id="root"></div>. The dashboard has never displayed anything.

SECOND BLOCKER: frontend/node_modules is a half-finished install — 198 packages
present, but no `vite`, no `typescript`, and no node_modules/.bin/ directory.
`npm run dev`, `npm run build` and `npm run lint` all fail before doing anything.

TASK:
  1. frontend/index.html — change the src to "/main.tsx".
  2. cd frontend && rm -rf node_modules package-lock.json && npm install
  3. Confirm `npm run dev` serves the app and the React tree mounts.

CONSTRAINT: FIX-ONLY. One character in index.html. Do not create a src/ folder
and move files into it — folder structure is frozen, and the one-line path fix
is the correct minimal change.

ACCEPTANCE: `npm run dev` serves on :3000 and the LandingPage renders. Browser
console shows no module-resolution errors.
```

### Prompt 0.1 [AMENDED] — Type-check the frontend

Unchanged from v1, with two corrections to the preamble:

```
AMENDMENT 1: the reason npm install could not reach the registry is
environmental, but the practical state is worse than "unverified" — see Prompt
0.0. Run 0.0 first; tsc cannot run at all until typescript is installed.

AMENDMENT 2: add frontend/index.html and frontend/main.tsx to the file list.
main.tsx imports './App.tsx' with an explicit .tsx extension, which is only
legal because tsconfig sets allowImportingTsExtensions. Confirm tsc accepts it.

Everything else in v1 Prompt 0.1 stands: run `npm run lint` (tsc --noEmit), fix
every error with the smallest possible change, no `any` or `@ts-ignore` without
a written justification, no redesign.
```

### Prompt 0.2 [AMENDED] — Confirm both servers actually boot

```
AMENDMENT — v1 says backend/main.py "is fixed, but has never been run." The
missing-import problem is fixed. A different one is not:

VERIFIED: backend/main.py:48 does
    from crypto_routes import router as crypto_router, startup_crypto, limiter
and backend/main.py:60-61 imports slowapi. crypto_routes pulls in `oqs`, `redis`
and `nacl`.

NONE of `slowapi`, `redis`, `pynacl`, `liboqs-python` appear in requirements.txt
OR backend/requirements.txt. `import backend.main` therefore fails with
ModuleNotFoundError before the app object is constructed. The backend tree still
cannot start.

Root main.py is unaffected — it never imports the crypto package (see Prompt
4.0, which is a problem in its own right).

TASK:
  1. Add slowapi, redis, pynacl to backend/requirements.txt. For liboqs-python,
     note that backend/crypto/mock_oqs_nacl.py exists as a shim — determine
     whether real liboqs is required or the shim is the intended path, and
     document the answer in backend/crypto/README.md.
  2. Then run the v1 checks:
       pip install -r requirements.txt
       python -c "import main"
       python -c "import sys; sys.path.insert(0,'backend'); import backend.main"
       uvicorn main:app --port 8000
       curl localhost:8000/health
       curl localhost:8000/system/links
       curl localhost:8000/system/config

ALSO REPORT (do not fix yet, it is Prompt 5.2): both main.py files place
`if __name__ == "__main__": uvicorn.run("main:app")` mid-file — main.py:688 with
~340 more lines of route definitions after it, backend/main.py:510 likewise.
This works only because uvicorn re-imports the module under a different name;
running `python main.py` directly executes module-level side effects twice and
constructs two SatelliteEmulator instances.

ACCEPTANCE: both trees import without error; /system/links returns JSON listing
emulator, ai1_classifier, ai2_agent, crypto, rf_station, websocket_clients.
Expect ai1_classifier and crypto to report DOWN at this stage — that is correct
and is addressed in Phases 1-2 and 0.3.
```

### Prompt 0.3 [NEW] — Make CY-1 startable, or the whole security thesis is untestable

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
     crypto/verify.py, matching backend/crypto/ exactly. This resolves the only
     remaining root<->backend drift in those two files.
  3. Verify: python crypto/crypto_routes.py  serves on :8001
     curl localhost:8001/health
  4. Then re-run curl localhost:8000/system/links and confirm crypto flips to
     connected.

CONSTRAINT: the shim must make it OBVIOUS when mock crypto is in use. If
mock_oqs_nacl is active, /health must say so, and it must be impossible to
present a mock-signed command as verified (see Prompt 4.0).

ACCEPTANCE: CY-1 starts from the root tree; /system/links reports crypto
connected; docs/WIRING.md's example JSON matches reality.
```

### Prompt 0.4 [NEW] — Reconcile root ↔ backend drift before the guard is written

```
CONTEXT: v1 Prompt 7.3 adds test_backend_sync.py asserting byte-identical pairs.
Written today it fails immediately — the trees have ALREADY drifted:

  MEASURED (diff line counts):
    real_data_fetcher.py          <-> backend/real_data_fetcher.py          22 lines
    models/classifier_inference.py <-> backend/pipeline/classifier_inference.py  6 lines
    crypto/sign.py                <-> backend/crypto/sign.py                 1 line (the shim)
    crypto/verify.py              <-> backend/crypto/verify.py               1 line (the shim)

  IN SYNC (verified identical):
    agents/recovery_agent.py, agents/procedure_library.json,
    emulator/satellite_emulator.py, satellite_catalog.py, config.py,
    requirements.txt

TASK: For each drifted pair, determine which side is correct — do not blindly
copy one over the other, read the diff and decide. Then reconcile. Prompt 0.3
resolves the two crypto pairs as a side effect.

Report, for each pair, which side won and why. This is the last moment where the
drift is small enough to reason about.

ACCEPTANCE: all pairs in the v1 Prompt 7.3 list are byte-identical (ignoring line
endings) before any Phase 1 work begins.
```

### Prompt 0.5 [NEW] — CORS: why half the dashboard will be blank on the demo LAN

```
VERIFIED PROBLEM — this will surface the moment the frontend renders (Prompt 0.0):

  frontend/package.json dev script:  vite --port=3000 --host=0.0.0.0
  config.py:139 CORS default:  http://localhost:3000, http://127.0.0.1:3000,
                               http://localhost:5173

On the two-Pi demo the operator opens http://<PI1-or-laptop-LAN-IP>:3000. That
origin is not in the allow-list.

WebSockets are exempt from CORS. So telemetry streams fine and the header badge
reads "LIVE TM" and shows a healthy link count — while EVERY REST panel fails
silently inside its .catch(): TLE/orbital elements, catalog, crypto status,
ledger, alerts, pipeline status, /system/links detail, RF spectrum.

This is the exact failure mode most likely to appear for the first time in front
of judges, and it looks like a frontend bug when it is a config default.

TASK:
  1. Document it prominently in docs/WIRING.md's setup section and in
     .env.example: DEADSAT_CORS_ORIGINS must list the operator's real origin.
  2. Add a startup warning in config.print_banner() when API_HOST is 0.0.0.0
     (i.e. LAN-facing) but every CORS origin is a loopback address — that
     combination is always a misconfiguration.
  3. Do NOT "fix" it by setting allow_origins=["*"]. The API exposes fault
     injection, recovery and reset.

ACCEPTANCE: starting the API bound to 0.0.0.0 with loopback-only CORS prints a
visible warning naming the variable to set.
```

---

# PHASE 1 — Dataset foundation

*Unchanged from v1. Prompt 1.1 stands as written.*

One addition to its acceptance criteria:

```
ADDITIONAL ACCEPTANCE for Prompt 1.1: generate_dataset.py must be runnable with
no network access. The three input CSVs are local; sgp4 propagation is local.
If any code path reaches for CelesTrak or N2YO at generation time, gate it behind
an explicit --refresh flag that defaults off. Dataset generation must be
reproducible offline, or Phase 8.1's reproducibility claim cannot hold.
```

---

# PHASE 2 — ML pipeline correctness

*Prompts 2.1 and 2.2 unchanged from v1.*

### Prompt 2.3 [NEW] — Ship the artifacts, and make their absence honest

```
VERIFIED: models/classifier_inference.py:138-145 requires
  model_artifacts/transformer_encoder.pt
  model_artifacts/isolation_forest.pkl
  model_artifacts/scaler.pkl

There is no model_artifacts/ directory and no .pt/.pkl/.joblib file anywhere in
the repository. AI-1 has never been trained on this checkout.

MEASURED CONSEQUENCES IN THE RUNNING SYSTEM:
  /system/links            -> ai1_classifier permanently DOWN; header link count
                              can never reach n/n
  /pipeline/status         -> artifacts_ready: false
  /pipeline/classify       -> HTTP 503, always
  AiDiagnostics "recalibrate" -> "classification failed — 503" every time
                                 (AiDiagnostics.tsx:78)
  OperatorControlPanel     -> accuracy and truePos pinned at 0, re-polled every
                              15 s forever (OperatorControlPanel.tsx:141-152)
  /pipeline/run without skip_classifier -> fails

TASK (after 2.1 and 2.2):
  1. Resolve conflict C1 (the model_artifacts/ folder) with the project owner.
  2. Run train_classifier.py and produce the three artifacts.
  3. Decide and document whether artifacts are committed to the repo or built on
     first run. If built: add the command to README setup and make
     /pipeline/status's `hint` field say exactly what to run — it already does,
     confirm the frontend surfaces it (that is Prompt 6.2).
  4. Add a .gitignore entry if they are build outputs.

ACCEPTANCE: from a clean checkout, a documented command produces artifacts and
/system/links reports ai1_classifier connected.
```

---

# PHASE 3 — Recovery & emulator correctness

*Prompts 3.1, 3.2, 3.3 unchanged from v1. All three are confirmed real.*

Note for 3.2: the fallback path being unreachable is now doubly true — see
Prompt 4.0, where the verification gate fails every command before the fallback
logic is ever consulted. Fix 4.0 first, or the fallback test in 3.2's acceptance
criteria cannot pass.

---

# PHASE 4 — Security

### Prompt 4.0 [NEW] — Every recovery currently fails, and the real crypto is dead code

*Do this before 4.1 and 4.2. It is the single largest functional gap in the project.*

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
boots is the mock one. Every claim in README about post-quantum signing is,
on the root tree, describing unreachable code.

PROBLEM B — with CY-1 down, recovery is guaranteed to fail. Traced end to end:

  1. config.py:151  REQUIRE_COMMAND_VERIFICATION = True   (default)
  2. config.py:158  ALLOW_MOCK_SIGNING = False            (default)
  3. main.py:447-458  /crypto/sign catches the CY-1 connection failure and
     returns a fabricated "MOCK_ML_DSA_..." signature with "mock": true
  4. agents/recovery_agent.py:301  carries `mock` onto the signed command
  5. agents/recovery_agent.py:363  if cmd.get("mock"): return (False, "MOCK_SIGNATURE")
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
  2. /crypto/sign must NOT fabricate a signature when CY-1 is unreachable. Return
     503 with a clear reason. A mock signature that is immediately rejected
     downstream is worse than an honest failure — it burns a recovery attempt and
     produces a misleading log line.
  3. If a mock path is kept for bench demos, it must be opt-in via
     DEADSAT_ALLOW_MOCK_SIGNING=1 (the flag already exists and already defaults
     off — honour it at the SIGNING endpoint, not only in the agent), and the
     response and the UI must both say MOCK in plain language.
  4. After 0.3 gives you a runnable CY-1, prove the full path: inject -> classify
     -> sign -> verify -> uplink -> recovery success.

ACCEPTANCE:
  - with CY-1 running: a recovery completes and /crypto/ledger has a new entry
    signed by the real hybrid path, not a mock
  - with CY-1 down and DEADSAT_ALLOW_MOCK_SIGNING unset: /crypto/sign returns
    503, the agent reports SIGNING_UNAVAILABLE (not MOCK_SIGNATURE), and the UI
    says the crypto service is offline
  - root main.py and backend/main.py expose the same /crypto/* route set
```

### Prompt 4.1 — Fix nonce replay protection

*Unchanged from v1. Note it can only be tested after Prompt 0.3 makes CY-1 runnable.*

### Prompt 4.2 [AMENDED] — Authenticate WebSockets and reconcile the TTL

```
v1 text stands. Add one more sub-bug:

BUG D [NEW] — backend/main.py registers /crypto/check-command TWICE. The crypto
router is mounted at line 211 and defines it; @app.post("/crypto/check-command")
at line 413 defines it again. FastAPI serves the first match, so the 60-line
handler at 413 is unreachable dead code. Two divergent implementations of a
security endpoint, one of which never runs, is exactly the kind of thing that
gets "fixed" later by someone editing the dead one.

FIX: delete one. Given Prompt 4.0 makes the router canonical, delete the @app
handler and mirror the result to root main.py.
```

---

# PHASE 5 — Claims reconciliation

### Prompt 5.1 [AMENDED] — Make every claim verifiable

```
v1's five items all confirmed. Add these:

  6. README.md:467-468 setup instructions say:
         cd frontend/dashboard && npm install && npm run dev
         cd frontend/operator  && npm install && npm run dev
     NEITHER DIRECTORY EXISTS. There is one frontend/. Anyone following the
     README verbatim cannot start the project. Fix the paths and add the
     VITE_API_BASE step from frontend/.env.example.

  7. docs/FIX_PRIORITY.md states SatelliteDashboard.tsx is "1760 lines, 30
     Math.random() calls, still simulated." Measured today: 1858 lines, 5
     Math.random() calls, all five in the decorative starfield geometry
     (lines 698-706). The component IS wired to the backend. The status table at
     the top of that file is stale and understates the work already done —
     update it, or a reviewer reads it and assumes the dashboard is fake.

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

ACCEPTANCE unchanged: grep for "fixed", "improvement", "hardened", "secure",
"verified" — every remaining instance points to a test or a line of code.
```

### Prompt 5.2 [NEW] — Structural fragility that has no functional symptom yet

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

# PHASE 6 — Frontend (FIX-ONLY)

### Prompt 6.0 [NEW] — The WebSocket history envelope is parsed as a telemetry frame

*Do this before 6.1-6.4. It is the most visible wrong-data bug in the UI.*

```
VERIFIED BUG. main.py:653-658 — the FIRST message on /ws/telemetry is not a
frame, it is an envelope:

    {"type": "history", "frames": [...up to 60 frames...], "count": 60}

api.ts subscribe() JSON.parses every message and passes it straight to the frame
callback. Nothing checks `type`. Three components receive an object with no
telemetry fields on every connect AND every reconnect:

  SatelliteDashboard.tsx:477
      Math.round(undefined) -> NaN -> the OBC register pane renders
      literally  SP: 0x1FFF00NaN
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

### Prompt 6.1 — Cold-start blank panel and polling waste

*Unchanged from v1. Both bugs confirmed. Note Prompt 2.3 removes the underlying
cause of Bug B's 503 storm, but the timer should still go.*

### Prompt 6.2 [AMENDED] — Render the data that is already being fetched

```
v1 text stands — six fetched-but-discarded values, ledger most important.

ONE ADDITION: with AI-1 artifacts absent (until Prompt 2.3) and CY-1 down (until
Prompt 0.3), these panels are the only place a user can learn WHY the system is
degraded. /pipeline/status already returns a `hint` field containing the exact
command to run ("Train with: python train_classifier.py") and it is currently
thrown away. /crypto/status returns a `message` field saying "CY-1 not running —
signatures cannot be verified" and that is thrown away too.

Surface both. A dashboard that says "AI-1 artifacts missing — run
train_classifier.py" is worth more than one that silently shows 0.00%.
```

### Prompt 6.3 — Collapse duplicate WebSocket connections

*Unchanged from v1. Confirmed: 3 telemetry + 3 events subscriptions. Do this
AFTER Prompt 6.0 — fixing the envelope bug in one shared socket is simpler than
in three.*

### Prompt 6.4 — Reconcile the 5-fault UI with the 4-fault emulator

*Unchanged from v1. Confirmed at api.ts UI_FAULT_TO_BACKEND, including the
in-code admission: "The emulator has no dedicated battery fault;
firmware_corruption is the closest available analogue."*

---

# PHASE 7 — Tests & CI

### Prompt 7.1 [AMENDED] — Unit tests

```
v1 list stands. Add these cases, each covering a defect found in the audit:

  - api.ts message routing: a {type:'history'} payload must NOT reach the frame
    handler (Prompt 6.0). Testable without a browser by exporting the message
    dispatch as a pure function.
  - /crypto/sign with CY-1 unreachable and DEADSAT_ALLOW_MOCK_SIGNING unset
    returns 503, not a MOCK_ signature (Prompt 4.0).
  - root main.py and backend/main.py expose identical /crypto/* route sets
    (Prompt 4.0 / 7.3).
  - config: API_HOST=0.0.0.0 with loopback-only CORS_ORIGINS triggers the
    warning (Prompt 0.5).
  - emulator.get_latest_frame() returns {} before the first tick — assert every
    consumer handles it. /telemetry currently does frame["overall_health"] = ...
    on that empty dict and returns a one-key object; confirm that is intentional
    and that the frontend tolerates it.
```

### Prompt 7.2 — Continuous integration

*Unchanged from v1. Resolve conflict C2 first. Add `npm ci` to the frontend job —
the current node_modules state (Prompt 0.0) is exactly what CI exists to catch.*

### Prompt 7.3 [AMENDED] — backend/ drift guard

```
v1 text stands. Two corrections:

  1. Run Prompt 0.4 FIRST. Written today this test fails on four pairs, because
     the trees have already drifted again — the exact scenario the test exists to
     prevent, which rather makes the case for it.

  2. Add to the byte-identical list (both exist and are currently in sync):
       emulator/__init__.py   <-> backend/emulator/__init__.py
       agents/__init__.py     <-> backend/agents/__init__.py
     And add to the pairs to reconcile:
       real_data_fetcher.py   <-> backend/real_data_fetcher.py   (22 lines drifted)
       crypto/*.py            <-> backend/crypto/*.py            (shim + __init__)

     Note root crypto/ is missing __init__.py and mock_oqs_nacl.py entirely —
     Prompt 0.3 adds them. Until then the crypto pair check cannot pass.

  3. The route-set comparison for main.py <-> backend/main.py must account for
     the crypto router prefix (/crypto) and for backend/main.py's duplicate
     /crypto/check-command registration (Prompt 4.2 Bug D) — after that fix the
     sets should match exactly.
```

---

# PHASE 8 — Reproducibility & deployment proof

*Prompts 8.1, 8.2, 8.3 unchanged from v1.*

One addition to 8.2's list:

```
ADDITIONAL for Prompt 8.2: contact_calculator.py is one of the four files that
main.py adds to sys.path by directory (main.py:37-39) rather than importing as a
package. The same is true of satellite_emulator, recovery_agent and the models/
modules. This works, but it means module identity depends on import order, and
`from pipeline import run_pipeline` at main.py:993 resolves to the root
pipeline.py at root but would resolve to the backend/pipeline/ PACKAGE if the
working directory were backend/. Document the required working directory in
README, or convert to explicit package imports. Do not restructure folders.
```

---

## Revised sequence & effort

| Phase | v1 effort | v2 effort | Score after | Change |
|---|---|---|---|---|
| **0 — boot & render** | 1 h | **4 h** | 6.8 | +3 h: 4 new blocker prompts |
| 1 — dataset | 1 d | 1 d | 7.4 | — |
| 2 — ML correctness | 4 h | **6 h** | 8.2 | +2 h: artifacts (2.3) |
| 3 — recovery & emulator | 1 d | 1 d | 8.6 | — |
| 4 — security | 1 d | **1.5 d** | 9.0 | +4 h: crypto wiring (4.0) |
| 5 — claims | 2 h | **4 h** | 9.1 | +2 h: 5.2 |
| 6 — frontend fixes | 4 h | **6 h** | 9.2 | +2 h: 6.0 |
| 7 — tests & CI | 1 d | 1 d | 9.4 | — |
| 8 — reproducibility | 1 d | 1 d | 9.5 | — |

**Total ≈ 7–8 working days.**

**If you only have three days:** Phase 0 (all of it), Phase 1, Phase 2, Phase 5.
v1 recommended the same set minus Phase 0's new prompts — but without 0.0 the
frontend never renders, and without 0.3 and 4.0 the security demo cannot succeed
on stage. Phase 0 is no longer optional triage; it is the difference between a
system that runs and one that does not.

**The single highest-value hour in the whole list** is Prompt 0.0: one character
in `index.html`, plus `npm install`. Everything else in Phase 6, and every visual
claim in the README, depends on it.
