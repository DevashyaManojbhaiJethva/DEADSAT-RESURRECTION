# DeadSat Resurrection — Wiring & Deployment

How the pieces connect, and how to bring the two-Raspberry-Pi setup up.

---

## Topology

```
┌─────────────────────────────────────────────────┐
│  Pi #1 — Raspberry Pi 4 Model B        :8000    │
│                                                 │
│   SatelliteEmulator ──▶ AI-1 classifier         │
│          ▲                    │                 │
│          │                    ▼                 │
│          └──── AI-2 recovery agent (LangGraph)  │
│                       │                         │
│                       ▼                         │
│                 Crypto layer  ──▶ CY-1  :8001   │
│                 (sign / verify / ledger)        │
└─────────────────────────────────────────────────┘
        │  /rf/status (proxy)        ▲  /ws/telemetry
        ▼                            │  /ws/events
┌──────────────────────┐   ┌──────────────────────┐
│ Pi #2 — RF station   │   │ Operator browser     │
│   RTL-SDR     :8002  │   │   VITE_API_BASE      │
└──────────────────────┘   └──────────────────────┘
```

The browser only ever talks to **Pi #1**. The RF station on Pi #2 is proxied
through `/rf/status` and `/rf/spectrum`, so there is one origin to configure
and no CORS relationship with the second Pi.

---

## Connection map

| Link | Transport | Where |
|---|---|---|
| Emulator → AI-1 | in-process | `pipeline.py` builds the orbital window |
| AI-1 → AI-2 | in-process | `models/classifier_inference.py` (fault-key normalisation) |
| AI-2 → crypto (sign) | HTTP | `config.CRYPTO_SIGN_URL` |
| AI-2 → crypto (verify) | HTTP | `config.CRYPTO_VERIFY_URL` — **gate before execution** |
| Crypto → CY-1 | HTTP | `config.CY1_BASE` (`:8001`) |
| Pi #1 → Pi #2 RF | HTTP | `config.RF_BASE` (`:8002`) |
| Frontend → Pi #1 | HTTP + WS | `VITE_API_BASE` |

Check every link at runtime:

```bash
curl http://<PI1>:8000/system/links
```

```json
{ "all_connected": false,
  "links": {
    "emulator":       { "connected": true,  "detail": "frame_id=412" },
    "ai1_classifier": { "connected": false, "detail": "missing: transformer_encoder.pt" },
    "ai2_agent":      { "connected": true,  "detail": "langgraph available" },
    "crypto":         { "connected": true,  "detail": "HTTP 200" },
    "rf_station":     { "connected": false, "detail": "http://192.168.1.51:8002 unreachable" }
  } }
```

The dashboard header shows the same thing as a `LIVE TM` / `SIMULATED` badge
with an `n/m` link count; hover it for per-link detail.

---

## Setup

### Pi #1 — AI + crypto

```bash
cp .env.example .env
```

```ini
DEADSAT_API_HOST="0.0.0.0"           # accept LAN traffic
DEADSAT_PUBLIC_HOST="192.168.1.50"   # this Pi's LAN address
DEADSAT_RF_BASE="http://192.168.1.51:8002"
DEADSAT_CORS_ORIGINS="http://192.168.1.60:3000"
DEADSAT_API_KEY="pick-a-long-random-string"
```

```bash
pip install -r requirements.txt
python main.py          # or: uvicorn main:app --host 0.0.0.0 --port 8000
```

Startup prints the active wiring, so a misconfiguration is visible immediately
rather than mid-demo:

```
[Config]   API base     : http://192.168.1.50:8000
[Config]   RF station   : http://192.168.1.51:8002
[Config]   Verify gate  : ON
[Config]   Mock signing : BLOCKED
```

> **Do not train on the Pi.** Run `python train_classifier.py` on a laptop and
> copy the resulting `model_artifacts/` directory across. Inference is a
> 2-layer, `d_model=64` transformer over an 8-step window — fine on a Pi 4.
> Training is not.

### Pi #2 — RF ground station

```bash
python -m uvicorn rf_service:app --host 0.0.0.0 --port 8002
```

Only needs to expose `/rf/status` (and optionally `/rf/spectrum`). Pi #1
tolerates it being offline — `/rf/status` returns `{"online": false, ...}`
after a 3 s timeout rather than hanging the dashboard.

### Operator machine — frontend

```bash
cd frontend
cp .env.example .env
```

```ini
VITE_API_BASE="http://192.168.1.50:8000"
VITE_API_KEY="same-string-as-DEADSAT_API_KEY"
```

```bash
npm install && npm run dev      # http://localhost:3000
```

> **Then go back to Pi #1 and add this machine's origin to
> `DEADSAT_CORS_ORIGINS`.** This is the single most common way the demo
> breaks — see below.

---

### CORS — the one that will bite you on demo day

**Symptom:** the dashboard loads, the header badge reads **`LIVE TM`** with a
healthy link count, telemetry numbers update once a second — and the TLE/orbital
panel, catalog, crypto status, ledger, alerts, pipeline status and RF spectrum
are all blank. Nothing errors on screen.

**Cause:** WebSockets are exempt from the same-origin policy; `fetch()` is not.
So `/ws/telemetry` connects from any origin while every REST call is blocked by
the browser, and each one dies silently inside its `.catch()`.

The default pairing produces this:

| Setting | Default | Consequence |
|---|---|---|
| `DEADSAT_API_HOST` | `0.0.0.0` | API accepts LAN traffic |
| `npm run dev` | `--host=0.0.0.0 --port=3000` | UI reachable at `http://<LAN-IP>:3000` |
| `DEADSAT_CORS_ORIGINS` | `localhost` / `127.0.0.1` only | that LAN origin is **not** allowed |

Open the UI on `localhost` and everything works. Open it from any other machine
— which is what a two-Pi demo means — and half the dashboard is empty.

**Fix.** On Pi #1, set the origin the operator's browser actually shows in its
address bar:

```ini
# .env on Pi #1
DEADSAT_CORS_ORIGINS="http://192.168.1.60:3000"
```

Several machines, or local + LAN together:

```ini
DEADSAT_CORS_ORIGINS="http://localhost:3000,http://192.168.1.60:3000"
```

The origin must match **scheme + host + port** exactly. `http://192.168.1.60:3000`
and `http://192.168.1.60` are different origins; so are `localhost` and
`127.0.0.1`.

**Do not set `"*"`.** This API exposes `/fault/inject`, `/recovery/trigger` and
`/reset`, unauthenticated unless `DEADSAT_API_KEY` is set. `"*"` means any web
page the operator has open can drive the satellite.

**Detection.** `config.print_banner()` checks for this at startup and prints a
banner naming the variable when the API is LAN-bound but every allowed origin is
loopback:

```
[Config]   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
[Config]   WARNING: API is bound to 0.0.0.0 (LAN-facing) but every
[Config]            CORS origin is loopback-only:
[Config]              http://localhost:3000, http://127.0.0.1:3000
[Config]            A browser on any other machine will connect the
[Config]            WebSockets — the dashboard will say LIVE TM — while
[Config]            EVERY REST panel is blocked and silently empty.
[Config]            Set DEADSAT_CORS_ORIGINS to the operator's real
[Config]            origin, e.g.:
[Config]              DEADSAT_CORS_ORIGINS=http://192.168.1.60:3000
[Config]   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

Check it directly at any time:

```bash
python -c "import config; print(config.cors_is_unreachable_from_lan())"
```

---

## Security gates

Two switches control how strictly commands are handled. Both default to the
safe setting.

| Variable | Default | Effect |
|---|---|---|
| `DEADSAT_REQUIRE_VERIFICATION` | `1` | Every signed command must pass `/crypto/verify` before the emulator applies the procedure. |
| `DEADSAT_ALLOW_MOCK_SIGNING` | `0` | With CY-1 down, signing **fails** instead of producing a fake signature. |
| `DEADSAT_API_KEY` | *(empty)* | When set, mutating routes require `X-API-Key`. |

What changed:

- Previously any signing error produced `MOCK_SIG_...` marked `signed: True`,
  after which the agent logged "CY-1 signing SUCCESS". Taking the crypto
  service offline bypassed signing entirely. Signing failure now aborts and
  routes to the fallback procedure.
- `verify_command()` existed but was unreachable from the recovery path.
  `node_uplink_commands` now verifies every command first and refuses to call
  `emulator.apply_recovery()` if any fails.
- Mock signatures are rejected by the gate even when explicitly enabled, so
  the bench-mode escape hatch cannot silently certify unsigned commands.

Verified behaviour with the crypto service down:

```
[Agent] ── Node 6: Uplinking commands to satellite
[Agent]    Verifying 1 signatures with CY-1 ...
[Agent]    VERIFICATION FAILED — refusing uplink (X=MOCK_SIGNATURE)
  emulator executed : []          <- procedure never applied
  routed to         : fallback
```

---

## Fault mapping

The UI offers five faults; the emulator models four. `UI_FAULT_TO_BACKEND` in
`frontend/api.ts`:

| UI option | Backend fault | Why |
|---|---|---|
| `seu` | `SEU` | direct |
| `leak` | `software_bug` | direct |
| `injection` | `command_injection` | direct |
| `battery_fail` | `firmware_corruption` | no dedicated battery fault; this is the one that degrades the power bus |
| `adcs_fail` | `SEU` | an ADCS actuator failure presents as an SEU in this emulator |

---

## Troubleshooting

**Header says SIMULATED.** The telemetry WebSocket is down. Check
`VITE_API_BASE` and that Pi #1 is bound to `0.0.0.0` (not `127.0.0.1`).
CORS is *not* the cause here — WebSockets are exempt from it, so a CORS problem
leaves the header saying `LIVE TM`. See the next entry.

**Header says LIVE TM but the panels are empty.** CORS. The browser origin is
not in `DEADSAT_CORS_ORIGINS`, so every REST call is blocked while the
WebSockets keep streaming. Check Pi #1's startup banner for the
`API is bound to 0.0.0.0 ... but every CORS origin is loopback-only` warning, or
run `python -c "import config; print(config.cors_is_unreachable_from_lan())"`.
Full explanation in [CORS — the one that will bite you on demo day](#cors--the-one-that-will-bite-you-on-demo-day).

**Fault injection returns 401.** `DEADSAT_API_KEY` is set on Pi #1 but
`VITE_API_KEY` doesn't match.

**Recovery always fails with `VERIFY_UNAVAILABLE`.** CY-1 isn't running on
`:8001`. Start it, point `DEADSAT_CY1_BASE` at it, or — for a bench run only —
set `DEADSAT_REQUIRE_VERIFICATION=0`.

**RF panel shows offline.** Expected when Pi #2 is down. Confirm with
`curl http://<PI2>:8002/rf/status` and check `DEADSAT_RF_BASE`.

**Pipeline runs but AI-1 is skipped.** `model_artifacts/` is missing. Check
`/pipeline/status`; train on a laptop and copy the directory over.

---

## Note on the duplicated tree

`backend/` remains a parallel copy of the application, so every change here was
applied twice: `emulator/` ↔ `backend/emulator/`, `agents/` ↔ `backend/agents/`,
`models/` ↔ `backend/pipeline/`, `main.py` ↔ `backend/main.py`, plus
`config.py` ↔ `backend/config.py`. See `docs/FIX_PRIORITY.md` P1-14 for the
proposed drift guard.
