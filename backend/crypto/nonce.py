import threading
import logging
import time
import os

# NOTE: `hmac` was imported here for a compare_digest() call that guarded the
# nonce lookup. That comparison is gone — the claim is now a single atomic
# SET NX, so key presence is the answer and there is no secret to compare in
# constant time. Import removed rather than left dangling to imply a
# protection that is not there.

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
        """
        Claim a nonce. Returns True on first use, False on replay.

        BUG A — the comment said "SET NX — atomic, only sets if key doesn't
        exist" but the code did a separate get() then set(). Two concurrent
        requests carrying the same nonce both saw None and both proceeded. The
        threading.Lock only serialises within ONE process; with multiple
        uvicorn workers (or a second Pi) it gave no protection at all, which is
        precisely the deployment this system is built for.

        BUG B — worse, a FAILED comparison overwrote the nonce. If
        compare_digest(nonce, existing) returned False, control fell through to
        the unconditional set(), replacing the stored value and admitting the
        replay it was meant to block.

        Both are gone: the claim is now a single atomic SET NX EX. Redis
        decides, once, server-side. There is no read-then-write window and no
        comparison to get wrong — key presence IS the answer, so the stored
        value is a constant rather than the nonce itself.

        ALSO FIXED — the in-memory fallback never worked. `mock_store` was
        allocated but never read, `self.redis` was never assigned when redis
        was unavailable, and this method called `self.redis.get()`
        unconditionally: AttributeError, surfacing as a 500 from /crypto/sign
        on any machine without redis.
        """
        key = self._redis_key(nonce)
        ttl = NONCE_TTL_HOURS * 3600

        if self.is_mock:
            # Single-process fallback. The lock makes it atomic WITHIN this
            # process; it cannot span workers, which is why is_mock is
            # reported by /crypto/health.
            now = time.time()
            with self.lock:
                expiry = self.mock_store.get(key)
                if expiry is not None and expiry > now:
                    logger.warning('Replay rejected (in-memory) — nonce=%s', nonce[:16])
                    print(f'\033[93m[NONCE] REPLAY REJECTED: {nonce[:16]}...\033[0m')
                    return False
                # opportunistic sweep so the dict cannot grow without bound
                if len(self.mock_store) > 10000:
                    for k, exp in list(self.mock_store.items()):
                        if exp <= now:
                            del self.mock_store[k]
                self.mock_store[key] = now + ttl
            logger.info('Nonce accepted (in-memory) — nonce=%s', nonce[:16])
            print(f'\033[92m[NONCE] Accepted: {nonce[:16]}...\033[0m')
            return True

        # Redis path: ONE atomic operation. No lock — SET NX is resolved
        # server-side, so it is correct across processes and hosts, which a
        # threading.Lock never was.
        created = self.redis.set(key, '1', nx=True, ex=ttl)
        if not created:
            logger.warning('Replay rejected — nonce=%s', nonce[:16])
            print(f'\033[93m[NONCE] REPLAY REJECTED: {nonce[:16]}...\033[0m')
            return False

        logger.info('Nonce accepted — nonce=%s', nonce[:16])
        print(f'\033[92m[NONCE] Accepted: {nonce[:16]}...\033[0m')
        return True

    def is_used(self, nonce: str) -> bool:
        """
        Has this nonce been claimed? Read-only — does NOT consume.

        Key presence is the whole answer now that the stored value is a
        constant, so there is nothing to compare and no timing side channel to
        defend against here.
        """
        key = self._redis_key(nonce)
        if self.is_mock:
            with self.lock:
                expiry = self.mock_store.get(key)
                return expiry is not None and expiry > time.time()
        return bool(self.redis.exists(key))

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
