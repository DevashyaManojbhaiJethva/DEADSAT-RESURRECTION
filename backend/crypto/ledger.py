import sqlite3
import hashlib
import threading
import logging
import time
import secrets

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s'
)
logger = logging.getLogger('ledger')

GENESIS_HASH = hashlib.sha256(b'GENESIS').hexdigest()

class CommandLedger:

    def __init__(self, db_path='ledger.db'):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._watchdog_ok = True

        with self.lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS commands (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   INTEGER NOT NULL,
                    command_hash    TEXT NOT NULL,
                    ml_dsa_sig_hex  TEXT NOT NULL,
                    ed25519_sig_hex TEXT NOT NULL,
                    nonce       TEXT NOT NULL,
                    prev_hash   TEXT NOT NULL,
                    operator    TEXT NOT NULL,
                    valid_until INTEGER NOT NULL
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   INTEGER NOT NULL,
                    alert_type  TEXT NOT NULL,
                    detail      TEXT NOT NULL,
                    severity    TEXT NOT NULL
                )
            ''')
            conn.commit()
            conn.close()

    def _connect(self):
        # Each call opens a fresh connection — safe for multi-thread use
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def get_latest_hash(self):
        with self.lock:
            conn = self._connect()
            row = conn.execute(
                'SELECT id, command_hash, prev_hash FROM commands ORDER BY id DESC LIMIT 1'
            ).fetchone()
            conn.close()

        if row is None:
            return GENESIS_HASH

        # Recompute this entry's chain hash the same way add_entry does
        entry_id, cmd_hash, prev_hash = row
        return hashlib.sha256(f'{entry_id}{cmd_hash}{prev_hash}'.encode()).hexdigest()

    def add_entry(self, command_hex, ml_dsa_sig_hex, ed25519_sig_hex,
                  nonce, valid_until, operator='CY-1'):
        ts = int(time.time())
        command_hash = hashlib.sha256(bytes.fromhex(command_hex)).hexdigest()

        with self.lock:
            conn = self._connect()

            # Get previous chain tip inside the lock to avoid race conditions
            row = conn.execute(
                'SELECT id, command_hash, prev_hash FROM commands ORDER BY id DESC LIMIT 1'
            ).fetchone()

            if row is None:
                prev_hash = GENESIS_HASH
            else:
                prev_id, prev_cmd_hash, prev_prev_hash = row
                prev_hash = hashlib.sha256(
                    f'{prev_id}{prev_cmd_hash}{prev_prev_hash}'.encode()
                ).hexdigest()

            conn.execute('''
                INSERT INTO commands
                    (timestamp, command_hash, ml_dsa_sig_hex, ed25519_sig_hex,
                     nonce, prev_hash, operator, valid_until)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (ts, command_hash, ml_dsa_sig_hex, ed25519_sig_hex,
                  nonce, prev_hash, operator, valid_until))
            conn.commit()
            entry_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.close()

        logger.info('Entry %d added — cmd_hash=%s operator=%s', entry_id, command_hash[:16], operator)
        print(f'\033[92m[LEDGER] Entry {entry_id} added — cmd={command_hash[:16]}... operator={operator}\033[0m')
        return entry_id

    def get_entry(self, entry_id):
        with self.lock:
            conn = self._connect()
            row = conn.execute(
                'SELECT * FROM commands WHERE id = ?', (entry_id,)
            ).fetchone()
            conn.close()
        return row

    def get_all_entries(self):
        with self.lock:
            conn = self._connect()
            rows = conn.execute(
                'SELECT * FROM commands ORDER BY id ASC'
            ).fetchall()
            conn.close()
        return rows

    def verify_chain(self):
        with self.lock:
            conn = self._connect()
            rows = conn.execute(
                'SELECT id, command_hash, prev_hash FROM commands ORDER BY id ASC'
            ).fetchall()
            conn.close()

        if not rows:
            logger.info('Chain empty — nothing to verify')
            return True

        # Check first entry links back to GENESIS
        first_id, first_cmd_hash, first_prev_hash = rows[0]
        if first_prev_hash != GENESIS_HASH:
            logger.critical('Chain broken at entry %d — genesis mismatch', first_id)
            print('\033[91m[CRITICAL] LEDGER CHAIN INTEGRITY VIOLATION\033[0m')
            return False

        # Walk the rest of the chain
        for i in range(1, len(rows)):
            prev_id, prev_cmd_hash, prev_prev_hash = rows[i - 1]
            curr_id, curr_cmd_hash, curr_prev_hash = rows[i]

            expected_prev_hash = hashlib.sha256(
                f'{prev_id}{prev_cmd_hash}{prev_prev_hash}'.encode()
            ).hexdigest()

            if curr_prev_hash != expected_prev_hash:
                logger.critical(
                    'Chain broken between entry %d and %d — hash mismatch', prev_id, curr_id
                )
                print('\033[91m[CRITICAL] LEDGER CHAIN INTEGRITY VIOLATION\033[0m')
                return False

        logger.info('Chain verified — %d entries OK', len(rows))
        return True

    def _store_alert(self, alert_type, detail, severity):
        with self.lock:
            conn = self._connect()
            conn.execute(
                'INSERT INTO alerts (timestamp, alert_type, detail, severity) VALUES (?, ?, ?, ?)',
                (int(time.time()), alert_type, detail, severity)
            )
            conn.commit()
            conn.close()

    def start_watchdog(self, interval=10):
        def _watch():
            while True:
                time.sleep(interval)
                entries = self.get_all_entries()
                n = len(entries)
                ok = self.verify_chain()
                if ok:
                    self._watchdog_ok = True
                    print(f'\033[92m[WATCHDOG] Chain OK — {n} entries verified\033[0m')
                    logger.info('Watchdog: chain OK — %d entries', n)
                else:
                    self._watchdog_ok = False
                    print('\033[91m[WATCHDOG] CRITICAL — CHAIN TAMPERED, firing alert\033[0m')
                    logger.critical('Watchdog: chain tampered — firing alert')
                    self._store_alert(
                        'CHAIN_TAMPERED',
                        f'Hash chain broken — {n} entries checked',
                        'critical'
                    )

        t = threading.Thread(target=_watch, daemon=True, name='ledger-watchdog')
        t.start()
        logger.info('Watchdog started — interval=%ds', interval)
        print(f'\033[92m[WATCHDOG] Started — checking every {interval}s\033[0m')


if __name__ == '__main__':
    import os

    DB = 'test_ledger.db'
    if os.path.exists(DB):
        os.remove(DB)

    ledger = CommandLedger(db_path=DB)

    # Add 3 entries
    for i in range(1, 4):
        cmd = secrets.token_hex(16)
        ml_sig = secrets.token_hex(64)
        ed_sig = secrets.token_hex(32)
        nonce = secrets.token_hex(32)
        ledger.add_entry(cmd, ml_sig, ed_sig, nonce, int(time.time()) + 120)

    # Verify clean chain
    print('\n--- Initial chain verify ---')
    ledger.verify_chain()

    # Start watchdog
    print('\n--- Starting watchdog (10s interval) ---')
    ledger.start_watchdog(interval=10)

    # Wait one cycle so watchdog prints OK first
    time.sleep(12)

    # Manually corrupt entry 2 to trigger watchdog
    print('\n--- Corrupting entry 2 in ledger.db ---')
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE commands SET command_hash = 'DEADBEEF_CORRUPTED' WHERE id = 2")
    conn.commit()
    conn.close()
    print('\033[93m[TEST] Entry 2 corrupted — watchdog should fire within 10s\033[0m')

    # Wait for watchdog to catch it
    time.sleep(13)
    print('\n--- Test complete ---')
