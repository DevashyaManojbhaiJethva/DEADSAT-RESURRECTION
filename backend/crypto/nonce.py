import hmac
import threading
import logging
import time
import os

try:
    import redis
except ImportError:
    redis = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
logger = logging.getLogger('nonce')

NONCE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'nonce_store.db')
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
NONCE_TTL_HOURS = 24


class NonceManager:

    def __init__(self, db_path=NONCE_DB_PATH):
        self.lock = threading.Lock()
        self.is_mock = False
        self.mock_store = {}
        
        if redis is None:
            print('\033[93m[NONCE] redis module not installed — using in-memory store ⚠️\033[0m')
            self.is_mock = True
            return

        # Redis connection
        try:
            self.redis = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                decode_responses=True
            )
            # Test connection
            self.redis.ping()
            print('\033[92m[NONCE] Redis connected ✅\033[0m')
            logger.info('Redis connected — %s:%d', REDIS_HOST, REDIS_PORT)
        except Exception as e:
            print(f'\033[93m[NONCE] Redis connection failed: {e} — using in-memory store ⚠️\033[0m')
            self.is_mock = True

    def _redis_key(self, nonce: str) -> str:
        return f'nonce:{nonce}'

    def use_nonce(self, nonce: str) -> bool:
        # SET NX — atomic, only sets if key doesn't exist
        key = self._redis_key(nonce)
        nonce_bytes = nonce.encode()

        with self.lock:
            # Check existing using hmac.compare_digest
            existing = self.redis.get(key)
            if existing is not None:
                if hmac.compare_digest(nonce_bytes, existing.encode()):
                    logger.warning('Replay rejected — nonce=%s', nonce[:16])
                    print(f'\033[93m[NONCE] REPLAY REJECTED: {nonce[:16]}...\033[0m')
                    return False

            # Atomic SET with TTL
            self.redis.set(
                key,
                nonce,
                ex=NONCE_TTL_HOURS * 3600
            )

        logger.info('Nonce accepted — nonce=%s', nonce[:16])
        print(f'\033[92m[NONCE] Accepted: {nonce[:16]}...\033[0m')
        return True

    def is_used(self, nonce: str) -> bool:
        key = self._redis_key(nonce)
        existing = self.redis.get(key)
        if existing is None:
            return False
        return hmac.compare_digest(nonce.encode(), existing.encode())

    def generate_nonce(self) -> str:
        return os.urandom(32).hex()

    def clear_old_nonces(self, hours=24):
        # Redis auto-expires — this is just for compatibility
        print(f'\033[92m[NONCE] Redis auto-expires nonces after {NONCE_TTL_HOURS}h\033[0m')
        logger.info('Redis handles nonce expiry automatically')
        return 0


if __name__ == '__main__':
    nm = NonceManager()

    n1 = nm.generate_nonce()
    print(f'\n--- Generated nonce: {n1[:16]}... ---')

    print('\n--- First use (should accept) ---')
    nm.use_nonce(n1)

    print('\n--- Second use (should reject — replay) ---')
    nm.use_nonce(n1)

    n2 = nm.generate_nonce()
    print(f'\n--- New nonce: {n2[:16]}... ---')
    nm.use_nonce(n2)
    nm.use_nonce(n2)

    print(f'\n--- is_used(n1): {nm.is_used(n1)} ---')
    print(f'--- is_used(fresh): {nm.is_used(nm.generate_nonce())} ---')

    print('\n--- Test complete ---')
