import mock_oqs_nacl
import oqs
import nacl.signing
import secrets
import time
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ML-DSA-65 is the NIST 2024 official name for CRYSTALS-Dilithium3
# TTL — every command expires 120 seconds after signing
# Prevents old signed commands from being executed later
ALGORITHM = "ML-DSA-65"
TTL_SECONDS = 120


def sign_command(command_bytes: bytes, ml_dsa_secret: bytes, ed25519_signing_key) -> dict:
    """
    Hybrid-sign a satellite command with BOTH Ed25519 AND ML-DSA-65.
    Command is only valid if BOTH signatures verify.
    Ed25519  — classical, fast, battle-tested
    ML-DSA-65 — post-quantum, quantum computer cannot break
    Returns dict with both signatures, nonce, timestamp, TTL.
    """
    try:
        # Ed25519 sign — classical half
        ed25519_sig = ed25519_signing_key.sign(command_bytes).signature

        # ML-DSA-65 sign — post-quantum half
        with oqs.Signature(ALGORITHM, ml_dsa_secret) as signer:
            ml_dsa_sig = signer.sign(command_bytes)

        signed_at = int(time.time())
        valid_until = signed_at + TTL_SECONDS

        result = {
            "command": command_bytes.hex(),
            "ml_dsa_signature": ml_dsa_sig.hex(),
            "ed25519_signature": ed25519_sig.hex(),
            "algorithm": ALGORITHM,
            "nonce": secrets.token_hex(32),
            "signed_at": signed_at,
            "valid_until": valid_until
        }

        print(f"\033[92m[SIGNED] cmd={command_bytes.hex()[:16]}... ml_dsa_sig={len(ml_dsa_sig)} bytes, ed25519_sig={len(ed25519_sig)} bytes\033[0m")
        print(f"\033[92m[OK] TTL window: {TTL_SECONDS} seconds from now\033[0m")
        logger.info(f"Command signed: nonce={result['nonce'][:8]}... valid_until={valid_until}")

        return result

    except oqs.MechanismNotSupportedError:
        print(f"\033[91m[FAIL] {ALGORITHM} not supported — check liboqs build\033[0m")
        sys.exit(1)
    except Exception as e:
        print(f"\033[91m[FAIL] Signing failed: {e}\033[0m")
        logger.error(f"Signing failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from keygen import generate_keypair

    print("Generating keypair for test...")
    keys = generate_keypair()

    # Test command — simulating real satellite recovery command
    test_command = b"ADCS_MEMORY_SCRUB_v2"
    print(f"\nSigning test command: {test_command}")

    result = sign_command(
        command_bytes=test_command,
        ml_dsa_secret=keys["ml_dsa_secret"],
        ed25519_signing_key=keys["ed25519_signing_key"]
    )

    print(f"\nml_dsa_signature  : {len(bytes.fromhex(result['ml_dsa_signature']))} bytes")
    print(f"ed25519_signature : {len(bytes.fromhex(result['ed25519_signature']))} bytes")
    print(f"algorithm         : {result['algorithm']}")
    print(f"nonce             : {result['nonce']}")
    print(f"signed_at         : {result['signed_at']}")
    print(f"valid_until       : {result['valid_until']}")
    print(f"TTL               : {result['valid_until'] - result['signed_at']} seconds")
