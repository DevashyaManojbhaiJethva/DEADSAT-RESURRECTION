# DeadSat Resurrection — Priority-Ordered Fix List

Companion to `docs/BUG_AUDIT.md`.

## Current status — updated after the wiring pass (2026-08-11)

| | Critical | High | Medium | Low | Total |
|---|---|---|---|---|---|
| Found | 15 | 23 | 32 | 16 | **86** |
| Fixed | 5 | 4 | 6 | 0 | **15** |
| Partly fixed | 1 | – | – | – | **1** |
| New (introduced) | – | – | – | 2 | **2** |
| **Remaining** | **10** | **19** | **26** | **18** | **73** |

**Fixed (15)** — §2.1 fault-key mismatch · §2.2 torch hard-import · §2.3 emulator
naming · §3.2 mock signing · §3.3 + §7.1 verification gate · §3.5 uplink always
allowed · §3.6 discarded return · §3.13 mkdir at import · §3.14 log collisions ·
§3.15 hardcoded hosts · §6.1 CORS + auth · §6.3 broadcaster death · §6.6 empty
frame · §8.3 API base config.

**Partly fixed (1)** — §8.1 frontend connection. `App.tsx`, `TelemetryConsole`
and `OperatorControlPanel` are live. `SatelliteDashboard.tsx`, `AiDiagnostics`,
`SecurityConsole` and `OperatorPanel` still need work. See P0-11b below.

> **Corrected 2026-08-15.** This paragraph previously read *"`SatelliteDashboard.tsx`
> (1760 lines, 30 `Math.random()` calls) … are still simulated"*. Measured today:
> **1858 lines, 5 `Math.random()` calls**, and all five are in the decorative
> starfield geometry (lines ~698-706), not in telemetry. The component subscribes
> to `/ws/telemetry` and `/ws/events` and pulls `/catalog/satellite/{id}`,
> `/rf/spectrum`, `/system/links`, `/pipeline/status` and `/crypto/status`.
> `AiDiagnostics` and `SecurityConsole` are also wired to the backend.
>
> The remaining §8.1 work is narrower than "still simulated" implies: values are
> fetched but not all are rendered (see Prompt 6.2 — the crypto ledger in
> particular is retrieved and discarded), and the ground track in
> `api.ts:frameToTelemetryState()` is still fabricated.
>
> Understating progress is the same class of error as overstating it. A reviewer
> reading the old text would conclude the dashboard is fake and stop looking.

**New, introduced by the wiring pass (2, both Low)** — see P3-17 and P3-18.

### Ground rules

> **Folder structure must not change.** No moving, renaming, merging or deleting
> existing files or directories. New files may only be added *inside* directories that
> already exist — each one is flagged **[NEW FILE]** below. No new folders.

> **Every fix must be applied twice.** Because `backend/` stays as a parallel copy, each
> change to a shared module must be mirrored:
> `emulator/` ↔ `backend/emulator/`, `agents/` ↔ `backend/agents/`,
> `models/` ↔ `backend/pipeline/`, `crypto/` ↔ `backend/crypto/`,
> `main.py` ↔ `backend/main.py`, `satellite_catalog.py` ↔ `backend/satellite_catalog.py`.
> Item **P1-14** adds an automated guard so drift is caught.

**Effort key:** XS < 15 min · S < 1 h · M ≈ half day · L ≈ 1–2 days · XL ≈ 3+ days

---

## P0 — Blocks the project's core claims (14 items)

Do these in the order listed; several have hard dependencies.

### P0-1 · Regenerate the dataset with a real time axis
**§1.3 · CRITICAL · XL · blocks P0-2, P0-3**
Files: **[NEW FILE]** `generate_dataset.py` (repo root, alongside `train_classifier.py`); writes to existing `data/`

The dataset is one row per satellite at a single instant, so 3 of 4 fault rules can
never fire (`REV_DELTA` and `ecc_delta` are 0 for 100% of rows → 807 SOFTWARE_BUG,
0 SEU). Propagate each GP row forward ~20 epochs at ~90-min intervals using `sgp4`
(already a dependency) plus `satellite_catalog.build_tle_from_gp()`, then inject faults
as perturbations to the *series*: SEU = one-epoch eccentricity step that reverts;
SOFTWARE_BUG = frozen `REV_AT_EPOCH`; FIRMWARE_CORRUPTION = ramped `BSTAR`/`MEAN_MOTION_DOT`;
COMMAND_INJECTION = withheld epochs so `TLE_AGE_HOURS` passes 72.
Also dedup the 141 exact `(NORAD_CAT_ID, EPOCH)` duplicates caused by the three CSVs overlapping.

### P0-2 · Rebuild the training split and windowing
**§1.1 §1.2 §1.5 §1.6 · CRITICAL/HIGH · L · depends on P0-1**
File: `models/satellite_fault_classifier_V2.py`

Four leaks compound into meaningless accuracy numbers:
- `train_test_split(..., shuffle=True)` runs *before* windowing, so every 8-step
  "sequence" is 8 unrelated satellites → split by satellite with `GroupShuffleSplit`.
- `OrbitalSequenceDataset` slides across satellite boundaries → build windows *within*
  each `NORAD_CAT_ID`, sorted by `EPOCH`.
- `augment_fault_samples()` oversamples with replacement *before* the split → augment
  the training split only.
- The `StandardScaler` is fitted on the full dataset in `train_isolation_forest()` →
  fit on train only, then `transform()` val/test.

### P0-3 · Retire the contradictory synthetic SEU generator
**§1.4 · CRITICAL · S · depends on P0-1**
File: `models/satellite_fault_classifier_V2.py`

`assign_fault_labels()` defines SEU as an eccentricity *jump*; `_generate_synthetic_class()`
defines it as a constant `ECCENTRICITY=0.05`. Since all SEU data is synthetic, the model
learns the wrong concept. Once P0-1 supplies real SEU sequences, delete the call path
(keep the function, mark deprecated — no file removal).

### P0-4 · Remove the mock-signing fallback
**§3.2 · CRITICAL · S**
File: `agents/recovery_agent.py` → `node_request_signing`

Any exception from the signing service currently yields `MOCK_SIG_...` marked
`signed: True`, then prints "CY-1 signing SUCCESS". Taking the crypto service offline
bypasses signing entirely. Set `signing_success = False` and let `route_after_signing`
reach its (currently dead) `"fallback"` branch. Gate any mock behind an explicit
`DEADSAT_ALLOW_MOCK_SIGNING=1` env var that logs loudly.

### P0-5 · Verify signatures before applying a procedure
**§3.3 §7.1 · CRITICAL · M · depends on P0-4**
Files: `agents/recovery_agent.py` → `node_uplink_commands`; `emulator/satellite_emulator.py` → `apply_recovery`

`verify_command()` is never called outside `crypto/`. Add a verification call against
`/crypto/verify` before `emulator.apply_recovery()`, and have `apply_recovery()` accept
and check a verification token so it cannot be driven by an unverified caller. This is
the project's central claim — it currently has no enforcement anywhere.

### P0-6 · Fix nonce replay protection
**§7.2 §7.3 §7.4 · CRITICAL/HIGH · M**
File: `crypto/nonce.py`, `crypto/crypto_routes.py`

- The comment claims `SET NX — atomic` but the code does a separate `get` then `set` —
  a TOCTOU race. Use `redis.set(key, nonce, nx=True, ex=...)` and treat `False` as replay.
- If `hmac.compare_digest` returns `False`, control falls through to the unconditional
  `set`, *overwriting* the nonce and permitting the replay it should block.
- `use_nonce()` is called at **sign** time, not verify time — replay protection is on
  the wrong side of the trust boundary. Move it into `/crypto/verify`.

### P0-7 · Make `apply_recovery()` fault-aware
**§4.1 · CRITICAL · M · blocks P0-8**
File: `emulator/satellite_emulator.py`

Any recognised procedure name clears `fault_injected` and prints
"Recovery SUCCESS — satellite nominal", even when it does not address the active fault
(verified: `LOCKDOWN_REGEN_v1` on an active SEU returns `True` while ADCS stays in
`fault`). Map each procedure to the fault types it can remedy; return `False` and leave
state untouched otherwise.

### P0-8 · Make `success_criteria` authoritative
**§3.1 · CRITICAL · S · depends on P0-7**
File: `agents/recovery_agent.py` → `node_monitor_recovery`

`if passed or health == "nominal"` lets a nominal health reading override failed
criteria, so recovery is declared successful on the first poll and the fallback path is
unreachable. Drop the `or health == "nominal"` clause.

### P0-9 · Fix the `min_confidence` skip path
**§3.4 · CRITICAL · S**
File: `agents/recovery_agent.py` → `node_select_procedure`

On a confidence skip the node bumps `priority_index` and returns without setting
`selected_procedure`, `error` or `next_step`. `route_after_select` then routes to
`generate_commands`, which reads a **stale or missing** `selected_procedure` — silently
uplinking the wrong procedure. Add a `"reselect"` route back into `select_procedure`.
Live trigger: `software_bug` confidence in **[0.70, 0.80)**.

### P0-10 · Add authentication and tighten CORS
**§6.1 · CRITICAL · M**
File: `main.py`

`allow_origins=["*"]` plus unauthenticated `/fault/inject`, `/recovery/trigger`,
`/pipeline/run`, `/reset`, `/seed`, `/crypto/sign` means any web page the operator
visits can command the ground station. Add an API-key or bearer dependency on all
mutating routes; restrict origins to the frontend's actual host.

### P0-11 · Connect the frontend to the backend
**§8.1 · CRITICAL · L**
Files: `frontend/App.tsx`, `frontend/components/*.tsx`

The UI makes zero calls to the API. All telemetry is `Math.random()` in a `setInterval`,
and the "WebSocket client OP-HQ_DELHI handshaking authorized" line is a hardcoded log
*string*, not a connection. Replace the simulated state with a `/ws/telemetry`
subscription and wire the operator controls to `/fault/inject`, `/recovery/trigger` and
`/pipeline/run`. Until this is done, no screenshot demonstrates the Python system.

### P0-11b · Wire the remaining dashboard components
**§8.1 (partial) · CRITICAL · L**
Files: `frontend/components/SatelliteDashboard.tsx`, `AiDiagnostics.tsx`, `SecurityConsole.tsx`, `OperatorPanel.tsx`

The wiring pass connected `App.tsx`, `TelemetryConsole` and
`OperatorControlPanel`. Four components remain self-contained simulations and
take no props:

| Component | Lines | `Math.random()` | Should consume |
|---|---|---|---|
| `SatelliteDashboard.tsx` | 1760 | 30 | `useDeadsat().frame` — it renders on the default tab |
| `AiDiagnostics.tsx` | 171 | 1 | `/pipeline/classify`, `/pipeline/status` |
| `SecurityConsole.tsx` | 165 | 0 | `/crypto/ledger`, `/crypto/alerts`, `/crypto/status` |
| `OperatorPanel.tsx` | 155 | 0 | `/telemetry/history`, `/contact` |

`SatelliteDashboard` matters most — it is half of the default view. The
plumbing it needs already exists: `useDeadsat()` exposes `frame`, and `api.ts`
covers every endpoint listed above.

### P0-12 · Add an API base URL setting
**§8.3 · MEDIUM · XS · blocks P0-11**
Files: `frontend/.env.example`, `frontend/vite.config.ts`

There is no `VITE_API_BASE`, so there is no way to point a build at a backend host.

### P0-13 · Clamp emulator power drift
**§4.3 · HIGH · S**
File: `emulator/satellite_emulator.py` → `_update_nominal_drift`

`solar_output_w`, `bus_voltage_v` and `reaction_wheel_rpm` are unbounded random walks.
Verified: `power_w` reaches **46.6** after ~2000 ticks (~33 min at the API's 1 s tick),
below the `power_w > 75` success criterion — a long-running demo fails on its own.
Clamp all three the way `battery_pct` already is.

### P0-14 · Bound fault-effect escalation
**§4.4 · HIGH · S**
File: `emulator/satellite_emulator.py` → `_apply_fault_effects`

Verified: `adcs_rate_deg_s` reaches **14.68 °/s** (nominal < 0.01) after ~150 ticks, and
`obc_error_count` grows without limit. Add per-fault ceilings.

---

## P1 — High (22 items)

| # | ID | Item | File | Effort |
|---|---|---|---|---|
| P1-1 | §1.7 | **No "healthy" output class.** `FAULT_LABELS` has 4 entries and all NORMAL rows are dropped, so a healthy satellite is always classified as faulty. Add a NORMAL class (`num_classes: 5`) and retain NORMAL rows. | `models/satellite_fault_classifier_V2.py` | M |
| P1-2 | §1.8 | **Isolation Forest gates nothing.** `anomaly_flag` is returned but no caller uses it to suppress a classification. Gate on it in the bridge. | `models/classifier_inference.py` | S |
| P1-3 | §2.4 | **No model versioning.** `meta.json` records no training-data hash, git SHA or schema version, so stale artifacts load silently. | `models/satellite_fault_classifier_V2.py`, `models/classifier_inference.py` | S |
| P1-4 | §3.5 | **Uplink always allowed.** `uplink_allowed = True` is set in all three branches including `# dev mode`. Contact scheduling has no effect. Gate behind an env flag. | `agents/recovery_agent.py` | S |
| P1-5 | §3.6 | **`apply_recovery()` return value discarded.** `success = ...` is never read; a rejected uplink proceeds to monitoring. | `agents/recovery_agent.py` | XS |
| P1-6 | §3.7 | **Missing telemetry key counts as PASS.** `if val is None: continue`. Live instance: `SAFE_MODE_HOLD` requires `beacon_active`, which the emulator never emits — either emit it or fail closed. | `agents/recovery_agent.py`, `emulator/satellite_emulator.py` | S |
| P1-7 | §3.8 | **`>=` / `<=` silently always fail.** `float(condition[1:])` on `"<= 0.01"` raises, then falls through to a string compare that returns `False`. | `agents/recovery_agent.py` → `_check_criteria` | XS |
| P1-8 | §3.9 | **JSON booleans raise uncaught `AttributeError`.** `condition.startswith()` on a bool is not caught by `except (ValueError, TypeError)`. | `agents/recovery_agent.py` → `_check_criteria` | XS |
| P1-9 | §4.2 | **`start()` not idempotent.** Verified: calling it twice leaves an orphaned ticking thread that is never joined. Guard on `self._running`. | `emulator/satellite_emulator.py` | XS |
| P1-10 | §5.1 | **Fallback TLE is from 2024** (epoch `24163`), two years stale against the project's 2026 timeline — every offline contact window is fiction. Despite the "Improvement 4" claim. | `emulator/contact_calculator.py` | XS |
| P1-11 | §5.2 | **8,640 SGP4 propagations per recovery.** `search_hours=24, step_seconds=10` runs synchronously inside the graph. Use a coarse scan then refine near the crossing. | `emulator/contact_calculator.py`, `agents/recovery_agent.py` | M |
| P1-12 | §5.3 | **`/contact` runs three propagation passes** where one would do. | `emulator/contact_calculator.py` → `get_contact_summary` | S |
| P1-13 | §6.2 | **Module-level emulator breaks multi-worker deploys.** Each `uvicorn --workers N` process gets its own diverging emulator. Document single-worker, or move construction into lifespan state. | `main.py` | S |
| P1-14 | §6.8 | **`backend/` drift guard.** Structure is frozen, so instead of collapsing the tree add a test asserting the mirrored files are byte-identical (ignoring line endings). | **[NEW FILE]** `test_backend_sync.py` (repo root) | S |
| P1-15 | §6.3 | **One WebSocket error kills telemetry for everyone.** `_telemetry_broadcaster` has no `try/except`; a single raise ends the task permanently. | `main.py` | XS |
| P1-16 | §7.5 | **`sys.exit(1)` inside a library function** kills the whole API process on an unsupported mechanism. Raise instead. | `crypto/verify.py` | XS |
| P1-17 | §8.2 | **Browser-side CelesTrak fetch will be CORS-blocked**, and hardcodes NORAD **44804** (unrelated to the project's 28654). Proxy it through the backend. | `frontend/components/SatelliteDashboard.tsx` | S |
| P1-18 | §9.1 | **Mixed CRLF/LF.** ~50 files show as modified purely from line endings, burying real changes. Add `.gitattributes` with `* text=auto`. | **[NEW FILE]** `.gitattributes` (repo root) | XS |
| P1-19 | §1.9 | **Isolation Forest trained on faulty rows**, and its reported anomaly rate is circular (`contamination=0.05` forces ~5% by construction). Fit on NORMAL only. | `models/satellite_fault_classifier_V2.py` | S |
| P1-20 | §7.9 | **120 s signature TTL vs a 24 h contact wait.** Any command genuinely held for a real window expires before transmission. | `crypto/sign.py`, `agents/recovery_agent.py` | S |
| P1-21 | §3.10 | **Attempt counter conflates skips with attempts**, so one confidence skip can exhaust the budget before every procedure is tried. | `agents/recovery_agent.py` | XS |
| P1-22 | §3.11 | **`timeout_s` treated as a poll count** and silently capped at 30 by `MAX_POLL_ATTEMPTS`; a `timeout_s: 90` procedure gets ~30 s. | `agents/recovery_agent.py` | XS |

---

## P2 — Medium (31 items)

| # | ID | Item | File | Effort |
|---|---|---|---|---|
| P2-1 | §1.10 | Class balance never achieved — SOFTWARE_BUG keeps 807 rows while others pad to 400 (`n_needed = max(0, ...)` returns 0). | `models/satellite_fault_classifier_V2.py` | XS |
| P2-2 | §1.11 | Augmentation uses the global NumPy RNG, not the seeded one — training is not reproducible. | same | XS |
| P2-3 | §1.12 | `_generate_synthetic_class` reseeds identically per class → correlated noise across all four classes. | same | XS |
| P2-4 | §3.12 | A new `ContactCalculator` is built per attempt, each triggering a fresh 5 s blocking CelesTrak fetch. Cache it. | `agents/recovery_agent.py` | S |
| P2-5 | §3.13 | `LOG_DIR.mkdir()` runs at import time and lacks `parents=True`. | `agents/recovery_agent.py` | XS |
| P2-6 | §3.14 | Recovery-log filenames collide at 1 s resolution — `pipeline.py --all` can overwrite. Add a counter or PID. | `agents/recovery_agent.py` | XS |
| P2-7 | §3.15 | `SIGNING_ENDPOINT` / `FASTAPI_BASE` hardcoded to `localhost:8000`; `satellite_id` hardcoded to `"DEADSAT-1"`. Move to env. | `agents/recovery_agent.py` | S |
| P2-8 | §4.5 | Documented "noise on top of fault effects" does not exist — verified telemetry **freezes** during a fault (`_update_nominal_drift` returns early). Either implement it or correct the claim. | `emulator/satellite_emulator.py` | S |
| P2-9 | §4.6 | `get_latest_frame()` is a shallow copy; nested `fault_detail` is shared by reference. | `emulator/satellite_emulator.py` | XS |
| P2-10 | §4.7 | `stop()` uses `join(timeout=3)` with no post-check; a thread with `tick_interval > 3` survives silently. | `emulator/satellite_emulator.py` | XS |
| P2-11 | §5.4 | `load_tle()`'s `False` return is ignored by every caller; failure is indistinguishable from "no window". | `agents/recovery_agent.py` | XS |
| P2-12 | §5.5 | Fetched TLE is never validated (line prefix, 69-char length, checksum). A CelesTrak error page yields garbage. | `emulator/contact_calculator.py` | S |
| P2-13 | §5.6 | Catalog keeps the **first** row per NORAD by file order, not the newest epoch — 142 rows discarded arbitrarily. | `satellite_catalog.py` | XS |
| P2-14 | §5.7 | Ground-station altitude documented as km but supplied in metres (`alt_m = 53.0`). Maths is right, docstring is wrong — a 1000× trap. | `emulator/contact_calculator.py` | XS |
| P2-15 | §6.4 | `ThreadPoolExecutor` replaces the default executor but is never `shutdown()`. | `main.py` | XS |
| P2-16 | §6.5 | `asyncio.get_event_loop()` inside a running loop is deprecated — use `get_running_loop()`. | `main.py` | XS |
| P2-17 | §6.6 | `/telemetry` returns a near-empty frame before the first tick. Add a readiness gate. | `main.py` | XS |
| P2-18 | §6.7 | `asyncio.create_task(...)` results are never retained — tasks can be GC'd mid-flight, and two recoveries can race on one emulator. | `main.py` | S |
| P2-19 | §7.6 | Documented timing-attack protection is not implemented — `compare_digest` is never called and `import hmac` is unused. The claim is false (PyNaCl's own verify is constant-time). | `crypto/verify.py` | XS |
| P2-20 | §7.7 | Hard Redis dependency — `NonceManager.__init__` re-raises on connection failure, so crypto routes cannot start without Redis. | `crypto/nonce.py` | S |
| P2-21 | §7.8 | `import oqs` unguarded in `crypto/verify.py` while `backend/crypto/` ships `mock_oqs_nacl.py` — the two trees fail differently. | `crypto/verify.py` | XS |
| P2-22 | §8.4 | `.env.example` requests `GEMINI_API_KEY`; `@google/genai` is a dependency; neither appears in any source file. Remove the prompt for an unused secret. | `frontend/.env.example`, `frontend/package.json` | XS |
| P2-23 | §9.2 | `.gitignore` misses `model_artifacts/`, `recovery_logs/`, `crypto/nonce_store.db`, `crypto/*.bin`, `node_modules/`. Two run-output JSONs are already committed. | `.gitignore` | XS |
| P2-24 | §9.3 | Binary blobs committed: `PCB&&CAD.zip` (68 KB), `docs/For a satellite fault.docx`. Structure is frozen, so leave in place — consider git-lfs. The `&&` also breaks naive shell globbing. | — | XS |
| P2-25 | §9.4 | Inconsistent pinning: `langgraph==1.2.4` exact, everything else `>=` with no ceiling, no lockfile. | `requirements.txt` | S |
| P2-26 | §1.13 | V1 and V2 classifiers coexist with incompatible contracts (`TELEMETRY_COLS` vs `FEATURE_COLS`) and nothing marks V1 deprecated. Add an in-file deprecation notice — do not delete. | `models/satellite_fault_classifier.py` | XS |
| P2-27 | §6.9 | No rate limiting on `/fault/inject`, compounding P0-14's escalation. | `main.py` | S |
| P2-28 | §3.16 | `if bat_cur and alt:` — a `battery_pct` of `0` is falsy, dropping the reasoning note exactly when the satellite is worst off. | `agents/recovery_agent.py` | XS |
| P2-29 | §3.17 | `_find_procedure_library()` returns `candidates[0]` when nothing exists, turning a clear "not found" into a confusing open error. | `agents/recovery_agent.py` | XS |
| P2-30 | §5.8 | `find_next_contact()` latches AOS at `now` if already in view, returning the in-progress pass as the "next" one. | `emulator/contact_calculator.py` | XS |
| P2-31 | §5.9 | `get_by_name()` returns the first substring match with no disambiguation. | `satellite_catalog.py` | XS |

---

## P3 — Low / hygiene (16 items)

| # | ID | Item | File | Effort |
|---|---|---|---|---|
| P3-1 | §1.14 | `MEAN_MOTION_DDOT` is 0 in 827/854 rows — a near-constant feature diluting the attention input. | `models/feature_spec.py` | XS |
| P3-2 | §3.18 | Redundant `from datetime import ...` inside `node_schedule_uplink`. | `agents/recovery_agent.py` | XS |
| P3-3 | §3.19 | `__main__` smoke test starts an emulator and never stops it. | `agents/recovery_agent.py` | XS |
| P3-4 | §4.8 | Startup message hardcodes "every 1s" regardless of `tick_interval` (pipeline uses 0.3 s, tests 0.05 s). | `emulator/satellite_emulator.py` | XS |
| P3-5 | §7.10 | Private keys are memory-only; every restart invalidates prior signatures and orphans ledger history. | `crypto/keygen.py` | M |
| P3-6 | §8.5 | `express` is a runtime dependency of a static SPA; the `clean` script deletes a `server.js` that does not exist. | `frontend/package.json` | XS |
| P3-7 | §8.6 | Package still named `react-example`. | `frontend/package.json` | XS |
| P3-8 | §8.7 | Mojibake in a comment: `Do not modifyâfile watching...` (UTF-8 em-dash read as Latin-1). | `frontend/vite.config.ts` | XS |
| P3-9 | §9.5 | `data/` and `backend/data/` duplicate ~1.3 MB of identical CSVs. Structure frozen — leave, but P1-14's guard should cover them. | — | XS |
| P3-10 | §9.6 | No CI runs `test_integration.py`, so none of the above regresses visibly. | **[NEW FILE]** `.github/workflows/` — *requires a new folder; skip while structure is frozen, or run the suite via a pre-commit hook instead* | S |
| P3-11 | — | `README.md` claims "13 telemetry features" for AI-1; V2 uses 11 orbital elements. | `README.md` | XS |
| P3-12 | — | `recovery_agent.py`'s header lists "all bugs fixed" including three claims contradicted by the code (§4.5, §5.1, §7.2). | `agents/recovery_agent.py` | XS |
| P3-13 | — | `pyrightconfig.json` present but the codebase has extensive untyped dicts; type checking is effectively off. | `pyrightconfig.json` | S |
| P3-14 | — | No `LICENSE` file despite a public README with contributors. | **[NEW FILE]** `LICENSE` (repo root) | XS |
| P3-15 | — | `docs/deadsat_postman_collection.json` predates the `/pipeline/*` routes added in the upgrade. | `docs/deadsat_postman_collection.json` | XS |
| P3-16 | — | `.gitignore` lacks `*.pyc` coverage for the `__pycache__` dirs created under `backend/` by the sync guard. | `.gitignore` | XS |
| P3-17 | NEW | **Duplicate `/ws/events` subscription.** `useDeadsat` and `OperatorControlPanel` each open their own connection. Harmless (the backend handles many clients) but wasteful — lift the events stream into the hook and pass it down. | `frontend/useDeadsat.ts`, `components/OperatorControlPanel.tsx` | XS |
| P3-18 | NEW | **Unused type imports** `TelemetryState` / `SystemLog` left in `App.tsx` after the hook took over that state. Non-fatal (`noUnusedLocals` is off) but dead code. | `frontend/App.tsx` | XS |

---

## Dependency chain (P0 only)

```
P0-1  regenerate dataset ──┬──▶ P0-2  fix split/windowing
                           └──▶ P0-3  retire synthetic SEU

P0-4  remove mock signing ─────▶ P0-5  verify before apply
P0-6  fix nonce replay

P0-7  fault-aware recovery ────▶ P0-8  criteria authoritative
P0-9  min_confidence skip

P0-12 API base URL ────────────▶ P0-11 wire frontend
P0-10 auth + CORS
P0-13 clamp power drift
P0-14 bound fault escalation
```

**Suggested first sitting (~2 h, all XS/S):** P0-4, P0-8, P0-9, P0-13, P0-14, P1-5,
P1-7, P1-8, P1-9, P1-10, P1-15, P1-16, P1-18. That clears 4 criticals and 7 highs
without touching the ML pipeline or the frontend.
