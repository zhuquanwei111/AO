"""M2 单进程 SQLite：只存场次、事件及幂等元数据，不存原始公屏流水。"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import uuid
from collections import Counter, deque
from pathlib import Path

from fastapi import HTTPException

from .live_rules import (
    COOLDOWN_MS, MAX_EVENTS, MAX_OBSERVATIONS, MAX_OBSERVATION_AGE_MS,
    MAX_PENDING, MAX_WINDOW_OBSERVATIONS, PROMPT_TTL_MS, WINDOW_MS, question,
    FUSION_FUTURE_SKEW_MS,
)
from .live_fusion import (
    build_candidate, observation_source_allowed, online_count_details,
    validate_observation_content,
)
from .comment_reconstruction import reconstruct_comments

logger = logging.getLogger("screen_assistant.app")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LiveStore:
    """所有操作由 LiveService 串行在线程中调用，连接不跨进程共享。"""

    def __init__(self, path: str, clock):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.windows: dict[str, deque] = {}
        self.fusion_windows: dict[str, deque] = {}
        self.comment_batches: dict[str, dict[tuple[str, str], dict]] = {}
        self.comment_batch_sweeps: dict[str, int] = {}
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=0.5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS live_sessions (
                session_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                started_at_ms INTEGER NOT NULL, stopped_at_ms INTEGER,
                description TEXT NOT NULL, douyin_account TEXT,
                app_source TEXT
            );
            CREATE TABLE IF NOT EXISTS live_observations (
                session_id TEXT NOT NULL, event_hash TEXT NOT NULL,
                fingerprint TEXT NOT NULL, message_hash TEXT,
                result TEXT NOT NULL,
                PRIMARY KEY (session_id, event_hash),
                UNIQUE (session_id, message_hash)
            );
            CREATE TABLE IF NOT EXISTS live_audio_fusion_status (
                session_id TEXT PRIMARY KEY,
                background_analysis_busy_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(session_id) REFERENCES live_sessions(session_id)
            );
            CREATE TABLE IF NOT EXISTS live_count_samples (
                session_id TEXT NOT NULL, event_hash TEXT NOT NULL, source TEXT NOT NULL,
                observed_at_ms INTEGER, effective_at_ms INTEGER NOT NULL, received_at_ms INTEGER NOT NULL,
                availability TEXT NOT NULL, reliability TEXT NOT NULL, coverage TEXT NOT NULL,
                confidence REAL, field_semantics_confirmed INTEGER NOT NULL,
                raw_text TEXT, display_unit TEXT, parsed_count INTEGER, approximate INTEGER NOT NULL,
                text_position_json TEXT, ocr_lines_json TEXT,
                reason TEXT NOT NULL, PRIMARY KEY (session_id, event_hash)
            );
            CREATE INDEX IF NOT EXISTS live_count_samples_session_source_time
                ON live_count_samples (session_id, source, effective_at_ms);
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
            CREATE TABLE IF NOT EXISTS live_backstage_tasks (
                task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE,
                douyin_account TEXT NOT NULL, status TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL, claimed_at_ms INTEGER,
                stopped_at_ms INTEGER, received_at_ms INTEGER,
                start_signal_source TEXT NOT NULL DEFAULT 'ios_app', stop_signal_source TEXT,
                result_hash TEXT, result_json TEXT,
                FOREIGN KEY(session_id) REFERENCES live_sessions(session_id)
            );
            CREATE INDEX IF NOT EXISTS live_backstage_status
                ON live_backstage_tasks (status, created_at_ms);
        """)
        count_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(live_count_samples)")}
        for column, definition in (
            ("display_unit", "TEXT"),
            ("text_position_json", "TEXT"),
            ("ocr_lines_json", "TEXT"),
        ):
            if column not in count_columns:
                self.db.execute(f"ALTER TABLE live_count_samples ADD COLUMN {column} {definition}")
        session_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(live_sessions)")}
        if "douyin_account" not in session_columns:
            self.db.execute("ALTER TABLE live_sessions ADD COLUMN douyin_account TEXT")
        if "app_source" not in session_columns:
            self.db.execute("ALTER TABLE live_sessions ADD COLUMN app_source TEXT")
        task_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(live_backstage_tasks)")}
        if "start_signal_source" not in task_columns:
            self.db.execute(
                "ALTER TABLE live_backstage_tasks ADD COLUMN start_signal_source "
                "TEXT NOT NULL DEFAULT 'ios_app'"
            )
        if "stop_signal_source" not in task_columns:
            self.db.execute("ALTER TABLE live_backstage_tasks ADD COLUMN stop_signal_source TEXT")
        with self.db:
            self.db.execute(
                "UPDATE live_backstage_tasks SET stop_signal_source=start_signal_source "
                "WHERE stopped_at_ms IS NOT NULL AND stop_signal_source IS NULL"
            )

    def close(self):
        self.db.close()

    def _expire_comment_payloads(self, sid: str, now: int) -> None:
        previous = self.comment_batch_sweeps.get(sid)
        if previous is not None and now >= previous and now - previous < 1000:
            return
        self.comment_batch_sweeps[sid] = now
        for batch in self.comment_batches.get(sid, {}).values():
            if now > batch["deadline"]:
                batch["rows"].clear()
                batch.pop("records", None)
                if not batch.get("expired") and not batch["complete"]:
                    logger.info("live_comment_batch outcome=expired")
                batch["expired"] = True

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

    def create(self, description: str, douyin_account: str | None = None):
        active = self.db.execute(
            "SELECT COUNT(*) FROM live_sessions WHERE stopped_at_ms IS NULL"
        ).fetchone()[0]
        if active >= 8:
            raise HTTPException(429, "active live session limit reached")
        sid, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        now = self.clock()
        with self.db:
            self.db.execute(
                "INSERT INTO live_sessions "
                "(session_id,token_hash,started_at_ms,stopped_at_ms,description,douyin_account) "
                "VALUES (?, ?, ?, NULL, ?, ?)",
                (sid, digest(token), now, description, douyin_account),
            )
        return {
            "session_id": sid, "session_token": token, "started_at_ms": now,
            "douyin_account": douyin_account, "backstage_task_id": None,
            "backstage_status": "not_started" if douyin_account else "not_configured",
            "capabilities": {"observation_batch_metadata": True, "typed_prompts": False},
        }

    def _stored_app_source(self, sid: str, session) -> str:
        if session["app_source"]:
            return session["app_source"]
        task = self.db.execute(
            "SELECT start_signal_source FROM live_backstage_tasks WHERE session_id=?", (sid,)
        ).fetchone()
        return task["start_signal_source"] if task else "ios_app"

    def session_signal_source(self, sid: str, token: str | None) -> str:
        session = self.authorize(sid, token)
        return self._stored_app_source(sid, session)

    def start_backstage(self, sid: str, token: str | None, signal_source: str = "ios_app"):
        session = self.authorize(sid, token)
        if session["stopped_at_ms"] is not None:
            raise HTTPException(409, "live session stopped")
        if signal_source not in {"ios_app", "android_app"}:
            raise HTTPException(422, "invalid app signal source")
        existing = self.db.execute(
            "SELECT task_id,status,douyin_account,start_signal_source FROM live_backstage_tasks "
            "WHERE session_id=?", (sid,),
        ).fetchone()
        stored_source = session["app_source"] or (
            existing["start_signal_source"] if existing else None
        )
        if stored_source and stored_source != signal_source:
            raise HTTPException(409, "start signal source does not match app session")
        if not session["app_source"]:
            with self.db:
                self.db.execute("UPDATE live_sessions SET app_source=? WHERE session_id=?",
                                (stored_source or signal_source, sid))
            session = self.authorize(sid, token)
        account = session["douyin_account"]
        if not account:
            return {"status": "not_configured", "session_id": sid, "task_id": None,
                    "douyin_account": None, "task_status": "not_configured",
                    "signal_source": signal_source}
        if existing:
            if existing["start_signal_source"] != signal_source:
                raise HTTPException(409, "start signal source does not match existing task")
            return {"status": "duplicate", "session_id": sid,
                    "task_id": existing["task_id"], "douyin_account": existing["douyin_account"],
                    "task_status": existing["status"], "signal_source": signal_source}
        active = self.db.execute(
            "SELECT 1 FROM live_backstage_tasks WHERE result_hash IS NULL AND status IN "
            "('waiting_for_stop','capturing') LIMIT 1"
        ).fetchone()
        task_status = "not_connected" if active else "waiting_for_stop"
        task_id = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO live_backstage_tasks "
                "(task_id,session_id,douyin_account,status,created_at_ms,start_signal_source) "
                "VALUES (?,?,?,?,?,?)",
                (task_id, sid, account, task_status, self.clock(), signal_source),
            )
        return {"status": "started", "session_id": sid, "task_id": task_id,
                "douyin_account": account, "task_status": task_status,
                "signal_source": signal_source}

    def description(self, sid: str, token: str | None) -> str:
        return self.authorize(sid, token)["description"]

    def audio_access(self, sid: str, token: str | None):
        row = self.authorize(sid, token)
        if row["stopped_at_ms"] is not None:
            raise HTTPException(409, "live session stopped")
        return {"started_at_ms": row["started_at_ms"],
                "signal_source": self._stored_app_source(sid, row)}

    def stop(self, sid: str, token: str | None, signal_source: str | None = None):
        row = self.authorize(sid, token)
        if signal_source is not None and signal_source not in {"ios_app", "android_app"}:
            raise HTTPException(422, "invalid app signal source")
        expected_source = self._stored_app_source(sid, row)
        if signal_source is not None and signal_source != expected_source:
            raise HTTPException(409, "stop signal source does not match app session")
        signal_source = signal_source or expected_source
        stopped = row["stopped_at_ms"]
        if stopped is None:
            stopped = max(self.clock(), row["started_at_ms"])
            with self.db:
                self.db.execute(
                    "UPDATE live_sessions SET stopped_at_ms = ?,app_source = COALESCE(app_source,?) "
                    "WHERE session_id = ?",
                    (stopped, signal_source, sid),
                )
                task = self.db.execute(
                    "SELECT claimed_at_ms FROM live_backstage_tasks WHERE session_id=?", (sid,)
                ).fetchone()
                if task:
                    status = "capturing" if task["claimed_at_ms"] is not None else "not_connected"
                    self.db.execute(
                        "UPDATE live_backstage_tasks SET status=?,stopped_at_ms=?,stop_signal_source=? "
                        "WHERE session_id=?",
                        (status, stopped, signal_source, sid),
                    )
        self.windows.pop(sid, None)
        self.fusion_windows.pop(sid, None)
        batches = self.comment_batches.pop(sid, {})
        self.comment_batch_sweeps.pop(sid, None)
        if any(not batch["complete"] for batch in batches.values()):
            logger.info("live_comment_batch outcome=stopped_incomplete")
        task = self.db.execute(
            "SELECT task_id,status FROM live_backstage_tasks WHERE session_id=?", (sid,)
        ).fetchone()
        return {
            "session_id": sid, "stopped_at_ms": stopped,
            "backstage_task_id": task["task_id"] if task else None,
            "backstage_status": task["status"] if task else "not_configured",
            "signal_source": signal_source,
        }

    def add_observation(self, sid: str, token: str | None, body: dict, received_at_ms: int):
        session = self.authorize(sid, token)
        self._expire_comment_payloads(sid, received_at_ms)
        if session["stopped_at_ms"] is not None:
            raise HTTPException(409, "live session stopped")
        if not observation_source_allowed(body["kind"], body["source"],
                                          session["app_source"] or "ios_app"):
            raise HTTPException(422, "observation source does not match app session")
        event_hash = digest(body["event_id"])
        fingerprint = digest(json.dumps(body, ensure_ascii=False, sort_keys=True))
        prior = self.db.execute(
            "SELECT fingerprint,result FROM live_observations WHERE session_id=? AND event_hash=?",
            (sid, event_hash),
        ).fetchone()
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise HTTPException(409, "observation id already used with different content")
            result = json.loads(prior["result"])
            result.update(status="duplicate", prompt_id=None, reason="duplicate_event")
            return result
        count = self.db.execute(
            "SELECT COUNT(*) FROM live_observations WHERE session_id=?", (sid,)
        ).fetchone()[0]
        if count >= MAX_OBSERVATIONS:
            raise HTTPException(429, "live observation limit reached")

        observed = body.get("observed_at_ms")
        effective = observed or body.get("ended_at_ms") or body.get("started_at_ms") or received_at_ms
        for timestamp in (observed, body.get("started_at_ms"), body.get("ended_at_ms")):
            if timestamp is not None and (timestamp < session["started_at_ms"]
                                          or timestamp > received_at_ms + FUSION_FUTURE_SKEW_MS):
                raise HTTPException(422, "observation time outside live session")
        if observed is not None and body.get("started_at_ms") is not None:
            if body["started_at_ms"] > observed:
                raise HTTPException(422, "observation start follows observed time")

        result = {
            "status": "accepted", "prompt_id": None, "received_at_ms": received_at_ms,
            "effective_at_ms": effective,
            "time_basis": "observed" if observed is not None else "received",
        }
        if body["kind"] == "transcript" and observed is not None:
            evidence = (body.get("content") or {}).get("audio_evidence") or {}
            if evidence.get("time_basis") == "pts_arrival_approx":
                result["time_basis"] = "pts_arrival_approx"
        parsed = None
        count_details = None
        usable, reason = validate_observation_content(body)
        if usable and body["kind"] == "online_count" and observed is None:
            usable, reason = False, "observation_time_unknown"
        if usable and received_at_ms - effective > MAX_OBSERVATION_AGE_MS:
            usable, reason = False, "late"
        if usable and body["kind"] == "online_count":
            content = body["content"]
            count_details = online_count_details(
                content["raw_text"],
                field_semantics_confirmed=content["field_semantics_confirmed"],
            )
            if count_details is None:
                usable, reason = False, (
                    "field_semantics_unconfirmed"
                    if not content["field_semantics_confirmed"] else "count_unreadable"
                )
            else:
                parsed = count_details["nominal_value"]
        message_hash = None
        if usable and body["kind"] == "comment":
            content = body["content"]
            stable_id = content.get("platform_message_id")
            if stable_id:
                message_hash = digest(json.dumps([body["source"], stable_id]))
            elif body["source"] == "ios_replaykit_ocr" and not body.get("capture_batch_id"):
                normalized = "".join(content["text"].split())
                identity = content.get("author_id")
                position = content.get("text_position")
                if identity:
                    replay_key = [body["source"], normalized, identity, effective // 10_000]
                elif position:
                    coarse_position = [round(position[k] / 0.05) for k in ("x", "y")]
                    replay_key = [body["source"], normalized, coarse_position, effective // 10_000]
                else:
                    replay_key = None
                if replay_key is not None:
                    message_hash = digest(json.dumps(replay_key, ensure_ascii=False))
        if usable and message_hash and self.db.execute(
            "SELECT 1 FROM live_observations WHERE session_id=? AND message_hash=?",
            (sid, message_hash),
        ).fetchone():
            usable, reason, message_hash = False, "duplicate_message", None

        batch = None
        batch_count = body.get("capture_comment_count")
        if body["kind"] == "comment" and batch_count is not None:
            key = (body["source"], body["capture_batch_id"])
            batches = self.comment_batches.setdefault(sid, {})
            batch = batches.get(key)
            if batch is None:
                batch = {"count": batch_count, "indices": {}, "rows": [],
                         "deadline": min(effective, received_at_ms) + MAX_OBSERVATION_AGE_MS,
                         "complete": False}
                batches[key] = batch
            if batch["count"] != batch_count:
                raise HTTPException(409, "capture batch count mismatch")
            if body["line_index"] in batch["indices"] or batch["complete"]:
                raise HTTPException(409, "capture batch index already used")
            batch["deadline"] = min(batch["deadline"], effective + MAX_OBSERVATION_AGE_MS)
            if received_at_ms > batch["deadline"]:
                batch["rows"].clear()
                if not batch.get("expired"):
                    logger.info("live_comment_batch outcome=expired")
                    batch["expired"] = True
                usable, reason = False, "batch_expired"
        if not usable:
            result["reason"] = reason or "unavailable"
        elif batch is not None:
            result["reason"] = "batch_incomplete"
        else:
            result["reason"] = "windowed"

        with self.db:
            self.db.execute("INSERT INTO live_observations VALUES (?,?,?,?,?)", (
                sid, event_hash, fingerprint, message_hash,
                json.dumps(result, ensure_ascii=False),
            ))
            if body["kind"] == "online_count":
                content = body.get("content") or {}
                raw_text = content.get("raw_text")
                self.db.execute("""
                    INSERT INTO live_count_samples (
                        session_id,event_hash,source,observed_at_ms,effective_at_ms,received_at_ms,
                        availability,reliability,coverage,confidence,field_semantics_confirmed,
                        raw_text,display_unit,parsed_count,approximate,text_position_json,
                        ocr_lines_json,reason
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    sid, event_hash, body["source"], observed, effective, received_at_ms,
                    body["availability"], body["reliability"], body["coverage"],
                    body.get("confidence"), int(bool(content.get("field_semantics_confirmed"))),
                    raw_text, count_details["display_unit"] if count_details else content.get("display_unit"),
                    parsed, int(bool(count_details and count_details["approximate"])),
                    json.dumps(content["text_position"], ensure_ascii=False) if content.get("text_position") else None,
                    json.dumps(content.get("ocr_lines", []), ensure_ascii=False), result["reason"],
                ))
        if batch is not None:
            batch["indices"][body["line_index"]] = event_hash
            if usable:
                item = dict(body)
                item.update(received_at_ms=received_at_ms, effective_at_ms=effective,
                            normalized_count=None)
                batch["rows"].append(item)
            if len(batch["indices"]) == batch["count"]:
                batch["complete"] = True
                rows = batch["rows"]
                batch["rows"] = []
                if rows and received_at_ms <= batch["deadline"]:
                    observed_rows = [row for row in rows if row.get("observed_at_ms") is not None]
                    batch["records"] = reconstruct_comments(observed_rows)
                    window = self.fusion_windows.setdefault(sid, deque(maxlen=MAX_WINDOW_OBSERVATIONS))
                    window.extend(rows)
                    result["_batch_ready"] = True
                    if usable:
                        result["reason"] = "windowed"
            return result
        if usable:
            item = dict(body)
            item["received_at_ms"] = received_at_ms
            item["effective_at_ms"] = effective
            item["normalized_count"] = parsed if body["kind"] == "online_count" else None
            window = self.fusion_windows.setdefault(sid, deque(maxlen=MAX_WINDOW_OBSERVATIONS))
            window.append(item)
            cutoff = received_at_ms - WINDOW_MS
            self.fusion_windows[sid] = deque(
                (row for row in window if row["effective_at_ms"] >= cutoff),
                maxlen=MAX_WINDOW_OBSERVATIONS,
            )
        return result

    def session_summary(self, sid: str, token: str | None):
        session = self.authorize(sid, token)
        audio_status = self.db.execute(
            "SELECT background_analysis_busy_count FROM live_audio_fusion_status WHERE session_id=?", (sid,)
        ).fetchone()
        rows = self.db.execute(
            "SELECT * FROM live_count_samples WHERE session_id=? ORDER BY source,effective_at_ms,received_at_ms",
            (sid,),
        ).fetchall()
        by_source: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            by_source.setdefault(row["source"], []).append(row)
        summaries = []
        for source, samples in by_source.items():
            valid = [sample for sample in samples if (
                sample["reason"] == "windowed" and sample["parsed_count"] is not None
                and sample["field_semantics_confirmed"] and sample["observed_at_ms"] is not None
            )]
            peak_sample = max(valid, key=lambda sample: sample["parsed_count"]) if valid else None
            covered_ms = 0
            weighted_count_ms = 0
            average_approximate = False
            uncovered_ms = 0
            uncovered_intervals = 0
            longest_uncovered_ms = 0
            for left, right in zip(samples, samples[1:]):
                gap = right["effective_at_ms"] - left["effective_at_ms"]
                if gap <= 0:
                    continue
                continuous = (
                    left["reason"] == right["reason"] == "windowed"
                    and left["parsed_count"] is not None and right["parsed_count"] is not None
                    and left["field_semantics_confirmed"] and right["field_semantics_confirmed"]
                    and left["observed_at_ms"] is not None and right["observed_at_ms"] is not None
                    and gap <= MAX_OBSERVATION_AGE_MS
                )
                if continuous:
                    covered_ms += gap
                    weighted_count_ms += left["parsed_count"] * gap
                    average_approximate = average_approximate or bool(
                        left["approximate"] or right["approximate"]
                    )
                else:
                    uncovered_ms += gap
                    uncovered_intervals += 1
                    longest_uncovered_ms = max(longest_uncovered_ms, gap)
            average = (round(weighted_count_ms / covered_ms, 2) if covered_ms else None)
            first_valid = valid[0] if valid else None
            last_valid = valid[-1] if valid else None
            issue_counts = Counter(sample["reason"] for sample in samples if sample["reason"] != "windowed")
            summaries.append({
                "source": source,
                "source_verification": (
                    "declared_fixture_input" if source.startswith("fixture:")
                    else "unverified_client_source"
                ),
                "status": "unknown" if not valid else ("available" if average is not None else "partial"),
                "unit": "person",
                "peak": ({
                    "nominal_value": peak_sample["parsed_count"],
                    "raw_text": peak_sample["raw_text"], "approximate": bool(peak_sample["approximate"]),
                    "display_unit": peak_sample["display_unit"] or "未标单位",
                    "text_position": json.loads(peak_sample["text_position_json"])
                    if peak_sample["text_position_json"] else None,
                    "ocr_lines": json.loads(peak_sample["ocr_lines_json"] or "[]"),
                    "observed_at_ms": peak_sample["observed_at_ms"],
                    "confidence": peak_sample["confidence"],
                } if peak_sample else None),
                "time_weighted_average": ({
                    "nominal_value": average, "approximate": average_approximate,
                    "method": "previous_sample_hold_between_valid_observations",
                } if average is not None else None),
                "coverage": {
                    "recorded_samples": len(samples), "valid_value_samples": len(valid),
                    "first_valid_at_ms": first_valid["observed_at_ms"] if first_valid else None,
                    "last_valid_at_ms": last_valid["observed_at_ms"] if last_valid else None,
                    "covered_duration_ms": covered_ms,
                    "uncovered_between_samples_ms": uncovered_ms,
                    "uncovered_intervals": uncovered_intervals,
                    "longest_uncovered_interval_ms": longest_uncovered_ms,
                    "uncovered_before_first_valid_ms": (
                        max(0, first_valid["observed_at_ms"] - session["started_at_ms"])
                        if first_valid else None
                    ),
                    "uncovered_after_last_valid_ms": (
                        max(0, (session["stopped_at_ms"] or self.clock()) - last_valid["observed_at_ms"])
                        if last_valid else None
                    ),
                    "sample_issues": dict(issue_counts),
                },
            })
        if len(summaries) == 1:
            count_summary = summaries[0]
        elif not summaries:
            count_summary = {
                "status": "unknown", "unit": "person", "peak": None,
                "time_weighted_average": None,
                "coverage": {"recorded_samples": 0, "valid_value_samples": 0,
                             "covered_duration_ms": 0, "sample_issues": {}},
            }
        else:
            count_summary = {
                "status": "multiple_sources", "unit": "person", "peak": None,
                "time_weighted_average": None,
                "coverage": {"source_count": len(summaries)},
            }
        task = self.db.execute(
            "SELECT * FROM live_backstage_tasks WHERE session_id=?", (sid,)
        ).fetchone()
        backstage = None
        if task:
            result = json.loads(task["result_json"]) if task["result_json"] else None
            backstage = {
                "task_id": task["task_id"], "status": task["status"],
                "douyin_account": task["douyin_account"],
                "stop_signal_received": task["stopped_at_ms"] is not None,
                "started_at_ms": session["started_at_ms"],
                "stopped_at_ms": task["stopped_at_ms"],
                "claimed_at_ms": task["claimed_at_ms"],
                "received_at_ms": task["received_at_ms"],
                "room_id": result.get("room_id") if result else None,
                "metrics": result.get("metrics") if result else None,
                "raw_displays": result.get("raw_displays") if result else None,
                "approximate_metrics": result.get("approximate_metrics") if result else [],
                "metrics_captured_at": result.get("metrics_captured_at") if result else None,
                "capture_method": result.get("capture_method") if result else None,
                "value_semantics": result.get("value_semantics") if result else None,
                "missing_fields": result.get("missing_fields") if result else {},
                "start_signal_source": task["start_signal_source"],
                "stop_signal_source": task["stop_signal_source"],
            }
        return {
            "session_id": sid, "started_at_ms": session["started_at_ms"],
            "stopped_at_ms": session["stopped_at_ms"],
            "online_count": count_summary,
            "online_count_by_source": summaries,
            "audio_fusion": {"background_analysis_busy_count":
                             audio_status["background_analysis_busy_count"] if audio_status else 0},
            "backstage_recap": backstage,
        }

    def record_audio_fusion_busy(self, sid: str, token: str | None):
        self.authorize(sid, token)
        with self.db:
            self.db.execute("""
                INSERT INTO live_audio_fusion_status (session_id, background_analysis_busy_count)
                VALUES (?, 1)
                ON CONFLICT(session_id) DO UPDATE SET background_analysis_busy_count =
                    MIN(background_analysis_busy_count + 1, 9007199254740991)
            """, (sid,))

    def claim_backstage_task(self):
        task = self.db.execute("""
            SELECT t.*,s.stopped_at_ms AS session_stopped_at_ms
            FROM live_backstage_tasks t JOIN live_sessions s USING(session_id)
            WHERE t.result_hash IS NULL AND t.status IN ('waiting_for_stop','capturing')
            ORDER BY t.created_at_ms LIMIT 1
        """).fetchone()
        if task is None:
            return {"status": "idle", "task": None}
        now = self.clock()
        status = "capturing" if task["session_stopped_at_ms"] is not None else "waiting_for_stop"
        with self.db:
            updated = self.db.execute(
                "UPDATE live_backstage_tasks SET claimed_at_ms=COALESCE(claimed_at_ms,?),status=? "
                "WHERE task_id=? AND result_hash IS NULL",
                (now, status, task["task_id"]),
            ).rowcount
        if not updated:
            return {"status": "idle", "task": None}
        return {"status": "claimed", "task": self.backstage_task(task["task_id"])["task"]}

    def backstage_task(self, task_id: str):
        task = self.db.execute("""
            SELECT t.*,s.stopped_at_ms AS session_stopped_at_ms
            FROM live_backstage_tasks t JOIN live_sessions s USING(session_id)
            WHERE t.task_id=?
        """, (task_id,)).fetchone()
        if task is None:
            raise HTTPException(404, "backstage task not found")
        result = json.loads(task["result_json"]) if task["result_json"] else None
        return {"status": "ok", "task": {
            "task_id": task["task_id"], "session_id": task["session_id"],
            "douyin_account": task["douyin_account"], "status": task["status"],
            "start_signal_source": task["start_signal_source"],
            "stop_signal_source": task["stop_signal_source"],
            "stop_signal_received": task["session_stopped_at_ms"] is not None,
            "stopped_at_ms": task["session_stopped_at_ms"],
            "result_status": result.get("status") if result else None,
        }}

    def save_backstage_result(self, sid: str, payload: dict):
        task = self.db.execute(
            "SELECT * FROM live_backstage_tasks WHERE session_id=?", (sid,)
        ).fetchone()
        if task is None:
            raise HTTPException(404, "backstage task not found")
        session = self.db.execute(
            "SELECT stopped_at_ms FROM live_sessions WHERE session_id=?", (sid,)
        ).fetchone()
        if (payload["task_id"] != task["task_id"]
                or payload["douyin_account"] != task["douyin_account"]):
            raise HTTPException(409, "backstage task identity mismatch")
        if (payload["start_signal_source"] != task["start_signal_source"]
                or payload["stop_signal_source"] != task["stop_signal_source"]):
            raise HTTPException(409, "backstage app signal source mismatch")
        result_hash = digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        if task["result_hash"]:
            if task["result_hash"] == result_hash:
                return {"status": "duplicate", "task_id": task["task_id"],
                        "result_status": task["status"]}
            raise HTTPException(409, "backstage task already has a different result")
        if session["stopped_at_ms"] is None:
            raise HTTPException(409, "backstage result requires stopped session")
        if task["claimed_at_ms"] is None or task["status"] != "capturing":
            raise HTTPException(409, "backstage task has not been claimed")
        received_at = self.clock()
        with self.db:
            self.db.execute(
                "UPDATE live_backstage_tasks SET status=?,received_at_ms=?,result_hash=?,result_json=? "
                "WHERE task_id=?",
                (payload["status"], received_at, result_hash,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True), task["task_id"]),
            )
        return {"status": "accepted", "task_id": task["task_id"],
                "result_status": payload["status"], "received_at_ms": received_at}

    def fusion_candidate(self, sid: str, token: str | None):
        session = self.authorize(sid, token)
        if session["stopped_at_ms"] is not None:
            return None
        now = self.clock()
        rows = [row for row in self.fusion_windows.get(sid, ())
                if now - MAX_OBSERVATION_AGE_MS <= row["effective_at_ms"] <= now + FUSION_FUTURE_SKEW_MS]
        # 带完整性门控的原始 OCR 项须先经评论重建，不能退回旧单句模型路径。
        legacy_rows = [row for row in rows if not (
            row["kind"] == "comment" and row.get("capture_comment_count") is not None
        )]
        candidate = build_candidate(legacy_rows)
        if candidate is None:
            return None
        topic_hash = digest(candidate["topic"])
        last_same_topic = self.db.execute(
            "SELECT MAX(generated_at_ms) FROM live_events WHERE session_id=? AND topic_hash=?",
            (sid, topic_hash),
        ).fetchone()[0]
        topic_cooldown = 90_000 if candidate["topic"] == "person:head_down" else COOLDOWN_MS
        if last_same_topic is not None and now - last_same_topic < topic_cooldown:
            return None
        if candidate["priority"] < 100:
            recent = self.db.execute(
                "SELECT payload FROM live_events WHERE session_id=? AND generated_at_ms>?",
                (sid, now - COOLDOWN_MS),
            ).fetchall()
            if any(json.loads(row["payload"]).get("priority", 0) > candidate["priority"]
                   for row in recent):
                return None
        candidate["description"] = session["description"]
        return candidate

    def save_fusion_candidate(self, sid: str, token: str | None,
                              candidate: dict, suggestion: str):
        session = self.authorize(sid, token)
        if session["stopped_at_ms"] is not None:
            raise HTTPException(409, "live session stopped")
        now = self.clock()
        rows = candidate["observations"]
        latest = max(row["effective_at_ms"] for row in rows)
        if now - latest > MAX_OBSERVATION_AGE_MS:
            return {"reason": "candidate_expired", "prompt_id": None}
        topic_hash = digest(candidate["topic"])
        last = self.db.execute(
            "SELECT MAX(generated_at_ms) FROM live_events WHERE session_id=? AND topic_hash=?",
            (sid, topic_hash),
        ).fetchone()[0]
        topic_cooldown = 90_000 if candidate["topic"] == "person:head_down" else COOLDOWN_MS
        if last is not None and now-last < topic_cooldown:
            return {"reason": "cooldown", "prompt_id": None}
        if candidate["priority"] < 100:
            higher = self.db.execute(
                "SELECT payload FROM live_events WHERE session_id=? AND generated_at_ms>?",
                (sid, now-COOLDOWN_MS),
            ).fetchall()
            if any(json.loads(row["payload"]).get("priority", 0) > candidate["priority"]
                   for row in higher):
                return {"reason": "suppressed_by_priority", "prompt_id": None}
        pending = self.db.execute(
            "SELECT COUNT(*) FROM live_events WHERE session_id=? AND returned_at_ms IS NULL AND expires_at_ms>?",
            (sid, now),
        ).fetchone()[0]
        total = self.db.execute(
            "SELECT COUNT(*) FROM live_events WHERE session_id=?", (sid,)
        ).fetchone()[0]
        if pending >= MAX_PENDING:
            return {"reason": "pending_limit", "prompt_id": None}
        if total >= MAX_EVENTS:
            raise HTTPException(429, "live event limit reached")

        start_at = min(row.get("started_at_ms") or row["effective_at_ms"] for row in rows)
        end_at = max(row.get("ended_at_ms") or row["effective_at_ms"] for row in rows)
        first_received = min(row["received_at_ms"] for row in rows)
        last_received = max(row["received_at_ms"] for row in rows)
        sources = []
        for row in rows:
            item = {
                "kind": row["kind"], "source": row["source"], "coverage": row["coverage"],
                "reliability": row["reliability"], "confidence": row.get("confidence"),
                "time_basis": "observed" if row.get("observed_at_ms") is not None else "received",
                "effective_at_ms": row["effective_at_ms"],
                "received_at_ms": row["received_at_ms"],
            }
            if row.get("observed_at_ms") is not None:
                item["observed_at_ms"] = row["observed_at_ms"]
            if row.get("started_at_ms") is not None:
                item["started_at_ms"] = row["started_at_ms"]
            if row.get("ended_at_ms") is not None:
                item["ended_at_ms"] = row["ended_at_ms"]
            if row["kind"] == "platform_notice":
                item["evidence_text"] = row["content"]["text"]
                if row["content"].get("text_position"):
                    item["text_position"] = row["content"]["text_position"]
                if row["content"].get("ocr_lines"):
                    item["ocr_lines"] = row["content"]["ocr_lines"]
                item["source_verification"] = (
                    "declared_fixture_input" if row["source"].startswith("fixture:")
                    else "unverified_client_observation"
                )
            elif row["source"].startswith("fixture:"):
                item["source_verification"] = "declared_fixture_input"
            else:
                item["source_verification"] = "unverified_client_source"
            if row["kind"] == "comment" and row["content"].get("text_position"):
                item["text_position"] = row["content"]["text_position"]
            if row["kind"] == "person_state":
                item["count"] = row["content"].get("sample_count", 1)
            if row["kind"] == "online_count":
                raw_count = row["content"]["raw_text"]
                item["client_reported_field_semantics"] = row["content"]["field_semantics_confirmed"]
                item["raw_count_text"] = raw_count
                item["parsed_count"] = row.get("normalized_count")
                details = online_count_details(raw_count, field_semantics_confirmed=True)
                item["approximate"] = bool(details and details["approximate"])
                item["display_unit"] = details["display_unit"] if details else row["content"].get("display_unit")
                if row["content"].get("text_position"):
                    item["text_position"] = row["content"]["text_position"]
                if row["content"].get("ocr_lines"):
                    item["ocr_lines"] = row["content"]["ocr_lines"]
            if row["kind"] == "transcript":
                item["is_final"] = row["content"]["is_final"]
                item["revision"] = row["content"]["revision"]
                item["supersedes_event_id"] = row["content"].get("supersedes_event_id")
                if row["content"].get("audio_evidence"):
                    item["audio_evidence"] = row["content"]["audio_evidence"]
                    item["time_basis"] = row["content"]["audio_evidence"]["time_basis"]
                item["evidence_text"] = row["content"]["text"][:120]
            sources.append(item)
        event = {
            "prompt_id": uuid.uuid4().hex, "input_mode": "live_observation",
            "first_received_at_ms": first_received, "last_received_at_ms": last_received,
            "start_at_ms": start_at, "end_at_ms": end_at,
            "start_offset_ms": start_at - session["started_at_ms"],
            "end_offset_ms": end_at - session["started_at_ms"],
            "time_basis": (sources[0]["time_basis"] if len({s["time_basis"] for s in sources}) == 1 else "mixed"),
            "phenomenon": candidate["phenomenon"], "suggestion": suggestion,
            "display_text": suggestion,
            "observation_count": sum(
                row["content"].get("sample_count", 1) if row["kind"] == "person_state" else 1
                for row in rows
            ),
            "sources": sources, "priority": candidate["priority"],
        }
        if candidate.get("prompt_type") == "comment_reply":
            event["prompt_type"] = "comment_reply"
            event["reply_to"] = candidate["reply_to"]
        elif candidate.get("prompt_type") == "operation_notice":
            event["prompt_type"] = "operation_notice"
        with self.db:
            self.db.execute(
                "INSERT INTO live_events (session_id,topic_hash,generated_at_ms,expires_at_ms,payload) VALUES (?,?,?,?,?)",
                (sid, topic_hash, now, now + PROMPT_TTL_MS,
                 json.dumps(event, ensure_ascii=False)),
            )
        return {"reason": "generated", "prompt_id": event["prompt_id"]}

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
        self._expire_comment_payloads(sid, now)
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
        if message is None and topic is not None and body["input_mode"] == "live_observation":
            message = digest(json.dumps([body["source"], "ocr-topic", topic[0], when // 10_000]))
        author = digest(json.dumps([body["source"], body["author_id"]])) if body["author_id"] else None
        reason = None
        if body["reliability"] != "known" or body["coverage"] == "unknown":
            reason = "unreliable"
        elif body["coverage"] == "ocr_visible_sample" and body["input_mode"] != "live_observation":
            reason = "ocr_tracking_unavailable"
        elif now-when > MAX_OBSERVATION_AGE_MS:
            reason = "late"
        elif not message and not author and body["input_mode"] != "live_observation":
            reason = "no_independent_evidence"
        elif message and self.db.execute(
            "SELECT 1 FROM live_observations WHERE session_id=? AND message_hash=?", (sid, message)
        ).fetchone():
            reason = "duplicate_message"
        elif topic is None:
            reason = "not_a_question"
        elif not message and author and any(o["topic"] == topic[0] and o["author"] == author for o in window):
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
            if body["input_mode"] == "live_observation":
                # OCR has no platform message/author identity. A stable content key
                # suppresses frame replay without claiming independent viewers.
                latest = self.db.execute(
                    "SELECT MAX(generated_at_ms) FROM live_events WHERE session_id=? AND topic_hash=?",
                    (sid, digest(topic[0])),
                ).fetchone()[0]
                if latest is not None and now-latest < COOLDOWN_MS:
                    result["reason"] = "cooldown"
                else:
                    result["candidate"] = topic[1]
            elif Counter(o["topic"] for o in window)[topic[0]] >= 2:
                result.update(self.generate(sid, session, topic, group, now))
        self.db.execute("INSERT INTO live_observations VALUES (?,?,?,?,?)",
                        (sid, key, fingerprint, message, json.dumps(result)))
        return result, window

    def save_live_prompt(self, sid: str, token: str | None, body: dict,
                         observed_at_ms: int, suggestion: str):
        session = self.authorize(sid, token)
        if session["stopped_at_ms"] is not None:
            raise HTTPException(409, "live session stopped")
        topic = question(body["text"])
        if topic is None:
            return {"reason": "not_a_question"}
        now = self.clock()
        topic_hash = digest(topic[0])
        last = self.db.execute(
            "SELECT MAX(generated_at_ms) FROM live_events WHERE session_id=? AND topic_hash=?",
            (sid, topic_hash),
        ).fetchone()[0]
        if last is not None and now-last < COOLDOWN_MS:
            return {"reason": "cooldown"}
        pending = self.db.execute(
            "SELECT COUNT(*) FROM live_events WHERE session_id=? AND returned_at_ms IS NULL AND expires_at_ms>?",
            (sid, now),
        ).fetchone()[0]
        total = self.db.execute(
            "SELECT COUNT(*) FROM live_events WHERE session_id=?", (sid,)
        ).fetchone()[0]
        if pending >= MAX_PENDING:
            return {"reason": "pending_limit"}
        if total >= MAX_EVENTS:
            raise HTTPException(429, "live event limit reached")
        event = {
            "prompt_id": uuid.uuid4().hex,
            "input_mode": "live_observation",
            "first_received_at_ms": now,
            "last_received_at_ms": now,
            "start_at_ms": observed_at_ms,
            "end_at_ms": observed_at_ms,
            "start_offset_ms": observed_at_ms-session["started_at_ms"],
            "end_offset_ms": observed_at_ms-session["started_at_ms"],
            "time_basis": "observed",
            "phenomenon": f"公屏出现一条可见问题：{topic[1]}",
            "suggestion": suggestion,
            "display_text": suggestion,
            "observation_count": 1,
            "sources": [{"source": body["source"], "coverage": body["coverage"],
                         "time_basis": "observed", "input_mode": "live_observation",
                         "reliability": body["reliability"],
                         "confidence": body.get("confidence"),
                         "text_position": body.get("text_position"), "count": 1}],
        }
        with self.db:
            self.db.execute(
                "INSERT INTO live_events (session_id,topic_hash,generated_at_ms,expires_at_ms,payload) VALUES (?,?,?,?,?)",
                (sid, topic_hash, now, now+PROMPT_TTL_MS, json.dumps(event, ensure_ascii=False)),
            )
        return {"reason": "generated", "prompt_id": event["prompt_id"]}

    def add_signal(self, sid: str, token: str | None, body: dict, received_at_ms: int):
        session = self.authorize(sid, token)
        if session["stopped_at_ms"] is not None:
            raise HTTPException(409, "live session stopped")
        if not observation_source_allowed(
            "person_state", body["source"], session["app_source"] or "ios_app",
        ):
            raise HTTPException(422, "person signal source does not match app session")
        if not (session["started_at_ms"] <= body["started_at_ms"] <= body["observed_at_ms"] <= received_at_ms):
            raise HTTPException(422, "signal time outside live session")
        key = digest(body["event_id"])
        fingerprint = digest(json.dumps(body, ensure_ascii=False, sort_keys=True))
        prior = self.db.execute(
            "SELECT fingerprint,result FROM live_observations WHERE session_id=? AND event_hash=?", (sid, key)
        ).fetchone()
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise HTTPException(409, "observation id already used with different content")
            return json.loads(prior["result"])
        count = self.db.execute(
            "SELECT COUNT(*) FROM live_observations WHERE session_id=?", (sid,)
        ).fetchone()[0]
        if count >= MAX_OBSERVATIONS:
            raise HTTPException(429, "live observation limit reached")
        now = self.clock()
        topic_hash = digest("person:head_down")
        last = self.db.execute(
            "SELECT MAX(generated_at_ms) FROM live_events WHERE session_id=? AND topic_hash=?", (sid, topic_hash)
        ).fetchone()[0]
        event_row = None
        if last is not None and now-last < 90_000:
            result = {"status": "suppressed", "reason": "cooldown", "prompt_id": None}
        else:
            total = self.db.execute("SELECT COUNT(*) FROM live_events WHERE session_id=?", (sid,)).fetchone()[0]
            if total >= MAX_EVENTS:
                raise HTTPException(429, "live event limit reached")
            phenomenon = "画面中可见人物持续低头"
            suggestion = "抬头看一下镜头，接住观众的互动。"
            event = {
                "prompt_id": uuid.uuid4().hex, "input_mode": "live_observation",
                "first_received_at_ms": received_at_ms, "last_received_at_ms": received_at_ms,
                "start_at_ms": body["started_at_ms"], "end_at_ms": body["observed_at_ms"],
                "start_offset_ms": body["started_at_ms"]-session["started_at_ms"],
                "end_offset_ms": body["observed_at_ms"]-session["started_at_ms"],
                "time_basis": "observed", "phenomenon": phenomenon,
                "suggestion": suggestion, "display_text": suggestion,
                "observation_count": body["sample_count"],
                "sources": [{"source": body["source"], "coverage": "vision_pose_sample",
                             "time_basis": "observed", "input_mode": "live_observation",
                             "reliability": "known", "confidence": body["confidence"],
                             "count": body["sample_count"]}],
            }
            event_row = (sid, topic_hash, now, now+PROMPT_TTL_MS,
                         json.dumps(event, ensure_ascii=False))
            result = {"status": "accepted", "reason": "generated", "prompt_id": event["prompt_id"]}
        with self.db:
            if event_row is not None:
                self.db.execute(
                    "INSERT INTO live_events (session_id,topic_hash,generated_at_ms,expires_at_ms,payload) VALUES (?,?,?,?,?)",
                    event_row,
                )
            self.db.execute("INSERT INTO live_observations VALUES (?,?,?,?,?)",
                            (sid, key, fingerprint, None, json.dumps(result)))
        return result

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
