"""M2 单进程 SQLite：只存场次、事件及幂等元数据，不存原始公屏流水。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from pathlib import Path

from fastapi import HTTPException


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LiveStore:
    """所有操作由 LiveService 串行在线程中调用，连接不跨进程共享。"""

    def __init__(self, path: str, clock):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=0.5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS live_sessions (
                session_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                started_at_ms INTEGER NOT NULL, stopped_at_ms INTEGER,
                description TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def authorize(self, sid: str, token: str | None):
        row = self.db.execute(
            "SELECT * FROM live_sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        # 不区分不存在与令牌错误，且错误正文不带输入值。
        expected = row["token_hash"] if row else "0" * 64
        valid = hmac.compare_digest(expected, digest(token or ""))
        if not row or not valid:
            raise HTTPException(403, "live session access denied")
        return row

    def create(self, description: str):
        active = self.db.execute(
            "SELECT COUNT(*) FROM live_sessions WHERE stopped_at_ms IS NULL"
        ).fetchone()[0]
        if active >= 8:
            raise HTTPException(429, "active live session limit reached")
        sid, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        now = self.clock()
        with self.db:
            self.db.execute(
                "INSERT INTO live_sessions VALUES (?, ?, ?, NULL, ?)",
                (sid, digest(token), now, description),
            )
        return {"session_id": sid, "session_token": token, "started_at_ms": now}

    def stop(self, sid: str, token: str | None):
        row = self.authorize(sid, token)
        stopped = row["stopped_at_ms"]
        if stopped is None:
            stopped = max(self.clock(), row["started_at_ms"])
            with self.db:
                self.db.execute(
                    "UPDATE live_sessions SET stopped_at_ms = ? WHERE session_id = ?",
                    (stopped, sid),
                )
        return {"session_id": sid, "stopped_at_ms": stopped}

    def events(self, sid: str, token: str | None):
        row = self.authorize(sid, token)
        return {"session_id": sid, "started_at_ms": row["started_at_ms"],
                "stopped_at_ms": row["stopped_at_ms"], "events": []}
