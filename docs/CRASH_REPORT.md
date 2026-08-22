# DeadSat Resurrection — Crash & Fix Priority

Post-wiring crash hunt. **2026-08-11**

Method: import-time `NameError` analysis on both trees, `cfg.*` attribute
resolution, frontend→backend endpoint reachability, `api.*` method resolution,
WebSocket connection counting, and render-vs-state reference checks.

| | Count |
|---|---|
| **C0 — Fatal, fixed during this pass** | 3 |
| **C1 — Will crash / fail at runtime** | 4 |
| **C2 — Degrades silently** | 6 |
| **C3 — Waste / polish** | 5 |

---

## C0 — Fatal. Found and fixed in this pass.

### C0-1 · `backend/main.py` could not start at all ✅ FIXED
**Severity: fatal — module never imports**

The RF-bridge and crypto blocks mirrored into `backend/main.py` used `cfg.` 12
times, `httpx.` 5 times, and `Depends(require_api_key)` in a parameter default —
but the file imported **none** of them, and `require_api_key` was never defined
there.

Because `_auth: None = Depends(require_api_key)` is a *default argument*, it is
evaluated when the `def` executes, i.e. at import time:

```
NameError: name 'Depends' is not defined
```

The entire `backend/` server was dead. Fixed by adding `Depends`, `Header`,
`httpx`, `import config as cfg`, and the `require_api_key` definition.

> **This is the one to be aware of** — if you had deployed `backend/main.py` to
> Pi #1 it would not have booted. Root `main.py` was unaffected.

### C0-2 · `/crypto/verify` did not exist on root `main.py` ✅ FIXED
**Severity: fatal to every recovery**

The new verification gate calls `{API_BASE}/crypto/verify` before the emulator
applies any procedure. That route only existed on `backend/` (via the crypto
router). On root `main.py` every recovery would fail closed with
`VERIFY_UNAVAILABLE: 404`. Added `/crypto/verify`, plus `/crypto/ledger` and
`/crypto/alerts` which the frontend also calls.

### C0-3 · `/crypto/rotate` missing from `backend/` ✅ FIXED
Security Console's key-rotation button would 404 against the backend tree.
Mirrored across.

---

## C1 — Will crash or hard-fail at runtime. Not yet fixed.

### C1-1 · `SatelliteDashboard` never retries the TLE fetch after a cold start
**Impact: TLE/orbit panel stays blank for 5 minutes**

`load()` calls `api.telemetry()` and only proceeds `if (f?.norad_id)`. Before
the emulator's first tick, `get_latest_frame()` returns `{}`, so `norad_id` is
undefined, the TLE fetch is skipped, and the next attempt is **300 seconds**
later. If the UI loads faster than the backend boots — the normal case — the
orbital panel is empty for five minutes.

*Fix:* retry on a short backoff until `norad_id` resolves, then fall back to the
5-minute refresh. `frontend/components/SatelliteDashboard.tsx`. **XS**

### C1-2 · API-key mismatch 401s every control with no visible reason
**Impact: all operator actions fail silently**

Setting `DEADSAT_API_KEY` on Pi #1 without `VITE_API_KEY` in `frontend/.env`
makes `/fault/inject`, `/pipeline/run`, `/pipeline/classify`, `/reset` and
`/crypto/rotate` return 401. The UI surfaces this only as a small trace-log
line; the dashboard otherwise looks healthy because telemetry (a WebSocket) is
unauthenticated and keeps flowing.

*Fix:* have `/system/links` report auth state, and show a persistent banner when
a mutating call returns 401. `main.py`, `frontend/useDeadsat.ts`. **S**

### C1-3 · WebSockets bypass authentication entirely
**Impact: security hole; also why C1-2 is invisible**

`require_api_key` guards REST routes only. `/ws/telemetry` and `/ws/events`
accept any connection, so anyone on the LAN can stream live telemetry and watch
recovery events regardless of `DEADSAT_API_KEY`.

*Fix:* accept the key as a query parameter or first message and close
unauthenticated sockets. `main.py`, `backend/main.py`. **S**

### C1-4 · `/pipeline/classify` polled every 15 s with no backoff
**Impact: constant 503s, wasted Pi CPU**

`OperatorControlPanel` polls AI-1 every 15 seconds. With artifacts untrained
that is a 503 four times a minute, forever. With artifacts present it is a full
transformer inference pass every 15 s on a Raspberry Pi 4 — for a number shown
in a corner of the UI.

*Fix:* poll `/pipeline/status` once, and only classify on demand or on a fault
transition. `frontend/components/OperatorControlPanel.tsx`. **XS**

---

## C2 — Degrades silently. Wrong or missing output, no error shown.

### C2-1 · Six fetched values are never rendered
Data is fetched and thrown away:

| Component | Fetched but not displayed |
|---|---|
| `SecurityConsole` | `ledger`, `cryptoMode`, `lastError` |
| `AiDiagnostics` | `statusHint`, `artifactsReady`, `lastClass` |

The Security Console pulls the signed-command ledger and never shows it — the
one artifact that most directly demonstrates the crypto layer working.
*Fix:* render them. **S**

### C2-2 · Emulator status has no "Payload" concept
`SatelliteDashboard` maps `Payload` from `overall_health`, so it can never
disagree with the other subsystems. The emulator models OBC/ADCS/Power/Comms
only. Either add a payload subsystem or drop the row. **XS**

### C2-3 · `battery_fail` and `adcs_fail` collapse onto other faults
The UI offers 5 faults, the emulator models 4, so `battery_fail →
firmware_corruption` and `adcs_fail → SEU`. Selecting either shows a diagnosis
that does not match the label. Either add the two faults to the emulator or
remove them from the dropdown. **M**

### C2-4 · `/system/links` reports `ai2_agent` connected if `langgraph` merely imports
It checks `import langgraph`, not that `RecoveryAgent` constructs or that
`procedure_library.json` loads. A broken procedure library reports healthy.
**XS**

### C2-5 · RF spectrum shape is assumed, not agreed
The waterfall reads `res.data.bins ?? res.data.power_dbm`. Pi #2's actual
response shape has not been confirmed — if it differs, the panel shows a flat
floor and reports "offline" while Pi #2 is in fact running.
*Fix:* pin the contract once Pi #2 exists. **S**

### C2-6 · Contact-window countdown is decorative
`countdownSecs` decrements on every telemetry frame and resets at 0. It is not
derived from `/contact`'s real AOS time. **S**

---

## C3 — Waste and polish.

### C3-1 · Six WebSocket connections per dashboard load
`useDeadsat`, `AiDiagnostics` and `SatelliteDashboard` each open their own
telemetry socket; `useDeadsat`, `OperatorControlPanel` and `SatelliteDashboard`
each open an events socket. **3 telemetry + 3 events = 6**, where 2 would do.

Harmless functionally — the backend fans out fine — but on a Pi 4 it triples
serialisation work per frame.
*Fix:* lift both streams into `useDeadsat` and pass down via context or props.
**M**

### C3-2 · Dead `const noradId = 0;` left in `SatelliteDashboard`. **XS**

### C3-3 · Unused type imports `TelemetryState` / `SystemLog` in `App.tsx`. **XS**

### C3-4 · `AuthScreen` still uses a `setInterval` animation with no backend tie-in.
Cosmetic only — flagged for completeness. **XS**

### C3-5 · `tsc` has never been run against any of this.
`npm install` cannot reach the registry from this environment, so every
TypeScript change is verified structurally only (balanced delimiters, resolved
imports, declared identifiers). **Run `npm run lint` before the demo** — this is
the single highest-value check still outstanding, and it may surface type errors
that invalidate items above.

---

## Suggested order

1. **C3-5** — run `npm run lint`. Everything else is guesswork until the
   compiler has seen this code once.
2. **C1-1** — TLE retry. One-line-ish fix, removes a five-minute blank panel on
   every cold start.
3. **C1-4** — stop polling the classifier. Cheapest CPU win on the Pi.
4. **C1-2 + C1-3** — auth consistency. Do these together; WebSocket auth is why
   the 401s are currently invisible.
5. **C2-1** — render the ledger. It is the best evidence the crypto layer works
   and it is already being fetched.
6. **C2-3** — reconcile the 5-vs-4 fault mismatch before anyone demos
   `battery_fail`.
7. **C3-1** — collapse the six sockets to two.

---

## Verification status

| Check | Result |
|---|---|
| Import-time `NameError` (both trees) | clean |
| `cfg.*` attribute resolution | 24/24 resolve |
| Python compile, all files | clean |
| Integration suite | 109 passed, 0 failed, 4 skipped |
| Frontend → backend endpoints | 23/23 resolve on both trees |
| `api.*` method resolution | all resolve |
| Component state vs render refs | no undefined references |
| **TypeScript compile** | **NOT RUN — no registry access** |
