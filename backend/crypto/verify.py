import mock_oqs_nacl
import oqs
import nacl.signing
import nacl.exceptions
import time
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ML-DSA-65 is the NIST 2024 official name for CRYSTALS-Dilithium3
#
# BUG E: this file claimed "Uses hmac.compare_digest() to prevent timing
# attacks", both here and in verify_command's docstring, and imported hmac to
# say so — but compare_digest was never called anywhere in the module. The
# claim was decoration.
#
# The honest position, which is also the correct one: we never compare
# signatures ourselves. Verification is delegated to primitives that are
# already constant-time by construction — PyNaCl's Ed25519 verify (libsodium's
# crypto_sign_verify_detached) and liboqs' ML-DSA-65 verify. Adding a
# compare_digest() over their boolean results would protect nothing. The
# unused import has been removed rather than left implying a defence that was
# not present.
ALGORITHM = "ML-DSA-65"


def verify_command(
    command_hex: str,
    ml_dsa_sig_hex: str,
    ed25519_sig_hex: str,
    ml_dsa_public: bytes,
    ed25519_verify_key,
    valid_until: int
) -> dict:
    """
    Verify hybrid satellite command — BOTH signatures must pass.
    Also checks TTL — expired commands rejected even with valid signatures.
    Ed25519  — classical verification
    ML-DSA-65 — post-quantum verification
    Signature comparison is constant-time by construction: it is performed
    inside PyNaCl (libsodium) and liboqs, not here. See the note at the top of
    this module — an earlier version of this docstring claimed a
    hmac.compare_digest() that was never called.
    """
    checked_at = int(time.time())

    # Check 1 — TTL expiry
    # Command expired — reject even if signatures are valid
    if checked_at > valid_until:
        print(f"\033[91m[FAIL] COMMAND_EXPIRED — TTL window passed\033[0m")
        logger.warning(f"Command expired at {valid_until}, now {checked_at}")
        return {
            "valid": False,
            "reason": "COMMAND_EXPIRED",
            "algorithm": ALGORITHM,
            "ed25519_ok": False,
            "ml_dsa_ok": False,
            "expired": True,
            "checked_at": checked_at
        }

    try:
        command_bytes = bytes.fromhex(command_hex)
        ml_dsa_sig = bytes.fromhex(ml_dsa_sig_hex)
        ed25519_sig = bytes.fromhex(ed25519_sig_hex)
    except ValueError as e:
        print(f"\033[91m[FAIL] Invalid hex input: {e}\033[0m")
        return {
            "valid": False,
            "reason": f"INVALID_HEX: {e}",
            "algorithm": ALGORITHM,
            "ed25519_ok": False,
            "ml_dsa_ok": False,
            "expired": False,
            "checked_at": checked_at
        }

    # Check 2 — Ed25519 verification
    # Classical signature — fast, battle-tested
    ed25519_ok = False
    try:
        # Verify Ed25519 signature
        ed25519_verify_key.verify(command_bytes, ed25519_sig)
        # PyNaCl raised nothing, so the signature is good. The comparison
        # happened inside libsodium in constant time; there is nothing to
        # re-check here.
        ed25519_ok = True
        print(f"\033[92m[OK] Ed25519 signature valid\033[0m")
        logger.info("Ed25519 verification passed")
    except nacl.exceptions.BadSignatureError:
        print(f"\033[91m[FAIL] ED25519_FAIL — classical signature tampered\033[0m")
        logger.warning("Ed25519 verification failed")
        return {
            "valid": False,
            "reason": "ED25519_FAIL",
            "algorithm": ALGORITHM,
            "ed25519_ok": False,
            "ml_dsa_ok": False,
            "expired": False,
            "checked_at": checked_at
        }

    # Check 3 — ML-DSA-65 verification
    # Post-quantum signature — quantum computer cannot break
    ml_dsa_ok = False
    try:
        with oqs.Signature(ALGORITHM) as verifier:
            try:
                ml_dsa_ok = verifier.verify(command_bytes, ml_dsa_sig, ml_dsa_public)
            except Exception:
                ml_dsa_ok = False

        if not ml_dsa_ok:
            print(f"\033[91m[FAIL] ML_DSA_FAIL — post-quantum signature tampered\033[0m")
            logger.warning("ML-DSA-65 verification failed")
            return {
                "valid": False,
                "reason": "ML_DSA_FAIL",
                "algorithm": ALGORITHM,
                "ed25519_ok": True,
                "ml_dsa_ok": False,
                "expired": False,
                "checked_at": checked_at
            }

        print(f"\033[92m[OK] ML-DSA-65 signature valid\033[0m")
        logger.info("ML-DSA-65 verification passed")

    except oqs.MechanismNotSupportedError as exc:
        # BUG D: this was `sys.exit(1)`. A library function must not kill the
        # process that imported it — inside the API that terminated uvicorn
        # mid-request, taking the emulator, the recovery agent and every
        # WebSocket client down with it, and leaving no HTTP response to
        # explain why. Raise so the caller can turn it into a 503.
        print(f"\033[91m[FAIL] {ALGORITHM} not supported — check liboqs build\033[0m")
        logger.error("%s unavailable in this liboqs build: %s", ALGORITHM, exc)
        raise RuntimeError(
            f"{ALGORITHM} is not supported by the installed liboqs build. "
            f"Rebuild liboqs with ML-DSA-65 enabled, or run with the "
            f"development shim. Original error: {exc}"
        ) from exc

    # ── WIRING: mock-crypto gate ──────────────────────────────────────
    # Both signatures "passed", but if mock_oqs_nacl is active that means
    # nothing: its verifiers accept any byte string containing b"MOCK"
    # (mock_oqs_nacl.py lines 34 and 59). Certifying such a command as
    # valid would let fake cryptography present as real all the way to the
    # recovery agent's verification gate and into the ledger.
    #
    # Fail closed. Opt out only via DEADSAT_ALLOW_MOCK_SIGNING=1, which is
    # the same flag config.ALLOW_MOCK_SIGNING already defines and already
    # defaults to off — honoured here so bench development still works,
    # but the result stays clearly labelled as mock either way.
    if mock_oqs_nacl.is_mock_active():
        allow_mock = os.environ.get(
            "DEADSAT_ALLOW_MOCK_SIGNING", ""
        ).strip().lower() in ("1", "true", "yes", "on")

        if not allow_mock:
            print(f"\033[91m[FAIL] MOCK_CRYPTO_NOT_VERIFIABLE — "
                  f"{mock_oqs_nacl.mock_detail()}\033[0m")
            logger.error("Refusing to certify command: %s",
                         mock_oqs_nacl.mock_detail())
            return {
                "valid": False,
                "reason": "MOCK_CRYPTO_NOT_VERIFIABLE",
                "algorithm": ALGORITHM,
                "ed25519_ok": ed25519_ok,
                "ml_dsa_ok": ml_dsa_ok,
                "expired": False,
                "mock": True,
                "mock_detail": mock_oqs_nacl.mock_detail(),
                "message": ("Signatures were checked by a development shim, "
                            "not by real cryptography. Install liboqs/PyNaCl "
                            "or set DEADSAT_ALLOW_MOCK_SIGNING=1 to accept "
                            "unverified commands on a bench."),
                "checked_at": checked_at
            }

        print(f"\033[93m[WARN] MOCK crypto accepted via "
              f"DEADSAT_ALLOW_MOCK_SIGNING — {mock_oqs_nacl.mock_detail()}\033[0m")
        logger.warning("Mock crypto explicitly allowed: %s",
                       mock_oqs_nacl.mock_detail())

    # Both signatures valid + TTL not expired
    print(f"\033[92m[OK] HYBRID_SIGNATURES_VALID — ed25519=True, ml_dsa=True, expired=False\033[0m")
    logger.info("Hybrid verification passed")

    return {
        "valid": True,
        "reason": "HYBRID_SIGNATURES_VALID",
        "algorithm": ALGORITHM,
        "ed25519_ok": True,
        "ml_dsa_ok": True,
        "expired": False,
        "mock": mock_oqs_nacl.is_mock_active(),
        "mock_detail": mock_oqs_nacl.mock_detail(),
        "checked_at": checked_at
    }


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from keygen import generate_keypair
    from sign import sign_command

    print("Generating keypair for test...")
    keys = generate_keypair()

    test_command = b"ADCS_MEMORY_SCRUB_v2"
    signed = sign_command(
        command_bytes=test_command,
        ml_dsa_secret=keys["ml_dsa_secret"],
        ed25519_signing_key=keys["ed25519_signing_key"]
    )

    print("\n--- Test 1: Valid hybrid ---")
    result = verify_command(
        command_hex=signed["command"],
        ml_dsa_sig_hex=signed["ml_dsa_signature"],
        ed25519_sig_hex=signed["ed25519_signature"],
        ml_dsa_public=keys["ml_dsa_public"],
        ed25519_verify_key=keys["ed25519_verify_key"],
        valid_until=signed["valid_until"]
    )
    print(f"Result: {result}\n")

    print("--- Test 2: Expired command ---")
    result = verify_command(
        command_hex=signed["command"],
        ml_dsa_sig_hex=signed["ml_dsa_signature"],
        ed25519_sig_hex=signed["ed25519_signature"],
        ml_dsa_public=keys["ml_dsa_public"],
        ed25519_verify_key=keys["ed25519_verify_key"],
        valid_until=int(time.time()) - 1
    )
    print(f"Result: {result}\n")

    print("--- Test 3: Ed25519 tampered ---")
    result = verify_command(
        command_hex=signed["command"],
        ml_dsa_sig_hex=signed["ml_dsa_signature"],
        ed25519_sig_hex="aa" * 64,
        ml_dsa_public=keys["ml_dsa_public"],
        ed25519_verify_key=keys["ed25519_verify_key"],
        valid_until=signed["valid_until"]
    )
    print(f"Result: {result}\n")

    print("--- Test 4: ML-DSA-65 tampered ---")
    result = verify_command(
        command_hex=signed["command"],
        ml_dsa_sig_hex="bb" * 3309,
        ed25519_sig_hex=signed["ed25519_signature"],
        ml_dsa_public=keys["ml_dsa_public"],
        ed25519_verify_key=keys["ed25519_verify_key"],
        valid_until=signed["valid_until"]
    )
    print(f"Result: {result}\n")
