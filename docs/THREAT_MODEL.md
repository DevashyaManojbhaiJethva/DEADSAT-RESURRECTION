# Threat Model — DeadSat Resurrection

**Scope:** authenticated command uplink to a satellite whose ground segment is
two Raspberry Pis and an operator browser.
**Status:** written 2026-08-16, against the code in this repository.
**Audience:** anyone deciding whether to trust a command this system emits.

Every mitigation below names the **file and function** that implements it, so
each claim can be checked in under a minute. Anything not yet implemented is
marked **FUTURE WORK** and is not counted as a mitigation.
`docs/verify_threat_model.py` mechanically checks that every citation in this
file resolves to a symbol that exists.

---

## 1. What this system is defending

One asset: **the authority to execute a command on the spacecraft.**

Everything else — telemetry confidentiality, dashboard availability, the ledger
— matters only insofar as it protects or evidences that authority. A recovery
system that can be made to uplink an attacker's command is worse than no
recovery system, because it converts a stuck satellite into a controlled one.

---

## 2. Adversaries

### A1 — Passive RF eavesdropper
Receives the 137.9 MHz downlink with a £20 RTL-SDR. Sees telemetry and any
command echoed in the clear. Cannot transmit.

**Capability:** read everything on the air.
**Goal:** learn command formats and timing; capture a signed command for later
replay (feeds A3 and A4).

This adversary is *assumed successful*. The downlink is not encrypted and this
project does not claim it is. Telemetry is treated as public.

### A2 — Active uplink forger
Has a transmitter and can reach the spacecraft's receiver. Composes arbitrary
command bytes and transmits them.

**Capability:** inject any bit pattern at the right frequency.
**Goal:** execute a command the operator did not authorise — a spurious
`SAFE_MODE_HOLD`, a fake `ADCS_WHEEL_RESTART_v1`, or anything worse on real
hardware.

This is the adversary the entire signing layer exists for.

### A3 — Replay attacker
A special case of A2 with a recording. Does not need to forge anything: takes a
command that was genuinely signed, and transmits it again later.

**Capability:** capture and retransmit verbatim.
**Goal:** re-execute a legitimate command at a moment of their choosing — for
example replaying a `POWER_SAFE_MODE_v1` every time the satellite recovers,
holding it in safe mode indefinitely.

Replay defeats signature checking entirely, because the signature is valid.
It is stopped by freshness, not by cryptography.

### A4 — Future quantum-capable attacker (harvest-now, decrypt-later)
Records signed commands today. Runs Shor's algorithm on a
cryptographically-relevant quantum computer at some point in the satellite's
operational life.

**Capability:** recover an Ed25519 private key from a public key or signature,
retroactively.
**Goal:** forge commands that verify against a key the ground segment still
trusts.

The relevant property is that **satellite key rotation is expensive and slow**.
A key uploaded today may still be the trust anchor in fifteen years. Classical
signatures alone assume no quantum computer arrives within the mission — an
assumption nobody can make honestly.

---

## 3. Trust boundaries

```
 [Operator browser]  --(1)--  [Pi #1 API]  --(2)--  [CY-1 signer]
                                  |
                                 (3)
                                  |
                            [Pi #2 RF station]
                                  |
                                 (4)  RF uplink — HOSTILE
                                  |
                            [ Spacecraft ]
```

| # | Boundary | Crosses | Assumption |
|---|---|---|---|
| 1 | Browser → Pi #1 | REST + WebSocket over LAN | LAN is **semi-trusted**: authenticated with a shared secret, not encrypted |
| 2 | Pi #1 → CY-1 | in-process function calls (see §6) | Same process, same trust domain |
| 3 | Pi #1 → Pi #2 | HTTP over LAN | Semi-trusted; RF station holds no keys |
| 4 | Pi #2 → spacecraft | radio | **Fully hostile.** Anyone can transmit and receive |

Boundary 4 is the only one where an adversary is assumed present by default.
Boundaries 1–3 are on a private network; the shared secret exists so that
"private network" does not have to mean "trusted network".

**The signing key never crosses boundary 3 or 4.** Commands are signed on Pi #1
and only the signature travels — `node_request_signing` in
`agents/recovery_agent.py` posts command bytes to the signer and receives
signatures back.

---

## 4. In scope

### T1 — Uplink command forgery (adversary A2)

An attacker transmits a command the operator never authorised.

| Mitigation | Where |
|---|---|
| Every command carries **two** signatures, both required | `sign_command` in `crypto/sign.py` |
| Both are checked before a command is accepted; either failing rejects it | `verify_command` in `crypto/verify.py` |
| The recovery agent refuses to uplink an unverified command | `_verify_command` and `node_uplink_commands` in `agents/recovery_agent.py` |
| The verification gate is on by default | `REQUIRE_COMMAND_VERIFICATION` in `config.py` |
| Signatures cannot be issued when the crypto backend is fake | `_mock_signing_allowed` and `sign` in `crypto/crypto_routes.py` |

The gate is *fail-closed*: an unreachable or unverifiable signer aborts the
uplink rather than proceeding. That was not always true — see §7.1.

### T2 — Replay (adversary A3)

An attacker retransmits a command that was legitimately signed.

| Mitigation | Where |
|---|---|
| Each signed command carries a single-use nonce | `generate_nonce` and `use_nonce` in `crypto/nonce.py` |
| The nonce is claimed **atomically**, so two concurrent presentations cannot both win | `use_nonce` in `crypto/nonce.py` (Redis `SET NX EX`) |
| The claim happens at **verification** time, not signing time | `verify` in `crypto/crypto_routes.py` |
| Replays are reported distinctly, not as a generic failure | `verify` in `crypto/crypto_routes.py` returns `REPLAYED_NONCE` |
| Commands expire, bounding the replay window even if the nonce store is lost | `TTL_SECONDS` in `crypto/sign.py` (120 s) |
| Expiry is enforced before signatures are even checked | `verify_command` in `crypto/verify.py` |
| A duplicate command hash against the ledger raises an alert | `check_command` in `crypto/rogue_detector.py` |

Two details that matter more than they look:

- **The nonce is consumed by the verifier, not the signer.** Consuming it at
  signing time only ever caught our own duplicate signing calls; a replayed
  command goes straight to the verifier and never passes through the signer at
  all. It was on the wrong side of the boundary.
- **The nonce is only claimed if the signatures already verified.** Otherwise
  an attacker could burn a legitimate nonce by submitting garbage under it,
  denying the real command its single use.

### T3 — Signature downgrade

An attacker strips or weakens one of the two signatures — presenting an
Ed25519-only command to a verifier that accepts it, or substituting a
placeholder that some code path treats as valid.

| Mitigation | Where |
|---|---|
| **Both** signatures must verify; there is no single-algorithm accept path | `verify_command` in `crypto/verify.py` |
| A missing signature field is rejected outright | `_verify_command` in `agents/recovery_agent.py` |
| Signatures produced by the development shim are refused, not trusted | `is_mock_active` and `mock_detail` in `crypto/mock_oqs_nacl.py`, checked in `verify_command` |
| The signer refuses to issue shim signatures at all by default | `sign` in `crypto/crypto_routes.py` (503 `SIGNING_UNAVAILABLE`) |
| Mock mode is visible, not silent | `health` in `crypto/crypto_routes.py` reports `mock_crypto` |

### T4 — Stale-command execution

A command signed hours ago is transmitted when the situation has changed — the
satellite has already recovered, or moved to a different fault.

| Mitigation | Where |
|---|---|
| 120-second TTL on every signature | `TTL_SECONDS` in `crypto/sign.py` |
| Expiry checked first, before signature verification | `verify_command` in `crypto/verify.py` |
| Commands are re-signed immediately before transmission if the TTL has nearly elapsed | `_refresh_expiring_signatures` in `agents/recovery_agent.py` |

The re-signing step exists because the agent signs at planning time but may
wait for a ground-contact window before transmitting — potentially hours. A
long TTL would have been the easy fix and the wrong one: it widens the replay
window on a command that authorises a spacecraft action. Re-signing keeps the
TTL short.

### T5 — Unauthorised access to the ground segment (boundary 1)

An attacker on the LAN drives the API directly, or reads live telemetry.

| Mitigation | Where |
|---|---|
| Mutating REST routes require a shared secret | `require_api_key` in `main.py` |
| WebSocket streams require the same secret | `_ws_authenticate` in `main.py` (closes with 1008) |
| CORS is restricted to declared origins, never `*` | `_cors_origins` in `config.py` |
| A LAN-facing bind with loopback-only origins is flagged at startup | `cors_is_unreachable_from_lan` and `print_banner` in `config.py` |
| Whether the supplied key was accepted is reportable | `system_links` in `main.py` |
| Signing and verification are rate limited | `sign` and `verify` in `crypto/crypto_routes.py` |

### T6 — Tampering with the command record

An attacker with write access to the ledger edits history to hide a command.

| Mitigation | Where |
|---|---|
| The ledger is a SHA-256 hash chain | `add_entry` in `crypto/ledger.py` |
| Chain integrity is verified continuously | `verify_chain` and `start_watchdog` in `crypto/ledger.py` |
| A broken chain raises an alert rather than failing silently | `_store_alert` in `crypto/ledger.py` |

---

## 5. Out of scope

Stated so that nobody mistakes silence for coverage.

| Threat | Why excluded |
|---|---|
| **Physical access to Pi #1 or Pi #2** | An attacker holding the hardware has the signing key. No software mitigation exists at that point; this is a physical-security problem |
| **Supply-chain compromise** | A backdoored `liboqs`, `pynacl` or PyPI package defeats everything here. `requirements.lock` pins versions, but pinning a compromised version pins the compromise |
| **RF jamming of the ground station** | Denial of service against the uplink. Signing does not help; the mitigation is redundant ground stations |
| **Downlink confidentiality** | Telemetry is transmitted in the clear and treated as public |
| **Compromise of the operator's browser** | A browser executing attacker code can issue authorised commands. Session security is not modelled |
| **Spacecraft-side key compromise** | Assumed out of reach on the ground |

---

## 6. Why hybrid Ed25519 + ML-DSA-65 rather than either alone

Both signatures are required. Either failing rejects the command
(`verify_command` in `crypto/verify.py`).

**Why not Ed25519 alone.** Ed25519's security rests on the discrete-log problem
on Curve25519, which Shor's algorithm solves in polynomial time. A satellite
commissioned today may still be flying in fifteen years, and its trust anchor
is expensive to rotate. Adversary A4 does not need a quantum computer *now* —
only before end of mission. Recording signed commands today costs nothing.

**Why not ML-DSA-65 alone.** ML-DSA (FIPS 204, standardised 2024) is young.
Lattice schemes have been broken before — SIKE was broken in 2022, after
selection for NIST's alternate round, by a classical attack found in a
weekend. Betting a spacecraft solely on a five-year-old assumption is not
obviously safer than betting it on a thirty-year-old one.

**Hybrid.** An attacker must break *both* an established classical scheme and a
new post-quantum one. The classical half covers a break in the lattice
assumption; the post-quantum half covers a quantum computer. Neither failure
mode alone forges a command. The cost is 3,309 bytes of ML-DSA-65 signature
per command on top of 64 bytes of Ed25519 — irrelevant on a ground link,
non-trivial on a constrained uplink, and worth stating.

This is the same reasoning behind hybrid TLS key exchange (X25519 + ML-KEM):
during a transition, add rather than replace.

---

## 7. Residual risks

These are real and unmitigated. They are listed because a threat model that
only lists wins is marketing.

### 7.1 — The spacecraft does not verify signatures. The ground segment does.

**This is the most important limitation in this document.**

`apply_recovery` in `emulator/satellite_emulator.py` takes a single argument:
a procedure *name*. It never sees a signature, a nonce or a TTL. Verification
happens on the ground, in `node_uplink_commands`
(`agents/recovery_agent.py`), and only then is the emulator told what to do.

So the architecture demonstrated here is:

> *the ground segment refuses to transmit a command it cannot verify*

and **not**:

> *the spacecraft refuses to execute a command it cannot verify*

Against adversary A2 — who transmits directly to the spacecraft and never
touches our ground segment — the entire signing layer is bypassed. On real
hardware the verification must run **on the spacecraft**, with the public keys
held in on-board storage. Everything in §4 T1–T4 would then be the flight
software's job, not the agent's.

What this project genuinely demonstrates is the *ground-side* half: command
provenance, an auditable ledger, replay protection, and a gate that fails
closed. That is a real and useful half. It is not the whole.

**FUTURE WORK:** move verification into the emulator, so `apply_recovery`
accepts a signed command envelope and rejects anything that does not verify —
modelling what flight software would do.

### 7.2 — The mock-signing path still exists

`crypto/mock_oqs_nacl.py` substitutes fake `oqs` and `nacl` modules when the
real libraries are missing. Its verifiers accept **any** byte string containing
`MOCK`.

It is fail-closed by default: `sign` in `crypto/crypto_routes.py` returns 503
rather than issuing a shim signature, and `verify_command` in
`crypto/verify.py` refuses to certify one. Both can be overridden with
`DEADSAT_ALLOW_MOCK_SIGNING=1` (`ALLOW_MOCK_SIGNING` in `config.py`), which
exists so bench development works without a liboqs build.

**The residual risk is operational, not cryptographic.** Anyone who sets that
variable to silence a startup error has disabled the project's central
security property, and the only signals are a `[CRYPTO MOCK]` log line and
`mock_crypto: true` from `health` in `crypto/crypto_routes.py`. On a demo
machine that variable is easy to set and easy to forget.

**FUTURE WORK:** refuse to start at all when `DEADSAT_ALLOW_MOCK_SIGNING=1` and
the API is bound to a non-loopback address.

### 7.3 — Replay protection degrades quietly without Redis

`NonceManager` in `crypto/nonce.py` falls back to an in-process dictionary when
Redis is unavailable. That is atomic within one process but gives **no
protection across uvicorn workers or hosts** — two workers hold two independent
nonce stores, and the same nonce is accepted once by each.

It is reported (`health` in `crypto/crypto_routes.py` returns
`nonce_store: "in-memory (NOT replay-safe across workers)"`), not silent. But
a single-worker deployment gives no visible symptom, so the degradation is
easy to carry into a multi-worker one.

**FUTURE WORK:** refuse to start multi-worker without a shared nonce store.

### 7.4 — Keys are generated per process, not persisted

`generate_keypair` in `crypto/keygen.py` creates a fresh hybrid keypair at
startup. Every restart produces a new signing identity, so signatures issued
before a restart cannot be verified after one, and there is no long-lived
public key a spacecraft could hold.

Acceptable for a demonstration; incoherent for flight. A real system pins a
public key on the spacecraft before launch.

**FUTURE WORK:** persist the keypair and add a documented rotation procedure
with an overlap window.

### 7.5 — Contact windows can be computed from synthetic elements

`FALLBACK_TLE` in `emulator/contact_calculator.py` is a **synthetic** element
set, used when CelesTrak is unreachable. Uplink scheduling then targets a
window that does not exist.

Not a confidentiality or integrity risk, but an availability one: commands
scheduled against a fictional pass are transmitted when nothing is listening.
Flagged at load (`load_tle`) and when stale (`warn_if_tle_stale`), both in
`emulator/contact_calculator.py`.

### 7.6 — The TTL assumes synchronised clocks

`verify_command` in `crypto/verify.py` compares `valid_until` against local
time. A ground segment with a skewed clock either rejects valid commands or
accepts expired ones. Nothing here checks clock health.

**FUTURE WORK:** monotonic-clock validation, and reject commands whose
`valid_until` is implausibly far in the future.

### 7.7 — `/crypto/check-command` performs no cryptography

`check_command` in `main.py` returns `valid: false` with an explanatory
message. It once rubber-stamped any non-empty signature string. It is now
honest, but it remains an endpoint whose name implies a check it does not
perform.

**FUTURE WORK:** remove it, or give it the fields needed to call
`verify_command` properly.

---

## 8. Assurance

| Property | Evidence |
|---|---|
| The mitigations above are implemented | `docs/verify_threat_model.py` resolves every citation in this file |
| The gate rejects unverifiable commands | `test_sign_refuses_to_fabricate_when_crypto_is_mocked` in `test_units.py` |
| Concurrent replays cannot both win | `test_concurrent_identical_nonces_exactly_one_succeeds` in `test_units.py` |
| Replays are rejected at verification | `test_nonce_is_consumed_at_verify_not_at_sign` in `test_units.py` |
| The tests can actually fail | `verify_tests_can_fail.py` reverts each fix and asserts its test goes red |

The last row matters most. A suite that passes proves nothing until it has been
watched failing for the right reason.
