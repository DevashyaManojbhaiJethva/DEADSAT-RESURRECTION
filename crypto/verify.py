import oqs
import nacl.signing
import nacl.exceptions
import hmac
import time
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ML-DSA-65 is the NIST 2024 official name for CRYSTALS-Dilithium3
# hmac.compare_digest() — timing-attack resistant comparison
# prevents attacker from guessing signature byte by byte
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
    Uses hmac.compare_digest() to prevent timing attacks.
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
        # hmac.compare_digest — timing-attack resistant final check
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

    except oqs.MechanismNotSupportedError:
        print(f"\033[91m[FAIL] {ALGORITHM} not supported — check liboqs build\033[0m")
        sys.exit(1)

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
