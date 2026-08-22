# DeadSat Resurrection — Roadmap from 6.8 to 9.5

**Current: 6.8/10.** Target: 9.5. Realistic effort: **~2 weeks focused**, ~10 working days.

Each item lists the file, the effort, the score it moves, and — most importantly —
**acceptance criteria**, so "done" is a fact rather than an opinion.

| Dimension | Now | Target | Gap driver |
|---|---|---|---|
| Concept & originality | 9 | 9 | already strong, leave it alone |
| Scope & ambition | 9 | 9 | already strong |
| Architecture | 7 | 9 | tree duplication, no auth story |
| Backend implementation | 6 | 9 | emulator fidelity, unbounded state |
| Documentation | 7 | 9.5 | claims that don't match code |
| Frontend | 5 | 9 | never type-checked, unrendered data |
| Security implementation | 4 | 9.5 | gate now runs; nonce + WS auth open |
| ML rigour | 4.5 | 9 | dataset shape + 3 leakage bugs |
| Verification & testing | 3 | 9 | no CI, no unit tests |

> **One honest ceiling.** Without access to real fault-labelled telemetry you
> cannot claim real-world validated performance — that caps ML at ~9, not 10.
> The 9 is reachable by being rigorous *and* explicit: physically-grounded
> synthetic faults, leak-free evaluation, a baseline to compare against, and a
> stated limitation. Reviewers reward that. 9.5 overall is achievable because
> the other eight dimensions can each reach 9+.

---

## TIER A — Credibility blockers (≈3 days, 6.8 → 8.2)

Nothing else counts until these are done. Each one is currently a claim the
code contradicts.

### A1 · Run the TypeScript compiler
`frontend/` · **30 min** · unblocks everything in the frontend

Nothing in the frontend has ever been type-checked. Every other frontend item
is provisional until this passes.

```bash
cd frontend && npm install && npm run lint
```

**Acceptance:** `tsc --noEmit` exits 0. Fix what it finds before continuing.

---

### A2 · Regenerate the dataset with a real time axis
**[NEW FILE]** `generate_dataset.py` (repo root) · **1 day** · ML 4.5 → 7

The dataset is one row per satellite at one instant, so `REV_DELTA` and
`ecc_delta` are 0 for 100% of rows and three of four fault rules cannot fire.
Zero real SEU examples exist.

Propagate each GP row forward with `sgp4` (already a dependency) using
`satellite_catalog.build_tle_from_gp()`, ~20 epochs at ~90-minute steps.
Re-derive elements at each step, then inject faults as perturbations to the
*series*:

| Fault | Injection |
|---|---|
| SEU | one-epoch eccentricity/mean-anomaly step that reverts next epoch |
| SOFTWARE_BUG | freeze or roll back `REV_AT_EPOCH` for a run of epochs |
| FIRMWARE_CORRUPTION | ramp `BSTAR` / `MEAN_MOTION_DOT` past threshold |
| COMMAND_INJECTION | withhold epochs so `TLE_AGE_HOURS` passes 72 |

Also dedup the 141 exact `(NORAD_CAT_ID, EPOCH)` duplicates from the three
overlapping CSVs.

**Acceptance:**
- every satellite has ≥ `seq_len` distinct epochs
- `assign_fault_labels()` on the output yields ≥ 300 rows of **each** of the four classes, all from injected series rather than `_generate_synthetic_class`
- `ecc_delta` and `REV_DELTA` are non-zero for > 90% of rows
- `python generate_dataset.py --verify` prints the class distribution

---

### A3 · Remove the three leakage bugs
`models/satellite_fault_classifier_V2.py` · **3 h** · ML 7 → 8.5

1. **Shuffle-before-window** — split by satellite with `GroupShuffleSplit(groups=NORAD_CAT_ID)`, then build windows *within* each satellite sorted by `EPOCH`.
2. **Augment-before-split** — augment the training split only.
3. **Scaler on full data** — fit on train, `transform()` val/test.

Also fix the SEU contradiction: `assign_fault_labels()` defines SEU as a jump,
`_generate_synthetic_class()` as a constant `ECCENTRICITY=0.05`. After A2,
delete the call path.

**Acceptance:**
- no `NORAD_CAT_ID` appears in more than one split (assert it in a test)
- windows never straddle a satellite boundary (assert it)
- scaler `.fit()` called exactly once, on train only
- test accuracy **drops** — if it doesn't, the leak is still there

---

### A4 · Make recovery verification real
`emulator/satellite_emulator.py`, `agents/recovery_agent.py` · **4 h** · Backend 6 → 8

Two bugs make "automatic fallback to an alternate procedure" untestable:

- `apply_recovery()` clears `fault_injected` for **any** recognised procedure name. Applying the comms procedure to an SEU returns `True` and logs "Recovery SUCCESS". Map each procedure to the faults it can remedy; return `False` otherwise.
- `node_monitor_recovery` accepts `if passed or health == "nominal"`, so success criteria are advisory. Drop the `or`.

**Acceptance:**
- a test injects SEU, applies `LOCKDOWN_REGEN_v1`, asserts `False` and that the fault is still active
- a test forces the primary procedure to fail and asserts the fallback actually runs and is recorded in `recovery_log`

---

### A5 · Reconcile every claim with the code
`agents/recovery_agent.py`, `crypto/verify.py`, `emulator/contact_calculator.py`, `README.md` · **2 h** · Docs 7 → 9

Cheapest points available. Either implement or delete:

| Claim | Reality |
|---|---|
| "Improvement 2 — noise on fault telemetry" | `_update_nominal_drift()` returns early during faults; telemetry freezes |
| "Improvement 4 — fallback TLE updated to recent epoch" | epoch `24163` = June **2024** |
| "Uses hmac.compare_digest() to prevent timing attacks" | never called; `import hmac` unused |
| "SET NX — atomic" (nonce) | plain `get` then `set` — a race |
| README "13 telemetry features" | V2 uses 11 orbital elements |
| header "All bugs fixed, all improvements applied" | three of them aren't |

**Acceptance:** grep the repo for "fixed", "improvement", "hardened" — every
remaining instance is verifiable by a test or a line of code.

---

## TIER B — Substance (≈4 days, 8.2 → 9.0)

### B1 · Honest metrics + a baseline
**[NEW FILE]** `docs/MODEL_CARD.md` · **4 h** · ML 8.5 → 9

A transformer is only justified if it beats something simpler. Train a logistic
regression and a gradient-boosted tree on the same leak-free splits and report
all three.

**Acceptance:** model card states dataset provenance (real GP elements +
synthetic fault injection), split strategy, per-class precision/recall/F1,
confusion matrix, the baseline comparison, and an explicit limitations section
naming the absence of real fault-labelled telemetry.

### B2 · Close the remaining security gaps
`crypto/nonce.py`, `main.py` · **1 day** · Security 4 → 9

- **Nonce race** — use `redis.set(key, nonce, nx=True, ex=...)`; treat `False` as replay. Current code does `get` then `set`, and a failed `compare_digest` falls through to an unconditional `set` that *overwrites* the nonce.
- **Nonce burned at signing, not verification** — move `use_nonce()` into `/crypto/verify`.
- **WebSocket auth** — `/ws/telemetry` and `/ws/events` accept anyone on the LAN regardless of `DEADSAT_API_KEY`.
- **TTL vs contact window** — 120 s signature TTL against a possible 24 h wait for AOS. Reconcile.
- **`sys.exit(1)` in `verify.py`** — a library killing the API process. Raise instead.

**Acceptance:** a test fires two concurrent requests with the same nonce and
asserts exactly one succeeds; an unauthenticated WebSocket connect is refused
when the key is set.

### B3 · Clamp the emulator
`emulator/satellite_emulator.py` · **3 h** · Backend 8 → 9

Verified failures: `power_w` drifts to **46.6 W** after ~33 min (below the
`> 75` success criterion, so a long demo fails on its own), `adcs_rate_deg_s`
reaches **14.7 °/s**, `start()` isn't idempotent and leaves orphaned threads.

**Acceptance:** a test runs 5000 ticks and asserts every telemetry field stays
within physical bounds; calling `start()` twice yields one thread.

### B4 · Tests and CI
**[NEW FILE]** `tests/` + CI workflow · **1 day** · Testing 3 → 9

`test_integration.py` covers the seams; add unit tests for the logic that keeps
breaking: `_check_criteria` operators, fault-key normalisation, window
construction, procedure selection, nonce replay.

**Acceptance:** CI runs `test_integration.py` + unit tests + `tsc --noEmit` on
every push. Badge in the README.

### B5 · Finish the frontend
`frontend/components/*` · **1 day** · Frontend 5 → 9

- render the six fetched-but-unused values — the **crypto ledger** especially, it's the best evidence the security layer works and it's already being fetched
- fix the 5-minute blank TLE panel on cold start (retry until `norad_id` resolves)
- stop polling `/pipeline/classify` every 15 s
- collapse 6 WebSocket connections to 2
- reconcile the 5-fault UI against the 4-fault emulator

**Acceptance:** `tsc` clean; a cold start shows real data within 5 s; browser
devtools shows exactly 2 WebSocket connections.

---

## TIER C — The last 0.5 (≈3 days, 9.0 → 9.5)

### C1 · Demonstrate the two-Pi deployment
**1 day** · Architecture 7 → 9

The two-Pi split is a genuine differentiator and currently exists only as
config. Record `/system/links` showing all components green across both Pis,
with the RF station live.

**Acceptance:** a short video or screenshot set showing fault → classify →
recover with Pi #2 feeding real RTL-SDR spectrum.

### C2 · Reproducibility
**4 h** · Architecture, Docs

Pin dependencies with a lockfile, seed every RNG (`augment_fault_samples()`
currently uses the global NumPy RNG while everything else is seeded), and make
`train_classifier.py` produce byte-identical artifacts across runs.

**Acceptance:** two clean training runs produce identical `meta.json` and
matching evaluation metrics.

### C3 · `backend/` drift guard
**[NEW FILE]** `test_backend_sync.py` · **1 h**

The tree stays duplicated by constraint, so make divergence impossible to miss.
This already caught a fatal bug: `backend/main.py` used `cfg.`, `httpx.` and
`Depends` without importing any of them and could not start at all.

**Acceptance:** test asserts mirrored files are byte-identical modulo line
endings; runs in CI.

### C4 · Orbital mechanics cleanup
**4 h** · Backend

Refresh the 2024 fallback TLE; replace the 8,640-propagation linear AOS scan
with coarse-then-refine (~50× cheaper, and it runs synchronously inside the
recovery graph on a Pi); validate fetched TLE lines before `twoline2rv`.

### C5 · A written threat model
**[NEW FILE]** `docs/THREAT_MODEL.md` · **3 h** · Security 9 → 9.5

The project's thesis is authenticated satellite command. State the adversary,
the trust boundaries, what's in scope (uplink forgery, replay, quantum-capable
attacker) and what isn't (physical access, supply chain). This is what separates
"we used post-quantum crypto" from "we understood why."

---

## Sequence

```
Week 1   A1 → A2 → A3 → A4 → A5          (6.8 → 8.2)
Week 2   B1 → B2 → B3 → B4 → B5          (8.2 → 9.0)
Spill    C1 → C2 → C3 → C4 → C5          (9.0 → 9.5)
```

Do **A1 first** — it's 30 minutes and everything frontend-related is
unverified until it passes.

**If you only get three days:** A1, A2, A3, A5. That fixes the invalid
evaluation and the false claims, which are the two things a careful reviewer
will find first — and takes you to roughly 8.

---

## What is already done

For context, 15 of the 86 audited bugs are fixed, plus the frontend wiring:

- fault-key normalisation (AI-1 → AI-2 handoff no longer `KeyError`s)
- verification gate — commands are checked before the emulator executes them
- mock-signing bypass removed (crypto service down now *fails*, not fakes)
- CORS allowlist + optional API-key auth
- two-Pi config, RF bridge, `/system/links`
- frontend connected: zero fabricated data outside 3D decoration
- 109-check integration suite

Three of those were fatal: `backend/main.py` could not import at all, and
`/crypto/verify` and `/crypto/rotate` didn't exist on the trees that called them.
