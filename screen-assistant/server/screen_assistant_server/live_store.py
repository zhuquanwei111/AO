"""M2 单进程 SQLite：只存场次、事件及幂等元数据，不存原始公屏流水。"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from collections import Counter, deque
from pathlib import Path

from fastapi import HTTPException

from .live_rules import (
    COOLDOWN_MS, MAX_EVENTS, MAX_OBSERVATIONS, MAX_OBSERVATION_AGE_MS,
    MAX_PENDING, MAX_WINDOW_OBSERVATIONS, PROMPT_TTL_MS, WINDOW_MS, question,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LiveStore:
    """所有操作由 LiveService 串行在线程中调用，连接不跨进程共享。"""

    def __init__(self, path: str, clock):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.windows: dict[str, deque] = {}
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=0.5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS live_sessions (
                session_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                started_at_ms INTEGER NOT NULL, stopped_at_ms INTEGER,
                description TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_observations (
                session_id TEXT NOT NULL, event_hash TEXT NOT NULL,
                fingerprint TEXT NOT NULL, message_hash TEXT,
                result TEXT NOT NULL,
                PRIMARY KEY (session_id, event_hash),
                UNIQUE (session_id, message_hash)
            );
            CREATE TABLE IF NOT EXISTS live_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                topic_hash TEXT NOT NULL, generated_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL, returned_at_ms INTEGER,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS live_events_session
                ON live_events (session_id, seq);
            CREATE INDEX IF NOT EXISTS live_events_topic
                ON live_events (session_id, topic_hash, generated_at_ms);
        """)

    def close(self):
        self.db.close()

    def authorize(self, sid: str, token: str | None):
        row = self.db.execute(
            "SELECT * FROM live_sessions WHERE session_id = ?", (sid,)
        ).fetchone()
        # 不区分不存在与令牌错误，且错误正文不带输入值。
        expected = row["token_hash"] if row else "0" * 64
        supplied = token if token and len(token) <= 128 else ""
        valid = hmac.compare_digest(expected, digest(supplied))
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
        self.windows.pop(sid, None)
        return {"session_id": sid, "stopped_at_ms": stopped}

    @staticmethod
    def cursor(sid: str, seq: int) -> str:
        return base64.urlsafe_b64encode(bytes.fromhex(sid) + seq.to_bytes(8, "big")).decode()

    def sequence(self, sid: str, after: str) -> int:
        if not after:
            return 0
        try:
            raw = base64.b64decode(after, altchars=b"-_", validate=True)
            if len(raw) != 24 or raw[:16] != bytes.fromhex(sid):
                raise ValueError
            seq = int.from_bytes(raw[16:], "big")
            maximum = self.db.execute(
                "SELECT COALESCE(MAX(seq),0) FROM live_events WHERE session_id=?", (sid,)
            ).fetchone()[0]
            if seq > maximum:
                raise ValueError
            return seq
        except (ValueError, binascii.Error):
            raise HTTPException(422, "invalid live cursor") from None

    @staticmethod
    def event(row):
        payload = json.loads(row["payload"])
        payload.update(generated_at_ms=row["generated_at_ms"],
                       expires_at_ms=row["expires_at_ms"],
                       returned_at_ms=row["returned_at_ms"],
                       delivery_state="returned_to_client" if row["returned_at_ms"] is not None else "generated")
        return payload

    def events(self, sid: str, token: str | None, after: str = "", limit: int = 50):
        row = self.authorize(sid, token)
        seq = self.sequence(sid, after)
        records = self.db.execute(
            "SELECT * FROM live_events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
            (sid, seq, limit + 1),
        ).fetchall()
        return {"session_id": sid, "started_at_ms": row["started_at_ms"],
                "stopped_at_ms": row["stopped_at_ms"],
                "events": [self.event(r) for r in records[:limit]],
                "has_more": len(records) > limit,
                "next_cursor": self.cursor(sid, records[min(len(records), limit)-1]["seq"] if records else seq)}

    def prompts(self, sid: str, token: str | None, after: str):
        session = self.authorize(sid, token)
        seq, now = self.sequence(sid, after), self.clock()
        stopped = session["stopped_at_ms"] is not None
        rows = [] if stopped else self.db.execute(
            "SELECT * FROM live_events WHERE session_id=? AND seq>? AND expires_at_ms>? ORDER BY seq LIMIT 50",
            (sid, seq, now),
        ).fetchall()
        with self.db:
            for row in rows:
                self.db.execute(
                    "UPDATE live_events SET returned_at_ms=COALESCE(returned_at_ms,?) WHERE seq=?",
                    (now, row["seq"]),
                )
        prompts = []
        for row in rows:
            event = self.event(row)
            if event["returned_at_ms"] is None:
                event.update(returned_at_ms=now, delivery_state="returned_to_client")
            prompts.append(event)
        maximum = self.db.execute(
            "SELECT COALESCE(MAX(seq),0) FROM live_events WHERE session_id=?", (sid,)
        ).fetchone()[0]
        return {"session_id": sid, "prompts": prompts, "stopped": stopped,
                "next_cursor": self.cursor(sid, rows[-1]["seq"] if rows else maximum)}

    def add_comment(self, sid: str, token: str | None, body: dict, received_at_ms: int):
        with self.db:
            result, window = self._add_comment(sid, token, body, received_at_ms)
        if window is not None:
            self.windows[sid] = window
        return result

    def _add_comment(self, sid: str, token: str | None, body: dict, received_at_ms: int):
        session = self.authorize(sid, token)
        if session["stopped_at_ms"] is not None:
            raise HTTPException(409, "live session stopped")
        key = digest(body["event_id"])
        fingerprint = digest(json.dumps(body, ensure_ascii=False, sort_keys=True))
        previous = self.db.execute(
            "SELECT fingerprint,result FROM live_observations WHERE session_id=? AND event_hash=?", (sid, key)
        ).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise HTTPException(409, "observation id already used with different content")
            result = json.loads(previous["result"])
            result.update(status="duplicate", counted=False, prompt_id=None, reason="duplicate_event")
            return result, None
        count = self.db.execute(
            "SELECT COUNT(*) FROM live_observations WHERE session_id=?", (sid,)
        ).fetchone()[0]
        if count >= MAX_OBSERVATIONS:
            raise HTTPException(429, "live observation limit reached")
        now = self.clock()
        when = body["observed_at_ms"] if body["observed_at_ms"] is not None else received_at_ms
        if not session["started_at_ms"] <= when <= received_at_ms <= now:
            raise HTTPException(422, "observation time outside live session")
        basis = "observed" if body["observed_at_ms"] is not None else "received"
        window = deque((o for o in self.windows.get(sid, ()) if now-WINDOW_MS <= o["when"] <= now),
                       maxlen=MAX_WINDOW_OBSERVATIONS)
        result = {"status": "accepted", "counted": False, "prompt_id": None, "reason": None}
        result.update(received_at_ms=received_at_ms, effective_at_ms=when, time_basis=basis)
        topic = question(body["text"])
        message = digest(json.dumps([body["source"], body["platform_message_id"]])) if body["platform_message_id"] else None
        author = digest(json.dumps([body["source"], body["author_id"]])) if body["author_id"] else None
        reason = None
        if body["reliability"] != "known" or body["coverage"] == "unknown":
            reason = "unreliable"
        elif body["coverage"] == "ocr_visible_sample":
            reason = "ocr_tracking_unavailable"
        elif now-when > MAX_OBSERVATION_AGE_MS:
            reason = "late"
        elif not message and not author:
            reason = "no_independent_evidence"
        elif message and self.db.execute(
            "SELECT 1 FROM live_observations WHERE session_id=? AND message_hash=?", (sid, message)
        ).fetchone():
            reason = "duplicate_message"
        elif topic is None:
            reason = "not_a_question"
        elif not message and any(o["topic"] == topic[0] and o["author"] == author for o in window):
            reason = "same_author_without_message_id"
        if reason:
            result["reason"] = reason
            message = None
        else:
            result["counted"] = True
            window.append({"topic": topic[0], "when": when, "basis": basis,
                           "source": body["source"], "coverage": body["coverage"], "author": author,
                           "received_at_ms": received_at_ms})
            group = [o for o in window if o["topic"] == topic[0]]
            result["reason"] = "awaiting_repeat"
            if Counter(o["topic"] for o in window)[topic[0]] >= 2:
                result.update(self.generate(sid, session, topic, group, now))
        self.db.execute("INSERT INTO live_observations VALUES (?,?,?,?,?)",
                        (sid, key, fingerprint, message, json.dumps(result)))
        return result, window

    def generate(self, sid, session, topic, group, now):
        topic_hash = digest(topic[0])
        last = self.db.execute(
            "SELECT MAX(generated_at_ms) FROM live_events WHERE session_id=? AND topic_hash=?", (sid, topic_hash)
        ).fetchone()[0]
        if last is not None and now-last < COOLDOWN_MS:
            return {"reason": "cooldown"}
        pending = self.db.execute(
            "SELECT COUNT(*) FROM live_events WHERE session_id=? AND returned_at_ms IS NULL AND expires_at_ms>?",
            (sid, now),
        ).fetchone()[0]
        if pending >= MAX_PENDING:
            return {"reason": "pending_limit"}
        total = self.db.execute("SELECT COUNT(*) FROM live_events WHERE session_id=?", (sid,)).fetchone()[0]
        if total >= MAX_EVENTS:
            raise HTTPException(429, "live event limit reached")
        first, last = min(o["when"] for o in group), max(o["when"] for o in group)
        sources = Counter((o["source"], o["coverage"], o["basis"]) for o in group)
        bases = {o["basis"] for o in group}
        event = {"prompt_id": uuid.uuid4().hex, "input_mode": "test_fixture",
                 "first_received_at_ms": min(o["received_at_ms"] for o in group),
                 "last_received_at_ms": max(o["received_at_ms"] for o in group),
                 "start_at_ms": first, "end_at_ms": last,
                 "start_offset_ms": first-session["started_at_ms"],
                 "end_offset_ms": last-session["started_at_ms"],
                 "time_basis": next(iter(bases)) if len(bases) == 1 else "mixed",
                 "phenomenon": f"公屏中出现重复问题：{topic[1]}",
                 "suggestion": "可以集中答一下。",
                 "display_text": f"公屏反复问“{topic[1]}”，可以集中答一下。",
                 "observation_count": len(group),
                 "sources": [{"source": s, "coverage": c, "time_basis": b,
                              "input_mode": "test_fixture", "reliability": "known", "count": n}
                             for (s, c, b), n in sources.items()]}
        # 与观察幂等记录在同一 SQLite 事务中提交。
        self.db.execute(
            "INSERT INTO live_events (session_id,topic_hash,generated_at_ms,expires_at_ms,payload) VALUES (?,?,?,?,?)",
            (sid, topic_hash, now, now+PROMPT_TTL_MS, json.dumps(event, ensure_ascii=False)),
        )
        return {"reason": "generated", "prompt_id": event["prompt_id"]}
