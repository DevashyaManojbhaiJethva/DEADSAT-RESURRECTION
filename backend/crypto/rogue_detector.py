import redis
import hmac
import threading
import logging
import time
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nonce import NONCE_DB_PATH, REDIS_HOST, REDIS_PORT

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
logger = logging.getLogger('rogue_detector')

SEVERITY = {
    'UNSIGNED_COMMAND':   'critical',
    'SIGNATURE_MISMATCH': 'critical',
    'REPLAY_ATTACK':      'critical',
    'UNKNOWN_COMMAND':    'medium',
}


class RogueDetector:

    def __init__(self, ledger_db_path, nonce_db_path=NONCE_DB_PATH):
        self.lock = threading.Lock()
        self.ledger_conn = sqlite3.connect(ledger_db_path, check_same_thread=False)

        # Redis for nonce checking
        self.redis = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True
        )
        try:
            self.redis.ping()
            print('\033[92m[ROGUE] Redis connected ✅\033[0m')
            logger.info('Redis connected')
        except redis.ConnectionError:
            print('\033[91m[ROGUE] Redis connection failed\033[0m')
            raise

        logger.info('RogueDetector initialised — ledger=%s', ledger_db_path)
        print('\033[92m[ROGUE] Detector online\033[0m')

    def _is_nonce_used(self, nonce: str) -> bool:
        key = f'nonce:{nonce}'
        existing = self.redis.get(key)
        if existing is None:
            return False
        return hmac.compare_digest(nonce.encode(), existing.encode())

    def _get_ledger_entry(self, command_hash: str):
        return self.ledger_conn.execute(
            'SELECT id, ml_dsa_sig_hex, ed25519_sig_hex, nonce FROM commands WHERE command_hash = ?',
            (command_hash,)
        ).fetchone()

    def fire_alert(self, alert_type: str, detail: str):
        severity = SEVERITY[alert_type]
        ts = int(time.time())

        with self.lock:
            self.ledger_conn.execute(
                'INSERT INTO alerts (timestamp, alert_type, detail, severity) VALUES (?, ?, ?, ?)',
                (ts, alert_type, detail, severity)
            )
            self.ledger_conn.commit()

        logger.warning('Alert fired — type=%s severity=%s detail=%s', alert_type, severity, detail)

        if severity == 'critical':
            print(f'\033[91m[ROGUE] CRITICAL ALERT: {alert_type} — {detail}\033[0m')
        else:
            print(f'\033[93m[ROGUE] ALERT: {alert_type} — {detail}\033[0m')

        return {'alert_type': alert_type, 'severity': severity, 'detail': detail, 'timestamp': ts}

    def check_command(self, command_hash: str, ml_dsa_sig_hex: str = None,
                      ed25519_sig_hex: str = None, nonce: str = None) -> dict:

        # 1. No signature at all
        if not ml_dsa_sig_hex and not ed25519_sig_hex:
            alert = self.fire_alert(
                'UNSIGNED_COMMAND',
                f'cmd_hash={command_hash[:16]} arrived with no signature'
            )
            return {'valid': False, **alert}

        # 2. Replay attack — nonce already used
        if nonce and self._is_nonce_used(nonce):
            alert = self.fire_alert(
                'REPLAY_ATTACK',
                f'cmd_hash={command_hash[:16]} nonce={nonce[:16]} already used'
            )
            return {'valid': False, **alert}

        # 3. Not in ledger
        entry = self._get_ledger_entry(command_hash)
        if entry is None:
            alert = self.fire_alert(
                'UNKNOWN_COMMAND',
                f'cmd_hash={command_hash[:16]} not found in ledger'
            )
            return {'valid': False, **alert}

        # 4. Signature mismatch
        entry_id, ledger_ml_dsa, ledger_ed25519, ledger_nonce = entry

        ml_dsa_match  = hmac.compare_digest(ml_dsa_sig_hex.encode(),  ledger_ml_dsa.encode())
        ed25519_match = hmac.compare_digest(ed25519_sig_hex.encode(), ledger_ed25519.encode())

        if not ml_dsa_match or not ed25519_match:
            alert = self.fire_alert(
                'SIGNATURE_MISMATCH',
                f'cmd_hash={command_hash[:16]} ledger_id={entry_id} '
                f'ml_dsa_ok={ml_dsa_match} ed25519_ok={ed25519_match}'
            )
            return {'valid': False, **alert}

        logger.info('Command verified clean — cmd_hash=%s ledger_id=%d', command_hash[:16], entry_id)
        print(f'\033[92m[ROGUE] Command clean — cmd_hash={command_hash[:16]}... ledger_id={entry_id}\033[0m')
        return {
            'valid':      True,
            'alert_type': None,
            'severity':   None,
            'ledger_id':  entry_id,
        }

    def close(self):
        self.ledger_conn.close()
        self.redis.close()
        logger.info('RogueDetector connections closed')
        print('\033[92m[ROGUE] Detector closed\033[0m')
