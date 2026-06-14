import sys
import types

# Create mock oqs module
try:
    import oqs
except ImportError:
    print("[CRYPTO MOCK] liboqs not found, installing mock oqs wrapper")
    
    oqs_mock = types.ModuleType('oqs')
    
    class MechanismNotSupportedError(Exception):
        pass
        
    class Signature:
        def __init__(self, alg, secret=None):
            self.alg = alg
            self.secret = secret
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
        def generate_keypair(self):
            return b"MOCK_ML_DSA_PUBLIC_KEY_BYTES_32_LENGTH_VAL"
        def export_secret_key(self):
            return b"MOCK_ML_DSA_SECRET_KEY_BYTES_64_LENGTH_VAL"
        def sign(self, msg):
            # Returns a 3309-byte mock signature to match ML-DSA-65 size requirements
            sig = b"MOCK_ML_DSA_SIGNATURE_FOR_" + msg
            return sig.ljust(3309, b"\x00")
        def verify(self, msg, sig, pub):
            # Check prefix or mock verify
            expected_prefix = b"MOCK_ML_DSA_SIGNATURE_FOR_" + msg
            return sig.startswith(expected_prefix) or sig.startswith(b"MOCK_") or b"MOCK" in sig

    oqs_mock.Signature = Signature
    oqs_mock.MechanismNotSupportedError = MechanismNotSupportedError
    sys.modules['oqs'] = oqs_mock

# Create mock nacl module
try:
    import nacl.signing
    import nacl.exceptions
except ImportError:
    print("[CRYPTO MOCK] PyNaCl not found, installing mock nacl wrapper")
    
    nacl_mock = types.ModuleType('nacl')
    nacl_signing_mock = types.ModuleType('nacl.signing')
    nacl_exceptions_mock = types.ModuleType('nacl.exceptions')
    
    class BadSignatureError(Exception):
        pass
        
    class VerifyKey:
        def __init__(self, key_bytes):
            self.key_bytes = key_bytes
        def verify(self, msg, sig):
            expected_prefix = b"MOCK_ED25519_SIGNATURE_FOR_" + msg
            if not sig.startswith(expected_prefix) and not sig.startswith(b"MOCK_") and not b"MOCK" in sig:
                raise BadSignatureError("Bad signature")
            return True
        def __bytes__(self):
            return self.key_bytes
            
    class SigningKey:
        def __init__(self, key_bytes):
            self.key_bytes = key_bytes
            self.verify_key = VerifyKey(b"MOCK_ED25519_PUBLIC_KEY_BYTES_32_LEN")
        @classmethod
        def generate(cls):
            return cls(b"MOCK_ED25519_SECRET_KEY_BYTES_32_LEN")
        def sign(self, msg):
            class SignedMessage:
                signature = (b"MOCK_ED25519_SIGNATURE_FOR_" + msg).ljust(64, b"\x00")
            return SignedMessage()
        def __bytes__(self):
            return self.key_bytes

    nacl_exceptions_mock.BadSignatureError = BadSignatureError
    nacl_signing_mock.SigningKey = SigningKey
    nacl_signing_mock.VerifyKey = VerifyKey
    
    sys.modules['nacl'] = nacl_mock
    sys.modules['nacl.signing'] = nacl_signing_mock
    sys.modules['nacl.exceptions'] = nacl_exceptions_mock
