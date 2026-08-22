import mock_oqs_nacl
import threading
import time
import logging
import os
import sys
import hashlib
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import APIRouter, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keygen import generate_keypair
from sign import sign_command
from verify import verify_command
from ledger import CommandLedger
from nonce import NonceManager, NONCE_DB_PATH
from rogue_detector import RogueDetector

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
logger = logging.getLogger('crypto_routes')

# --- Module-level state ---
_keypair        = None
_ledger         = None
_nonce_manager  = None
_rogue_detector = None
_key_lock       = threading.Lock()
_sign_count     = 0
_verify_count   = 0
_startup_time   = None
_self_test_ok   = False

LEDGER_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ledger.db')

# Rate limiter
limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix='/crypto', tags=['crypto'])


# --- Request / Response models ---

class SignRequest(BaseModel):
    command_bytes: str

class VerifyRequest(BaseModel):
    command_hex:     str
    ml_dsa_sig_hex:  str
    ed25519_sig_hex: str
    valid_until:     int
    # BUG C: replay protection was applied at SIGNING time — sign() consumed
    # the nonce and /verify never looked at it. That is the wrong side of the
    # trust boundary: the signer is us, the verifier is the gate an attacker
    # has to get past. Replaying a captured command never touched sign(), so
    # the check never ran. The nonce is now consumed here.
    # Optional in the schema so an old client gets an explicit
    # MISSING_NONCE rejection rather than a 422 it cannot interpret.
    nonce:           Optional[str] = None

class CheckCommandRequest(BaseModel):
    command_hex:     str
    ml_dsa_sig_hex:  Optional[str] = None
    ed25519_sig_hex: Optional[str] = None
    nonce:           Optional[str] = None


# --- Startup ---

def startup_crypto():
    global _keypair, _ledger, _nonce_manager, _rogue_detector
    global _startup_time, _self_test_ok

    with _key_lock:
        _startup_time = int(time.time())

        print('\033[92m[CRYPTO] Initialising crypto layer...\033[0m')
        logger.info('Crypto startup begin')

        _keypair = generate_keypair()
        print(f'\033[92m[CRYPTO] Key fingerprint: {_keypair["key_fingerprint"]}\033[0m')
        logger.info('Keypair generated — fingerprint=%s', _keypair['key_fingerprint'])

        _ledger = CommandLedger(db_path=LEDGER_DB_PATH)
        _ledger.start_watchdog(interval=10)

        _nonce_manager  = NonceManager()
        _rogue_detector = RogueDetector(ledger_db_path=LEDGER_DB_PATH)

        # Run self-test
        try:
            test_cmd    = b'SELFTEST_COMMAND'
            test_signed = sign_command(
                test_cmd,
                _keypair['ml_dsa_secret'],
                _keypair['ed25519_signing_key']
            )
            test_result = verify_command(
                test_signed['command'],
                test_signed['ml_dsa_signature'],
                test_signed['ed25519_signature'],
                _keypair['ml_dsa_public'],
                _keypair['ed25519_verify_key'],
                test_signed['valid_until']
            )
            _self_test_ok = test_result['valid']
        except Exception as e:
            _self_test_ok = False
            logger.error('Self-test failed — %s', str(e))

        if _self_test_ok:
            print('\033[92m[CRYPTO] SYSTEM SELF-CHECK: ALL PASS\033[0m')
            logger.info('Self-test passed')
        else:
            print('\033[91m[CRYPTO] SYSTEM SELF-CHECK: FAILED\033[0m')
            logger.error('Self-test failed')


def _ensure_init():
    if _keypair is None:
        startup_crypto()


def rotate_keypair() -> dict:
    """
    Rotate the in-process signing keypair.

    Nothing in this codebase runs crypto as a standalone service — it is
    always mounted in-process via `router` (see the comment block above
    main.py's /crypto/rotate). Key rotation therefore has to happen HERE,
    swapping the module-level _keypair, not by asking some other process to
    do it. Commands signed under the old key are unaffected: /crypto/verify
    only ever checks against the CURRENT _keypair, exactly like real key
    rotation invalidates outstanding unverified signatures going forward.
    """
    global _keypair
    _ensure_init()
    with _key_lock:
        new_keypair = generate_keypair()
        _keypair = new_keypair
    logger.info('Key rotated — fingerprint=%s', new_keypair['key_fingerprint'])
    print(f'\033[92m[CRYPTO] Key rotated — new fingerprint: '
          f'{new_keypair["key_fingerprint"]}\033[0m')
    return {
        'key_fingerprint': new_keypair['key_fingerprint'],
        'rotated_at': int(time.time()),
    }


# --- Endpoints ---

def _mock_signing_allowed() -> bool:
    """
    DEADSAT_ALLOW_MOCK_SIGNING, read at call time.

    config.ALLOW_MOCK_SIGNING already defines this flag and already defaults
    off, but it was only honoured inside the recovery agent. It is now enforced
    at the SIGNING ENDPOINT, which is where it belongs: an endpoint that hands
    out unverifiable signatures is a problem regardless of which client asked.
    """
    return os.environ.get('DEADSAT_ALLOW_MOCK_SIGNING', '').strip().lower() in (
        '1', 'true', 'yes', 'on')


@router.post('/sign')
@limiter.limit('30/minute')
def sign(req: SignRequest, request: Request):
    global _sign_count
    _ensure_init()

    # SECURITY: refuse to issue a signature that cannot be verified.
    #
    # When mock_oqs_nacl is active the primitives are fakes whose verify()
    # accepts any byte string containing b"MOCK". Signing with them produces a
    # credential that looks real to every downstream consumer, gets written to
    # the ledger, and is only caught at the verification gate — where it burns
    # a recovery attempt and reports MOCK_SIGNATURE instead of the true cause.
    # Fail here, loudly, with the reason.
    if mock_oqs_nacl.is_mock_active() and not _mock_signing_allowed():
        detail = {
            'reason': 'SIGNING_UNAVAILABLE',
            'message': (f'Refusing to sign: {mock_oqs_nacl.mock_detail()}. '
                        f'Install liboqs/PyNaCl, or set '
                        f'DEADSAT_ALLOW_MOCK_SIGNING=1 to accept signatures '
                        f'that are NOT cryptographically valid.'),
            'mock_crypto': True,
            'crypto_backend': mock_oqs_nacl.mock_detail(),
        }
        logger.error('Signing refused — %s', detail['message'])
        print(f'\033[91m[CRYPTO] SIGNING REFUSED — {mock_oqs_nacl.mock_detail()}\033[0m')
        raise HTTPException(status_code=503, detail=detail)

    cmd_bytes = bytes.fromhex(req.command_bytes)

    with _key_lock:
        result = sign_command(
            cmd_bytes,
            _keypair['ml_dsa_secret'],
            _keypair['ed25519_signing_key']
        )

        # BUG C: `_nonce_manager.use_nonce(result['nonce'])` used to run HERE,
        # burning the nonce at signing time. Since we are the signer, that
        # only ever rejected our own duplicate signing calls — a replayed
        # command goes straight to /verify and never passes through here, so
        # the check could not fire on the attack it exists to stop. The nonce
        # is now consumed in /verify, which is the trust boundary.

        ledger_id = _ledger.add_entry(
            command_hex     = result['command'],
            ml_dsa_sig_hex  = result['ml_dsa_signature'],
            ed25519_sig_hex = result['ed25519_signature'],
            nonce           = result['nonce'],
            valid_until     = result['valid_until']
        )

        _sign_count += 1

    logger.info('Signed — ledger_id=%d fingerprint=%s', ledger_id, _keypair['key_fingerprint'])

    return {
        'ml_dsa_sig':      result['ml_dsa_signature'],
        'ed25519_sig':     result['ed25519_signature'],
        'nonce':           result['nonce'],
        'valid_until':     result['valid_until'],
        'ledger_id':       ledger_id,
        'key_fingerprint': _keypair['key_fingerprint']
    }


@router.post('/verify')
@limiter.limit('60/minute')
def verify(req: VerifyRequest, request: Request):
    global _verify_count
    _ensure_init()

    result = verify_command(
        req.command_hex,
        req.ml_dsa_sig_hex,
        req.ed25519_sig_hex,
        _keypair['ml_dsa_public'],
        _keypair['ed25519_verify_key'],
        req.valid_until
    )

    # BUG C: replay protection, moved from sign() to the trust boundary.
    #
    # Only claim the nonce if the signatures themselves check out — otherwise
    # an attacker could burn a legitimate nonce by submitting garbage under it
    # and deny the real command its one use.
    if result.get('valid'):
        if not req.nonce:
            result['valid'] = False
            result['reason'] = 'MISSING_NONCE'
            result['message'] = ('Signatures verified but no nonce supplied — '
                                 'cannot rule out a replay, refusing.')
            logger.warning('Verify rejected — no nonce supplied')
        elif not _nonce_manager.use_nonce(req.nonce):
            result['valid'] = False
            result['reason'] = 'REPLAYED_NONCE'
            result['message'] = ('Signatures are valid but this nonce has '
                                 'already been used — replayed command.')
            logger.warning('Verify rejected — replayed nonce %s', req.nonce[:16])
        else:
            result['nonce_ok'] = True
            # Surface when replay protection is process-local rather than
            # shared, so a multi-worker deployment cannot quietly believe it
            # has protection it does not have.
            if getattr(_nonce_manager, 'is_mock', False):
                result['nonce_store'] = 'in-memory (NOT replay-safe across workers)'

    with _key_lock:
        _verify_count += 1

    logger.info('Verify — valid=%s reason=%s', result['valid'], result['reason'])
    return result


@router.get('/ledger')
@limiter.limit('60/minute')
def get_ledger(request: Request):
    _ensure_init()
    rows = _ledger.get_all_entries()
    return [
        {
            'id':          r[0],
            'timestamp':   r[1],
            'cmd_hash':    r[2],
            'ml_dsa_sig':  r[3],
            'ed25519_sig': r[4],
            'nonce':       r[5],
            'prev_hash':   r[6],
            'operator':    r[7],
            'valid_until': r[8],
        }
        for r in rows
    ]


@router.get('/alerts')
@limiter.limit('60/minute')
def get_alerts(request: Request):
    _ensure_init()
    with _ledger.lock:
        conn = _ledger._connect()
        rows = conn.execute(
            'SELECT id, timestamp, alert_type, detail, severity FROM alerts ORDER BY id DESC'
        ).fetchall()
        conn.close()
    return [
        {
            'id':         r[0],
            'timestamp':  r[1],
            'alert_type': r[2],
            'detail':     r[3],
            'severity':   r[4],
        }
        for r in rows
    ]


@router.post('/check-rogue')
@limiter.limit('60/minute')
def check_command(req: CheckCommandRequest, request: Request):
    _ensure_init()
    cmd_hash = hashlib.sha256(bytes.fromhex(req.command_hex)).hexdigest()
    result = _rogue_detector.check_command(
        command_hash    = cmd_hash,
        ml_dsa_sig_hex  = req.ml_dsa_sig_hex,
        ed25519_sig_hex = req.ed25519_sig_hex,
        nonce           = req.nonce
    )
    return {
        'valid':      result['valid'],
        'alert_type': result.get('alert_type'),
        'severity':   result.get('severity'),
        'message':    result.get('detail', 'OK')
    }


@router.get('/health')
def health():
    _ensure_init()
    uptime = int(time.time()) - _startup_time if _startup_time else 0

    # WIRING: a shimmed signer must never present as a real one. When
    # mock_oqs_nacl is active the self-test signs and verifies through the
    # same fake primitives, so it always passes — status 'ok' here was
    # therefore meaningless. Report 'mock' so /system/links, the Security
    # Console and any operator reading this endpoint can see it.
    mock_active = mock_oqs_nacl.is_mock_active()
    if mock_active:
        status = 'mock'
    else:
        status = 'ok' if _self_test_ok else 'degraded'

    colour = '\033[93m' if mock_active else '\033[92m'
    print(f'{colour}[CRYPTO] Health check — status={status} uptime={uptime}s'
          f'{" — " + mock_oqs_nacl.mock_detail() if mock_active else ""}\033[0m')
    # WIRING: NonceManager falls back to an in-memory dict when redis is
    # missing or unreachable. It set self.is_mock and nothing ever read it,
    # so replay protection could silently degrade to per-process-only —
    # which is no protection at all across uvicorn workers. Report it.
    nonce_mock = bool(getattr(_nonce_manager, 'is_mock', False))

    return {
        'status':           status,
        'self_test_passed': _self_test_ok,
        'mock_crypto':      mock_active,
        'crypto_backend':   mock_oqs_nacl.mock_detail(),
        'nonce_store':      'in-memory (NOT replay-safe across workers)'
                            if nonce_mock else 'redis',
        'uptime_seconds':   uptime,
        'key_fingerprint':  _keypair['key_fingerprint'] if _keypair else None
    }


@router.get('/metrics')
def metrics():
    _ensure_init()

    with _ledger.lock:
        conn = _ledger._connect()
        ledger_count = conn.execute('SELECT COUNT(*) FROM commands').fetchone()[0]
        alert_rows   = conn.execute(
            'SELECT alert_type, COUNT(*) FROM alerts GROUP BY alert_type'
        ).fetchall()
        conn.close()

    alerts_by_type = {row[0]: row[1] for row in alert_rows}

    with _key_lock:
        sc = _sign_count
        vc = _verify_count

    return {
        'sign_count':     sc,
        'verify_count':   vc,
        'alerts_by_type': alerts_by_type,
        'ledger_entries': ledger_count,
        'watchdog_ok':    _ledger._watchdog_ok
    }


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_methods=['*'],
        allow_headers=['*'],
    )

    # Rate limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    startup_crypto()
    app.include_router(router)

    print('\033[92m[CRYPTO] Starting server on port 8000\033[0m')
    uvicorn.run(app, host='0.0.0.0', port=8001)
    
