# 🔐 CY-1 Crypto Layer — DeadSat Resurrection

> **Post-Quantum Hybrid Cryptography for Autonomous Satellite Command Signing**  
> FAR AWAY 2026 · Space & Aerospace × Cybersecurity  
> Owner: **CY-1 — Cyber Security + Team Leader**

---

## Overview

This module implements a **hybrid post-quantum cryptography layer** for the DeadSat Resurrection ground station. Every recovery command is signed with a hybrid Ed25519 + ML-DSA-65 signature before uplink. Commands with invalid, expired, or replayed signatures are rejected before execution.

> **Scope of that claim.** It holds **only when real liboqs and PyNaCl are
> installed**. With the `mock_oqs_nacl` development shim active the primitives
> are fakes — see [Post-quantum dependency (liboqs)](#post-quantum-dependency-liboqs-—-is-it-actually-required)
> below. In that state `/crypto/sign` refuses with 503 and `/crypto/health`
> reports `status: mock`, so the layer fails closed rather than pretending;
> but nothing here is post-quantum until liboqs is built.
>
> "Production-grade" was dropped from this sentence deliberately. The layer has
> not been deployed, audited, or run against an adversary, and it depends on a
> single-node Redis for replay protection that degrades to a process-local
> store when Redis is absent.

| Standard | Algorithm | Type |
|---|---|---|
| NIST FIPS 204 (2024) | ML-DSA-65 | Post-Quantum |
| RFC 8032 | Ed25519 | Classical |
| Both must verify | Hybrid | Defense-in-depth |

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│            CY-1 Crypto Layer (Pi #1)            │
│                                                 │
│  keygen.py ──► Hybrid Keypair (Ed25519+ML-DSA)  │
│  sign.py   ──► Dual Signature + TTL 120s        │
│  verify.py ──► Dual Verify + Expiry Check       │
│  ledger.py ──► SHA-256 Hash-Chain + Watchdog    │
│  nonce.py  ──► Redis Atomic Replay Protection   │
│  rogue_detector.py ──► 4-Type Alert System      │
│  crypto_routes.py  ──► FastAPI REST :8001       │
└─────────────────────────────────────────────────┘
```

---

## Files

| File | Description |
|---|---|
| `keygen.py` | Hybrid Ed25519 + ML-DSA-65 keypair, SSH-style fingerprint, startup self-test |
| `sign.py` | Dual signature + TTL 120s + nonce generation |
| `verify.py` | Dual verify + expiry check, fail-fast ordering |
| `ledger.py` | SQLite hash-chain ledger + watchdog thread (10s interval) |
| `nonce.py` | Redis-backed nonce store, atomic `SET NX EX`, 24h auto-expiry |
| `rogue_detector.py` | Rogue command detector — 4 alert types, SEVERITY dict |
| `crypto_routes.py` | FastAPI router — 7 endpoints, CORS, rate limiting |

---

## Security Features

| Feature | Implementation |
|---|---|
| Post-Quantum signing | ML-DSA-65 (NIST FIPS 204 — 2024) |
| Classical signing | Ed25519 — both must verify |
| Replay protection | Redis atomic `SET NX EX`, 24h TTL, consumed at **verify** time |
| Tamper detection | SHA-256 hash-chain + watchdog thread |
| Timing attack resistance | Constant-time comparison inside libsodium (Ed25519) and liboqs (ML-DSA-65). `hmac.compare_digest()` in `rogue_detector.py` where signatures are compared against the ledger. |
| Rate limiting | 30/min on `/sign`, 60/min elsewhere |
| Command expiry | TTL 120s — expired commands rejected |
| CORS | Configured for FE-2 dashboard |

---

## Requirements

### Raspberry Pi 4 — Production

**System packages:**
```bash
sudo apt update
sudo apt install -y git python3 python3-pip redis-server cmake ninja-build libssl-dev
sudo systemctl enable redis-server
sudo systemctl start redis-server
redis-cli ping   # Must return PONG
```

**liboqs — ML-DSA-65 (build from source):**
```bash
git clone --depth 1 https://github.com/open-quantum-safe/liboqs.git
cd liboqs && mkdir build && cd build
cmake -GNinja -DCMAKE_INSTALL_PREFIX=/usr/local ..
ninja && sudo ninja install
cd ../..

git clone --depth 1 https://github.com/open-quantum-safe/liboqs-python.git
cd liboqs-python
pip install . --break-system-packages
```

**Python packages:**
```bash
source ~/venv/bin/activate
pip install fastapi uvicorn pynacl redis slowapi httpx
```

**Verify:**
```bash
redis-cli ping
python3 -c "import oqs; print('ML-DSA-65' in oqs.get_enabled_sig_mechanisms())"
```

---

### Post-quantum dependency (liboqs) — is it actually required?

**Yes. In production, real `liboqs-python` is mandatory.** `mock_oqs_nacl.py` is a
development fallback for machines where liboqs is not built, and it is **not
secure**. This section exists because that distinction was previously
undocumented, and the failure mode is silent.

| Package | How it installs | Status | Mockable? |
|---|---|---|---|
| `pynacl` (Ed25519) | `pip install pynacl` — wheels on PyPI | **Hard requirement.** Now in `requirements.txt`. | No reason to. Never rely on the shim for this. |
| `redis` | `pip install redis` + a running redis-server | **Hard requirement.** Now in `requirements.txt`. | No |
| `slowapi` | `pip install slowapi` | **Hard requirement.** Now in `requirements.txt`. | No |
| `liboqs-python` (ML-DSA-65) | Build liboqs from source, then install the Python binding — **not on PyPI** | **Required in production.** Cannot be added to `requirements.txt`. | Dev only, see warning below |

#### Why the shim cannot be the production path

`mock_oqs_nacl.py` installs fake `oqs` and `nacl` modules into `sys.modules` when
the real ones are missing. Its verification functions are unconditional passes:

```python
# mock_oqs_nacl.py:34  — mock oqs.Signature.verify
return sig.startswith(expected_prefix) or sig.startswith(b"MOCK_") or b"MOCK" in sig

# mock_oqs_nacl.py:59  — mock nacl VerifyKey.verify
if not sig.startswith(expected_prefix) and not sig.startswith(b"MOCK_") and not b"MOCK" in sig:
    raise BadSignatureError("Bad signature")
```

**Any byte string containing `MOCK` verifies successfully, under both algorithms.**

The consequence is worse than a disabled check, because nothing downstream can
tell. With the shim active:

- `sign_command()` returns a signature the shim will always accept
- CY-1 `/crypto/verify` returns `valid: true`
- CY-1 `/crypto/health` returns `status: ok`, `self_test_passed: true` — the
  self-test signs and verifies through the same shim, so it always passes
- `/system/links` on Pi #1 reports **crypto: connected**
- the recovery agent's verification gate passes
- the ledger records the command as signed

Every indicator in the system reads green while no cryptography has occurred.
The only signal is a `[CRYPTO MOCK] liboqs not found` line on stdout at import
time, which is invisible once the service is running under a process manager.

Note also that Pi #1's own `/crypto/verify` rejects signatures whose hex begins
with `MOCK_` — that guard does **not** catch shim-produced signatures, because
they are hex-encoded before transmission and so no longer carry the prefix.

#### Rules

1. **Never demo or deploy on the shim.** Verify with the command above; it must
   print `True`. If it prints `False` or raises, ML-DSA-65 is not available and
   any post-quantum claim is unsupported.
2. Treat a `[CRYPTO MOCK]` line in the logs as a failed start, not a warning.
3. If the shim is retained for offline development, it must be made loud —
   `/crypto/health` should report `mock_crypto: true` and `/system/links` should
   surface it, so a mocked signer can never present as a real one. Tracked as
   part of Prompt 4.0 in `docs/PHASE_PROMPTS_FINAL.md`.

---

### Windows — Testing Only

> ⚠️ Windows is for development/testing only. Production runs on Raspberry Pi 4.

**Redis:**
```
Download: https://github.com/microsoftarchive/redis/releases
Run: redis-server.exe
Test: redis-cli.exe ping   # Must return PONG
```

**Python packages:**
```bash
pip install fastapi uvicorn pynacl redis slowapi httpx
```

**liboqs:**
```
Download pre-built wheel:
https://github.com/open-quantum-safe/liboqs-python/releases
pip install liboqs_python-*.whl
```

---

## How to Run

### Raspberry Pi 4

```bash
# Step 1 — Activate venv
source ~/venv/bin/activate

# Step 2 — Go to crypto folder
cd ~/DEADSAT/crypto

# Step 3 — Start server
python3 crypto_routes.py
```

Server: `http://0.0.0.0:8001`

---

### Windows

```bash
# Step 1 — Start Redis
redis-server.exe

# Step 2 — Go to crypto folder
cd DEADSAT-RESURRECTION/crypto

# Step 3 — Start server
python3 crypto_routes.py
```

Server: `http://localhost:8001`

---

## API Endpoints

| Method | Endpoint | Input | Output |
|---|---|---|---|
| POST | `/crypto/sign` | `{ command_bytes: hex }` | `{ ml_dsa_sig, ed25519_sig, nonce, valid_until, ledger_id, key_fingerprint }` |
| POST | `/crypto/verify` | `{ command_hex, ml_dsa_sig_hex, ed25519_sig_hex, valid_until }` | `{ valid, reason, ed25519_ok, ml_dsa_ok, expired }` |
| POST | `/crypto/check-command` | `{ command_hex, ml_dsa_sig_hex, ed25519_sig_hex, nonce }` | `{ valid, alert_type, severity, message }` |
| GET | `/crypto/health` | — | `{ status, self_test_passed, uptime_seconds, key_fingerprint }` |
| GET | `/crypto/metrics` | — | `{ sign_count, verify_count, alerts_by_type, ledger_entries, watchdog_ok }` |
| GET | `/crypto/ledger` | — | `[ { id, timestamp, cmd_hash, nonce, prev_hash, ... } ]` |
| GET | `/crypto/alerts` | — | `[ { id, timestamp, alert_type, detail, severity } ]` |

---

## Quick Test

```bash
# Health check
curl http://localhost:8001/crypto/health

# Sign a command
curl -X POST http://localhost:8001/crypto/sign \
  -H 'Content-Type: application/json' \
  -d '{"command_bytes": "4144435f4d454d4f5259"}'

# Verify a command
curl -X POST http://localhost:8001/crypto/verify \
  -H 'Content-Type: application/json' \
  -d '{"command_hex": "...", "ml_dsa_sig_hex": "...", "ed25519_sig_hex": "...", "valid_until": 1234567890}'

# Metrics
curl http://localhost:8001/crypto/metrics
```

---

## Alert Types (rogue_detector.py)

| Alert | Severity | Meaning |
|---|---|---|
| `UNSIGNED_COMMAND` | 🔴 Critical | Command arrived with no signature |
| `SIGNATURE_MISMATCH` | 🔴 Critical | Signature does not match ledger |
| `REPLAY_ATTACK` | 🔴 Critical | Nonce already used |
| `UNKNOWN_COMMAND` | 🟡 Medium | Command not found in ledger |

---

## Integration with Other Members

### AI-2 Recovery Agent

Use `/crypto/sign` to sign each recovery command:

```python
# In recovery_agent.py
SIGNING_ENDPOINT = "http://10.36.220.90:8001/crypto/sign"

# Sign each command
response = httpx.post(SIGNING_ENDPOINT, json={"command_bytes": cmd_hex})
result = response.json()
# result contains: ml_dsa_sig, ed25519_sig, nonce, ledger_id
```

Use `/crypto/verify` (NOT `/crypto/check-command`) for normal execution verification:

```python
# Verify before execution
POST http://10.36.220.90:8001/crypto/verify
Body: { command_hex, ml_dsa_sig_hex, ed25519_sig_hex, valid_until }
```

### FE-2 Dashboard

```javascript
// Health + key fingerprint
GET http://10.36.220.90:8001/crypto/health

// Live ledger
GET http://10.36.220.90:8001/crypto/ledger

// Security alerts
GET http://10.36.220.90:8001/crypto/alerts

// Metrics
GET http://10.36.220.90:8001/crypto/metrics
```

---

## Pi Network Info

```
Pi #1 IP   : 10.36.220.90
Crypto port: 8001
Health URL : http://10.36.220.90:8001/crypto/health
```

---

## Watchdog Demo

The ledger watchdog fires every 10 seconds. To trigger a CRITICAL alert live on stage:

```bash
# Open SQLite and corrupt a record
sqlite3 ~/DEADSAT/crypto/ledger.db
UPDATE commands SET command_hash = 'TAMPERED' WHERE id = 1;
.quit
# Within 10 seconds — CRITICAL alert fires on terminal
```

---

> **DeadSat Resurrection — FAR AWAY 2026**  
> *Recovering Satellites in Seconds, Not Days* 🛰️🔐
