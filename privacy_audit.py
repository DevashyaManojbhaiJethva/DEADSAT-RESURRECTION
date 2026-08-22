"""Persistent metadata-only privacy audit store."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any


class PrivacyAuditStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = Lock()
        db = self._connect()
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS privacy_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                requester TEXT NOT NULL, role TEXT, event_id TEXT, intent TEXT NOT NULL,
                current_context TEXT NOT NULL, sensitivity TEXT NOT NULL,
                decision TEXT NOT NULL, reason TEXT NOT NULL)""")
            db.commit()
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def record(self, record: dict[str, Any]) -> None:
        with self._lock:
            db = self._connect()
            try:
                db.execute("INSERT INTO privacy_audit (timestamp, requester, role, event_id, intent, current_context, sensitivity, decision, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (record["timestamp"], record["requester"], record.get("role"), str(record.get("event_id", "")),
                            record["intent"], record["context"], record["sensitivity"], record["decision"], record["reason"]))
                db.commit()
            finally:
                db.close()

    def records(self, limit: int = 100) -> list[dict[str, Any]]:
        db = self._connect()
        try:
            rows = db.execute("SELECT timestamp, requester, role, event_id, intent, current_context, sensitivity, decision, reason FROM privacy_audit ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 1000),)).fetchall()
        finally:
            db.close()
        keys = ("timestamp", "requester", "role", "event_id", "intent", "context", "sensitivity", "decision", "reason")
        return [dict(zip(keys, row)) for row in rows]
