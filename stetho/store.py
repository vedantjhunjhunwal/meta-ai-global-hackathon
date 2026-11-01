"""SQLite store for machines, baselines and recordings.

Privacy by default:
  * Phone numbers are never stored. Users are keyed by an HMAC of their
    WhatsApp id with a server-side salt (`pseudonymise`).
  * Only derived features, quality results and the assessment are stored -
    not the video or audio. Raw media is deleted after analysis unless
    STETHO_RETAIN_MEDIA_HOURS > 0.
  * `delete_user` removes everything linked to a user (WhatsApp: "DELETE").
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(uid TEXT PRIMARY KEY, lang TEXT DEFAULT 'en', consent_at REAL,
  pending_mode TEXT, current_machine TEXT, created REAL);
CREATE TABLE IF NOT EXISTS machines(mid TEXT PRIMARY KEY, uid TEXT, name TEXT, rated_rpm REAL,
  nameplate TEXT, pending_nameplate TEXT, created REAL, UNIQUE(uid, name));
CREATE TABLE IF NOT EXISTS recordings(rid TEXT PRIMARY KEY, mid TEXT, uid TEXT, kind TEXT, created REAL,
  features TEXT, assessment TEXT, quality TEXT, simulated INTEGER);
CREATE TABLE IF NOT EXISTS processed(msg_id TEXT PRIMARY KEY, at REAL);
"""


def pseudonymise(raw_id: str, salt: str) -> str:
    return hmac.new(salt.encode(), raw_id.encode(), hashlib.sha256).hexdigest()[:24]


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def _q(self, sql, args=(), one=False):
        with self._lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            rows = cur.fetchall()
        return (dict(rows[0]) if rows else None) if one else [dict(r) for r in rows]

    # --- idempotency ---
    def mark_processed(self, msg_id: str) -> bool:
        """True if this message id is new (and is now recorded); False if duplicate."""
        with self._lock:
            cur = self.db.execute("INSERT OR IGNORE INTO processed(msg_id, at) VALUES(?, ?)", (msg_id, time.time()))
            self.db.commit()
            return cur.rowcount == 1

    # --- users ---
    def user(self, uid: str) -> dict:
        u = self._q("SELECT * FROM users WHERE uid=?", (uid,), one=True)
        if u is None:
            self._q("INSERT OR IGNORE INTO users(uid, created) VALUES(?, ?)", (uid, time.time()))
            u = self._q("SELECT * FROM users WHERE uid=?", (uid,), one=True)
        return u

    def update_user(self, uid: str, **fields):
        self.user(uid)
        allowed = {"lang", "consent_at", "pending_mode", "current_machine"}
        for k, v in fields.items():
            if k in allowed:
                self._q(f"UPDATE users SET {k}=? WHERE uid=?", (v, uid))

    def delete_user(self, uid: str) -> int:
        n = len(self._q("SELECT rid FROM recordings WHERE uid=?", (uid,)))
        for t in ("recordings", "machines", "users"):
            self._q(f"DELETE FROM {t} WHERE uid=?", (uid,))
        return n

    # --- machines ---
    def machine(self, uid: str, name: str) -> dict:
        name = (name or "pump").strip()[:40] or "pump"
        m = self._q("SELECT * FROM machines WHERE uid=? AND name=?", (uid, name), one=True)
        if m is None:
            self._q("INSERT OR IGNORE INTO machines(mid, uid, name, created) VALUES(?, ?, ?, ?)",
                    (uuid.uuid4().hex[:12], uid, name, time.time()))
            m = self._q("SELECT * FROM machines WHERE uid=? AND name=?", (uid, name), one=True)
        return m

    def update_machine(self, mid: str, **fields):
        for k, v in fields.items():
            if k in {"rated_rpm", "nameplate", "pending_nameplate"}:
                self._q(f"UPDATE machines SET {k}=? WHERE mid=?",
                        (json.dumps(v) if isinstance(v, dict) else v, mid))

    def machines(self, uid: str) -> list[dict]:
        return self._q("SELECT * FROM machines WHERE uid=? ORDER BY created", (uid,))

    # --- recordings ---
    def add_recording(self, mid, uid, kind, features, assessment, quality, simulated) -> str:
        rid = uuid.uuid4().hex[:12]
        self._q("INSERT INTO recordings VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, mid, uid, kind, time.time(), json.dumps(features), json.dumps(assessment),
                 json.dumps(quality), int(simulated)))
        return rid

    def recordings(self, mid: str, kind: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM recordings WHERE mid=?", [mid]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        rows = self._q(sql + " ORDER BY created", args)
        for r in rows:
            r["features"] = json.loads(r["features"])
            r["assessment"] = json.loads(r["assessment"]) if r.get("assessment") else {}
        return rows

    def baselines(self, mid: str) -> list[dict]:
        return [r["features"] for r in self.recordings(mid, "baseline")]

    def last(self, mid: str, kind: str) -> dict | None:
        rows = self.recordings(mid, kind)
        return rows[-1]["features"] if rows else None
