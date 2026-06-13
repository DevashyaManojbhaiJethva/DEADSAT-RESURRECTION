import oqs
import nacl.signing
import hashlib
import sqlite3
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ML-DSA-65 is the NIST 2024 official name for CRYSTALS-Dilithium3
ALGORITHM = "ML-DSA-65"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ML_DSA_PUBLIC_PATH = os.path.join(BASE_DIR, "ml_dsa_public.bin")
ED25519_PUBLIC_PATH = os.path.join(BASE_DIR, "ed25519_public.bin")


def generate_keypair() -> dict:
    global ALGORITHM
    try:
        with oqs.Signature(ALGORITHM) as signer:
            ml_dsa_public = signer.generate_keypair()
            ml_dsa_secret = signer.export_secret_key()
        print(f"\033[92m[OK] ML-DSA-65 keypair generated: {len(ml_dsa_public)} bytes public, {len(ml_dsa_secret)} bytes private\033[0m")
        logger.info(f"ML-DSA-65 public key: {len(ml_dsa_public)} bytes")

    except oqs.MechanismNotSupportedError:
        print(f"\033[93m[WARN] ML-DSA-65 not supported — trying Dilithium3\033[0m")
        ALGORITHM = "Dilithium3"
        with oqs.Signature(ALGORITHM) as signer:
            ml_dsa_public = signer.generate_keypair()
            ml_dsa_secret = signer.export_secret_key()
        print(f"\033[92m[OK] Dilithium3 keypair generated: {len(ml_dsa_public)} bytes public\033[0m")

    # Ed25519 keypair via PyNaCl — classical half of hybrid
    ed25519_signing_key = nacl.signing.SigningKey.generate()
    ed25519_verify_key = ed25519_signing_key.verify_key
    ed25519_public_bytes = bytes(ed25519_verify_key)
    print(f"\033[92m[OK] Ed25519 keypair generated: {len(ed25519_public_bytes)} bytes public, {len(bytes(ed25519_signing_key))} bytes signing key\033[0m")
    logger.info(f"Ed25519 public key: {len(ed25519_public_bytes)} bytes")

    # Save both public keys to disk
    with open(ML_DSA_PUBLIC_PATH, "wb") as f:
        f.write(ml_dsa_public)
    with open(ED25519_PUBLIC_PATH, "wb") as f:
        f.write(ed25519_public_bytes)
    print(f"\033[92m[OK] ml_dsa_public.bin saved: {ML_DSA_PUBLIC_PATH}\033[0m")
    print(f"\033[92m[OK] ed25519_public.bin saved: {ED25519_PUBLIC_PATH}\033[0m")

    # Key fingerprint — SSH-style SHA-256 of both public keys
    key_fingerprint = hashlib.sha256(ml_dsa_public + ed25519_public_bytes).hexdigest()[:16]
    print(f"\033[92m[OK] Key fingerprint: {key_fingerprint}\033[0m")
    logger.info(f"Key fingerprint: {key_fingerprint}")

    return {
        "ml_dsa_public": ml_dsa_public,
        "ml_dsa_secret": ml_dsa_secret,
        "ed25519_signing_key": ed25519_signing_key,
        "ed25519_verify_key": ed25519_verify_key,
        "key_fingerprint": key_fingerprint
    }


def startup_self_test():
    try:
        print(f"\033[93m[WARN] Running startup self-test...\033[0m")

        keys = generate_keypair()
        test_message = b"DEADSAT_SELF_TEST_MESSAGE"

        # Test ML-DSA-65 sign + verify
        with oqs.Signature(ALGORITHM, keys["ml_dsa_secret"]) as signer:
            ml_dsa_sig = signer.sign(test_message)
        with oqs.Signature(ALGORITHM) as verifier:
            ml_dsa_ok = verifier.verify(test_message, ml_dsa_sig, keys["ml_dsa_public"])
        if not ml_dsa_ok:
            raise Exception("ML-DSA-65 self-test verify failed")

        # Test Ed25519 sign + verify
        signed = keys["ed25519_signing_key"].sign(test_message)
        keys["ed25519_verify_key"].verify(test_message, signed.signature)

        # Test ledger write + read
        test_db = os.path.join(BASE_DIR, "test_selftest.db")
        conn = sqlite3.connect(test_db)
        conn.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, data TEXT)")
        conn.execute("INSERT INTO test (data) VALUES (?)", ("self_test_entry",))
        conn.commit()
        row = conn.execute("SELECT data FROM test LIMIT 1").fetchone()
        conn.close()
        os.remove(test_db)
        if row[0] != "self_test_entry":
            raise Exception("Ledger self-test read failed")

        print(f"\033[92m[OK] SYSTEM SELF-CHECK: ALL PASS\033[0m")
        logger.info("Startup self-test passed")
        return True

    except Exception as e:
        print(f"\033[91m[FAIL] SELF-CHECK FAILED: {e}\033[0m")
        logger.error(f"Startup self-test failed: {e}")
        return False


if __name__ == "__main__":
    print("=" * 55)
    print("  DeadSat Resurrection — CY-1 Hybrid Crypto Layer")
    print("  keygen.py — ML-DSA-65 + Ed25519 Keypair Generation")
    print("=" * 55)

    startup_self_test()

    print("\nGenerating production keypair...")
    keys = generate_keypair()

    print("=" * 55)
    print(f"ML-DSA-65 public key : {len(keys['ml_dsa_public'])} bytes")
    print(f"ML-DSA-65 private key: {len(keys['ml_dsa_secret'])} bytes")
    print(f"Ed25519 public key   : {len(bytes(keys['ed25519_verify_key']))} bytes")
    print(f"Ed25519 signing key  : {len(bytes(keys['ed25519_signing_key']))} bytes")
    print(f"Key fingerprint      : {keys['key_fingerprint']}")
    print(f"Private keys saved   : NO (memory only)")
    print("=" * 55)
