# DeadSat Resurrection — Wiring / Crash / Frontend-Output Audit

**Date:** 2026-08-15
**Method:** static analysis of the full tree (114 files, root + `backend/` + `frontend/`).
No live boot — the sandbox has no network, so `pip install fastapi` and
`npx vite build` both fail. Everything below is traced through the code,
not guessed; line numbers are given so each item can be checked directly.

---

## Summary

| Severity | Count | Effect |
|---|---|---|
| Blocker | 4 | App does not start / page is blank |
| Broken feature | 2 | Recovery and AI-1 can never succeed as shipped |
| Wiring bug | 5 | Wrong or missing values on the dashboard |
| Hygiene | 7 | Drift, dead code, stale docs |

---

## 1. Blockers — nothing renders / nothing boots

### B1 · Frontend loads a file that does not exist → blank white page
`frontend/index.html:12`

```html
<script type="module" src="/src/main.tsx"></script>
```

There is no `frontend/src/`. `main.tsx` sits at `frontend/main.tsx`.
Vite resolves the entry relative to project root, gets a 404, and renders an
empty `<div id="root">`. **This alone is enough for "nothing shows on the
frontend."**

**Fix:** `src="/main.tsx"`

---

### B2 · `frontend/node_modules` is a half-finished install
198 packages present, but **no `vite`, no `typescript`, no `node_modules/.bin/`**.
`npm run dev`, `npm run build` and `npm run lint` all fail.

**Fix:** `cd frontend && rm -rf node_modules package-lock.json && npm install`

---

### B3 · `backend/main.py` crashes at import
`backend/main.py:48,60-61`

```python
from crypto_routes import router as crypto_router, startup_crypto, limiter
from slowapi import Limiter, _rate_limit_exceeded_handler
```

`crypto_routes` pulls in `oqs`, `redis`, `nacl`; the module itself needs
`slowapi`. **None of `slowapi`, `redis`, `pynacl`, `liboqs-python` are in
either `requirements.txt`.** Result: `ModuleNotFoundError` before the app
object is built.

**Fix:** add the four packages to `backend/requirements.txt`, or keep
`backend/` out of the run path and treat root `main.py` as the only entry point.

---

### B4 · CY-1 (`:8001`) cannot be started from the root tree
`crypto/sign.py:1` and `crypto/verify.py:1` begin with `import oqs`.
`backend/crypto/` has a `mock_oqs_nacl` shim imported ahead of it — **the root
`crypto/` copy does not**, and root `crypto/` is also missing `__init__.py`.

So `python crypto/crypto_routes.py` (the only thing that would serve `:8001`)
dies immediately unless real liboqs is installed. Nothing else in the repo
listens on 8001.

**Fix:** copy `backend/crypto/mock_oqs_nacl.py` and `backend/crypto/__init__.py`
into `crypto/`, and add the same `import mock_oqs_nacl` first line to
`crypto/sign.py` and `crypto/verify.py`.

---

## 2. Features that can never succeed as shipped

### F1 · Every recovery ends in "RECOVERY FAILED"
The chain, with default config:

1. `config.py:151` — `REQUIRE_COMMAND_VERIFICATION = True`
2. `config.py:158` — `ALLOW_MOCK_SIGNING = False`
3. CY-1 is down (see **B4**), so `main.py:447-458` `/crypto/sign` returns a
   fabricated `MOCK_ML_DSA_…` signature with `"mock": true`
4. `agents/recovery_agent.py:301` carries `mock` through onto the command
5. `agents/recovery_agent.py:363` — `if cmd.get("mock"): return (False, "MOCK_SIGNATURE")`
6. `agents/recovery_agent.py:455-465` — verification gate refuses the uplink,
   sets `state["error"]`

The frontend then shows `RECOVERY FAILED: …` in the console
(`useDeadsat.ts:78-85`) and the anomaly feed never clears.

This is *correct security behaviour* — an unverifiable signature should not
execute — but with no runnable CY-1 the headline demo can never pass.

**Fix (pick one):**
- Make CY-1 runnable (B4), then start it: `python crypto/crypto_routes.py`
- Or, for a bench demo only: `DEADSAT_REQUIRE_VERIFICATION=0` in `.env`
  (and say so on screen — do not present it as verified)

---

### F2 · AI-1 has no trained artifacts
`models/classifier_inference.py:138-145` expects
`model_artifacts/transformer_encoder.pt`, `isolation_forest.pkl`, `scaler.pkl`.
There is **no `model_artifacts/` directory and no `.pt`/`.pkl` anywhere in the repo.**

Consequences on screen:

| UI | Behaviour |
|---|---|
| `/system/links` badge | `ai1_classifier` always DOWN → header shows `n/m`, never all-green |
| AI Diagnostics → "recalibrate" | always `classification failed — 503` (`AiDiagnostics.tsx:78`) |
| Operator Control Panel | accuracy/truePos pinned at 0, polled every 15 s (`OperatorControlPanel.tsx:141-152`) |
| `/pipeline/run` without `skip_classifier` | fails |

**Fix:** run `python train_classifier.py` and commit or ship the artifacts, or
default the UI calls to `skip_classifier: true`.

---

## 3. Wiring bugs — values render, but wrong

### W1 · The WebSocket history envelope is parsed as a telemetry frame ⚠ most visible
`main.py:653-658` sends this as the **first** `/ws/telemetry` message:

```json
{ "type": "history", "frames": [...60 frames...], "count": 60 }
```

`api.ts:subscribe()` `JSON.parse`s every message and hands it straight to the
frame callback — nothing checks `type`. Three components receive an object with
no telemetry fields:

- `SatelliteDashboard.tsx:477` — `Math.round(undefined)` → `NaN` → the OBC
  register pane renders **`SP: 0x1FFF00NaN`**
- `SatelliteDashboard.tsx:461` — a zeroed junk point is prepended to the chart
- `SatelliteDashboard.tsx:487` — log line `WS frame undefined — health=n/a`
- `AiDiagnostics.tsx:52-68` — all five channels flash `0.0` / `DOWNLINK OFF`
  in red `CRITICAL` for ~1 s on every connect and every reconnect
- `useDeadsat.ts:139` — `frame` briefly holds the envelope

**And the 60 backfilled frames are silently discarded** — the charts the
history was added for never actually get seeded from it.

**Fix** in `api.ts`, inside `subscribeTelemetry`:

```ts
const msg = JSON.parse(ev.data);
if (msg?.type === 'history') { onHistory?.(msg.frames ?? []); return; }
onMessage(msg as TelemetryFrame);
```

---

### W2 · CORS blocks every REST call in the LAN demo
`package.json` dev script binds `vite --port=3000 --host=0.0.0.0`, so the
operator opens `http://192.168.1.60:3000`.
`config.py:139` defaults to only
`http://localhost:3000, http://127.0.0.1:3000, http://localhost:5173`.

WebSockets are exempt from CORS, so **telemetry streams fine and the header
proudly says `LIVE TM`** — while every REST panel (TLE/catalog, crypto status,
ledger, alerts, pipeline status, `/system/links`, RF spectrum) fails silently
in its `.catch()` and stays empty. This is the classic "half the dashboard is
blank but it says it's connected" symptom.

**Fix:** in `.env` on Pi #1 — `DEADSAT_CORS_ORIGINS=http://192.168.1.60:3000`

---

### W3 · The real crypto implementation is dead code on the tree you run
Root `main.py` **never** does `include_router(crypto_router)` — it hand-rolls
`/crypto/*` at lines 431-637, all of which either proxy to CY-1 or return
mocks. The actual hybrid Ed25519 + ML-DSA-65 code in `crypto/sign.py`,
`verify.py`, `ledger.py`, `nonce.py`, `rogue_detector.py` is never imported.
`backend/main.py:211` *does* mount it. So the two trees have genuinely
different crypto behaviour, and the one you run is the mock one.

---

### W4 · Ground track, altitude and velocity are still fabricated
`api.ts:frameToTelemetryState()` — lat/lng advance by a fixed `+0.002 / +0.005`
per frame and altitude/velocity fall back to constants `402.18` / `7.672`.
The globe position is not the satellite's real position. The catalog and
`ContactCalculator` (sgp4) already have what's needed to compute this properly.

---

### W5 · Duplicate route in `backend/main.py`
Router registers `/crypto/check-command` at line 211; `@app.post` registers the
same path again at line 413. FastAPI serves the first — the 60-line handler at
413 is unreachable.

---

## 4. Hygiene / fragility

| # | Item | Where |
|---|---|---|
| H1 | `if __name__ == "__main__": uvicorn.run("main:app")` sits mid-file with ~340 more routes defined *after* it. Works only because uvicorn re-imports the module — but running `python main.py` directly executes module-level side effects twice, building two `SatelliteEmulator` instances. | `main.py:688`, `backend/main.py:510` |
| H2 | Root ↔ `backend/` drift: `real_data_fetcher.py` (22 lines differ), `models/classifier_inference.py` (6), `crypto/sign.py` + `verify.py` (the mock shim). Every fix must still be applied twice. | — |
| H3 | README setup says `cd frontend/dashboard` and `cd frontend/operator`. Neither exists — there is one `frontend/`. | `README.md:467-468` |
| H4 | `@google/genai`, `express`, `dotenv` are dependencies but nothing imports them. | `frontend/package.json` |
| H5 | `/catalog/search` reaches into private `cat._loaded` / `cat._catalog`. | `main.py:733-741` |
| H6 | Dead line `const noradId = 0; // replaced below by the live frame's norad_id` | `SatelliteDashboard.tsx:347` |
| H7 | Whole repo is uncommitted vs `git HEAD`; `D:\faraway\deadsat_resurrectuion nodel pipeline\` is a stale double-nested copy of an older zip. | — |

---

## Suggested order of work

1. **B1** — one character; gets the UI on screen at all
2. **B2** — reinstall node_modules
3. **W1** — the `type: 'history'` guard; removes NaN and the false CRITICAL flash
4. **W2** — set `DEADSAT_CORS_ORIGINS`; unblocks every REST panel
5. **B4 → F1** — mock shim into root `crypto/`, start CY-1, recovery goes green
6. **F2** — train and ship AI-1 artifacts
7. **B3 / W3 / H2** — decide whether `backend/` is a live entry point or an
   archive, and delete or fix it accordingly. Keeping two divergent copies is
   the root cause of half this list.
