"""独立运营路由；显式区分测试 fixture 与 iOS 实时观察。"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .live_rules import (
    FUSION_FUTURE_SKEW_MS, FUSION_SETTLE_MS, MAX_OBSERVATION_AGE_MS,
    MAX_SESSION_WAITERS, MAX_WAITERS, MAX_WAIT_MS,
)
from .live_store import LiveStore, digest
from .model_client import ModelTimeoutError, ModelUnavailableError
from .live_fusion import (
    Observation, ObservationSource, TEST_COMMENT_FIXTURE_SOURCES,
    observation_source_allowed,
)
from .person_observation import PersonObservation as PersonObservationValue, PersonPolicy

router = APIRouter(prefix="/v1/live/sessions", tags=["live-test-fixture"])
logger = logging.getLogger("screen_assistant.app")


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field(default="", max_length=500)
    douyin_account: str | None = Field(default=None, min_length=1, max_length=128)


class SessionSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal["ios_app", "android_app"]


BACKSTAGE_METRIC_KEYS = ("累计观众", "新增粉丝", "评论人数", "送礼人数", "获得音浪")


class BackstageResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    task_id: str = Field(min_length=1, max_length=64)
    douyin_account: str = Field(min_length=1, max_length=128)
    status: Literal["captured_locally", "partial", "needs_input", "failed"]
    room_id: str | None = Field(default=None, max_length=128)
    metrics: dict[str, int | None]
    raw_displays: dict[str, str] = Field(default_factory=dict, max_length=5)
    approximate_metrics: list[str] = Field(default_factory=list, max_length=5)
    metrics_captured_at: str | None = Field(default=None, max_length=64)
    stop_signal_received: bool
    start_signal_source: Literal["ios_app", "android_app"]
    stop_signal_source: Literal["ios_app", "android_app"]
    capture_method: str = Field(min_length=1, max_length=160)
    value_semantics: str = Field(min_length=1, max_length=200)
    missing_fields: dict[str, str] = Field(default_factory=dict, max_length=5)

    @field_validator("raw_displays", "missing_fields")
    @classmethod
    def validate_short_field_values(cls, value):
        if any(len(item) > 160 for item in value.values()):
            raise ValueError("field text exceeds the recap limit")
        return value

    @model_validator(mode="after")
    def validate_result(self):
        if set(self.metrics) != set(BACKSTAGE_METRIC_KEYS):
            raise ValueError("metrics must contain exactly the five recap fields")
        if any(value is not None and (value < 0 or value > 2**53) for value in self.metrics.values()):
            raise ValueError("metric values must be non-negative integers or null")
        if any(key not in BACKSTAGE_METRIC_KEYS for key in self.raw_displays):
            raise ValueError("raw display contains an unknown metric")
        if any(key not in BACKSTAGE_METRIC_KEYS for key in self.approximate_metrics):
            raise ValueError("approximation contains an unknown metric")
        if any(key not in BACKSTAGE_METRIC_KEYS for key in self.missing_fields):
            raise ValueError("missing-field map contains an unknown metric")
        if self.status == "captured_locally" and any(value is None for value in self.metrics.values()):
            raise ValueError("captured_locally requires all five values")
        if not self.stop_signal_received:
            raise ValueError("backstage result requires the app stop signal")
        if any(value is not None for value in self.metrics.values()):
            if self.metrics_captured_at is None:
                raise ValueError("captured metrics require a timestamp")
        if self.metrics_captured_at is not None:
            from datetime import datetime
            try:
                captured = datetime.fromisoformat(self.metrics_captured_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("metrics_captured_at must be ISO-8601") from exc
            if captured.tzinfo is None:
                raise ValueError("metrics_captured_at must include a timezone")
        return self


class TextPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)


class CommentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event_id: str = Field(min_length=1, max_length=128)
    input_mode: Literal["test_fixture", "live_observation"]
    source: ObservationSource
    coverage: Literal["structured_sample", "ocr_visible_sample", "unknown"]
    reliability: Literal["known", "unknown"] = "unknown"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    text_position: TextPosition | None = None
    text: str = Field(min_length=1, max_length=500)
    author_id: str | None = Field(default=None, min_length=1, max_length=128)
    platform_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    observed_at_ms: int | None = Field(default=None, ge=0, le=2**63-1, strict=True)

    @model_validator(mode="after")
    def validate_comment_source(self):
        if self.input_mode == "test_fixture":
            if self.source not in TEST_COMMENT_FIXTURE_SOURCES:
                raise ValueError("comment fixture source is not registered")
        elif self.source not in {"ios_replaykit_ocr", "android_media_projection_ocr"}:
            raise ValueError("live comment source is not registered")
        return self


class PersonSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event_id: str = Field(min_length=1, max_length=128)
    source: Literal[
        "ios_replaykit_vision", "android_vision_person",
        "android_mediaprojection_mlkit_pose",
    ]
    signal: Literal["head_down"]
    confidence: float = Field(ge=0.5, le=1.0)
    sample_count: int = Field(ge=5, le=100)
    started_at_ms: int = Field(ge=0, le=2**63-1, strict=True)
    observed_at_ms: int = Field(ge=0, le=2**63-1, strict=True)


class PersonObservationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal[
        "ios_replaykit_vision", "android_vision_person",
        "android_mediaprojection_mlkit_pose",
    ]
    coverage: Literal["sampled_person_roi"]
    segment_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0, le=2**53)
    observed_at_ms: int = Field(ge=0, le=2**53)
    uptime_ms: int = Field(ge=0, le=2**53)
    quality: str = Field(min_length=1, max_length=64)
    person_count: int | None = Field(default=None, ge=0, le=10)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    person_box: list[float] | None = Field(default=None, max_length=4)
    body_vector: list[float] | None = Field(default=None, max_length=10)
    face_vector: list[float] | None = Field(default=None, max_length=3)
    head_ratio: float | None = Field(default=None, allow_inf_nan=False)
    head_confidence: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    pts_seconds: float | None = Field(default=None, allow_inf_nan=False)


@dataclass
class PersonSessionState:
    segment_id: str
    policy: PersonPolicy
    last_sequence: int
    last_uptime_ms: int
    last_observed_at_ms: int
    last_fingerprint: str
    retired_segments: deque[str]


class LiveService:
    def __init__(self, path: str, clock, *, concurrency: int = 2, queue_limit: int = 16):
        self.path, self.clock = path, clock
        self.store: LiveStore | None = None
        self.lock = asyncio.Lock()
        self.fusion_slots = asyncio.Semaphore(concurrency)
        self.fusion_concurrency = concurrency
        self.fusion_queue_limit = queue_limit
        self.fusion_queue = deque()
        self.fusion_queued = {}
        self.fusion_active = {}
        self.fusion_dirty = set()
        self.fusion_audio_pending = {}
        self.fusion_stopping = set()
        self.fusion_closed = False
        self.waiters: dict[str, set[asyncio.Future]] = {}
        self.person_states: dict[str, PersonSessionState] = {}

    def schedule_fusion(self, sid, token, request):
        if self.fusion_closed or sid in self.fusion_stopping:
            return False
        active = self.fusion_active.get(sid)
        if active is not None and active.done():
            self._fusion_done(sid, active)
            active = None
        if active is not None:
            self.fusion_dirty.add(sid)
            logger.info("live_fusion outcome=coalesced")
            return True
        if sid in self.fusion_queued:
            logger.info("live_fusion outcome=coalesced")
            return True
        if len(self.fusion_active) >= self.fusion_concurrency and len(self.fusion_queue) >= self.fusion_queue_limit:
            logger.info("live_fusion outcome=busy")
            return False
        self.fusion_queued[sid] = (token, request)
        self.fusion_queue.append(sid)
        self._pump_fusion()
        return True

    def schedule_audio_fusion(self, sid, token, request):
        scheduled = self.schedule_fusion(sid, token, request)
        if scheduled:
            # 每次有效音频触发使用新身份，旧执行不能清除随后到达的音频。
            self.fusion_audio_pending[sid] = object()
        return scheduled

    def _pump_fusion(self):
        while not self.fusion_closed and self.fusion_queue and len(self.fusion_active) < self.fusion_concurrency:
            sid = self.fusion_queue.popleft()
            token, request = self.fusion_queued.pop(sid)
            task = asyncio.create_task(self._fusion_worker(sid, token, request))
            self.fusion_active[sid] = task
            task.add_done_callback(lambda task, sid=sid: self._fusion_done(sid, task))

    def _fusion_done(self, sid, task):
        if self.fusion_active.get(sid) is not task:
            return
        self.fusion_active.pop(sid, None)
        self.fusion_dirty.discard(sid)
        self.fusion_audio_pending.pop(sid, None)
        self._pump_fusion()

    async def _fusion_worker(self, sid, token, request):
        try:
            while True:
                self.fusion_dirty.discard(sid)
                if FUSION_SETTLE_MS:
                    await asyncio.sleep(FUSION_SETTLE_MS / 1000)
                audio_generation = self.fusion_audio_pending.get(sid)
                try:
                    await self.deliver_fusion(sid, token, request)
                except Exception:
                    logger.warning("live_fusion outcome=failed")
                finally:
                    if audio_generation is not None and self.fusion_audio_pending.get(sid) is audio_generation:
                        self.fusion_audio_pending.pop(sid, None)
                if sid not in self.fusion_dirty or sid in self.fusion_stopping:
                    break
        except asyncio.CancelledError:
            logger.info("live_fusion outcome=cancelled")
            raise

    async def stop_fusion(self, sid):
        self.fusion_dirty.discard(sid)
        if sid in self.fusion_queued:
            self.fusion_queued.pop(sid)
            self.fusion_queue.remove(sid)
        task = self.fusion_active.get(sid)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close_fusion(self):
        self.fusion_closed = True
        self.fusion_queue.clear()
        self.fusion_queued.clear()
        self.fusion_audio_pending.clear()
        tasks = list(self.fusion_active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def dispatch(self, method: str, *args):
        # 延迟初始化，使新存储不可写时不影响旧问答/健康检查的生命周期。
        if method == "close":
            for state in self.person_states.values():
                state.policy.stop()
            self.person_states.clear()
            if self.store is not None:
                self.store.close()
                self.store = None
            return None
        if self.store is None:
            self.store = LiveStore(self.path, self.clock)
        return getattr(self.store, method)(*args)

    async def call(self, method: str, *args):
        task = asyncio.create_task(asyncio.to_thread(self.dispatch, method, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except (sqlite3.Error, OSError):
            raise HTTPException(503, "live storage unavailable") from None

    async def run(self, method: str, *args):
        async with self.lock:
            # 请求取消时等待正在进行的数据库操作结束，再释放锁/关闭连接。
            result = await self.call(method, *args)
            if method == "stop":
                state = self.person_states.pop(args[0], None)
                if state is not None:
                    state.policy.stop()
            if method == "stop" or (method in {
                "add_comment", "save_live_prompt", "add_signal", "save_fusion_candidate"
            } and result.get("prompt_id")):
                for future in self.waiters.get(args[0], ()):
                    if not future.done():
                        future.set_result(None)
            return result

    async def observe_person(self, sid: str, token: str | None,
                             payload: dict, received_at_ms: int):
        """Run one bounded numeric sample through a session-owned policy."""
        async with self.lock:
            session = await self.call("audio_access", sid, token)
            if not observation_source_allowed(
                "person_state", payload.get("source", ""), session["signal_source"],
            ):
                raise HTTPException(422, "person observation source does not match app session")
            item = PersonObservationValue.from_payload(payload)
            if not item.valid():
                raise HTTPException(422, "invalid person observation")
            if (item.observed_at_ms < session["started_at_ms"]
                    or item.observed_at_ms > received_at_ms + FUSION_FUTURE_SKEW_MS):
                raise HTTPException(422, "person observation time outside live session")
            if received_at_ms - item.observed_at_ms > MAX_OBSERVATION_AGE_MS:
                return {"status": "ignored", "reason": "late_observation"}

            fingerprint = digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            state = self.person_states.get(sid)
            prior_state = copy.deepcopy(state)
            retired_segments = state.retired_segments if state is not None else deque(maxlen=64)
            if state is not None and item.segment_id in state.retired_segments:
                return {"status": "ignored", "reason": "old_segment"}
            if state is not None and item.segment_id == state.segment_id:
                if item.sequence == state.last_sequence:
                    if fingerprint == state.last_fingerprint:
                        return {"status": "duplicate", "reason": "duplicate_event"}
                    raise HTTPException(409, "person sequence reused with different content")
                if (item.sequence < state.last_sequence or item.uptime_ms <= state.last_uptime_ms
                        or item.observed_at_ms <= state.last_observed_at_ms):
                    return {"status": "ignored", "reason": "stale_person_sample"}
            elif state is not None:
                if (item.uptime_ms <= state.last_uptime_ms
                        or item.observed_at_ms <= state.last_observed_at_ms):
                    return {"status": "ignored", "reason": "old_segment"}
                retired_segments.append(state.segment_id)
                state.policy.stop()
                state = None

            if state is None:
                policy = PersonPolicy()
                policy.start(item.segment_id)
                state = PersonSessionState(
                    item.segment_id, policy, -1, -1, -1, "", retired_segments,
                )
                self.person_states[sid] = state

            decision = state.policy.observe(item)
            if decision.presence == "ignored":
                return {"status": "ignored", "reason": "stale_person_sample"}
            state.last_sequence = item.sequence
            state.last_uptime_ms = item.uptime_ms
            state.last_observed_at_ms = item.observed_at_ms
            state.last_fingerprint = fingerprint
            response = {
                "status": "observed", "reason": decision.presence,
                "presence": decision.presence, "motion": decision.motion,
                "facial_change": decision.facial_change,
            }
            candidate = decision.candidate
            if candidate is None:
                return response

            event_id = "person-candidate:" + digest(f"{item.segment_id}:{item.sequence}")
            body = {
                "kind": "person_state", "event_id": event_id,
                "source": candidate.source, "observed_at_ms": candidate.observed_at_ms,
                "started_at_ms": candidate.started_at_ms,
                "ended_at_ms": candidate.observed_at_ms,
                "availability": "available", "coverage": candidate.coverage,
                "reliability": "known", "confidence": candidate.confidence,
                "content": {"signal": candidate.kind, "sample_count": candidate.sample_count},
            }
            try:
                result = await self.call("add_observation", sid, token, body, received_at_ms)
            except Exception:
                if prior_state is None:
                    self.person_states.pop(sid, None)
                else:
                    self.person_states[sid] = prior_state
                raise
            return {"result": result, "body": body, "candidate": True}

    async def poll(self, sid, token, after, wait_ms):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_ms / 1000
        while True:
            async with self.lock:
                result = await self.call("prompts", sid, token, after)
                remaining = deadline-loop.time()
                if result["prompts"] or result["stopped"] or remaining <= 0:
                    return result
                if (sum(len(v) for v in self.waiters.values()) >= MAX_WAITERS
                        or len(self.waiters.get(sid, ())) >= MAX_SESSION_WAITERS):
                    raise HTTPException(429, "live poll waiter limit reached")
                future = loop.create_future()
                self.waiters.setdefault(sid, set()).add(future)
                after = result["next_cursor"]
            try:
                # 不持锁等待；到期后再次读数据库，覆盖截止瞬间的通知竞态。
                await asyncio.wait_for(future, remaining)
            except TimeoutError:
                pass
            finally:
                self.waiters[sid].discard(future)
                if not self.waiters[sid]:
                    del self.waiters[sid]

    async def deliver_fusion(self, sid: str, token: str | None, request: Request,
                             *, urgent: bool = False, allow_stopping: bool = False):
        async def deliver():
            candidate = await self.run("fusion_candidate", sid, token)
            if candidate is None:
                return {"reason": "quiet", "prompt_id": None}
            suggestion = candidate["suggestion"]
            if candidate["model_text"]:
                cfg = request.app.state.settings
                if not cfg.tianyi_api_key:
                    return {"reason": "model_unavailable", "prompt_id": None}
                try:
                    if candidate.get("prompt_type") == "comment_reply":
                        suggestion = await asyncio.wait_for(
                            request.app.state.model.reply_to_comment(
                                candidate["model_text"], nickname=candidate["reply_to"]["nickname"],
                                priority_eligible=candidate.get("priority_eligible", False),
                            ), timeout=cfg.model_timeout_seconds,
                        )
                    else:
                        source = ",".join(dict.fromkeys(
                            row["source"] for row in candidate["observations"]
                        ))
                        observed_at = max(row["effective_at_ms"] for row in candidate["observations"])
                        suggestion = await asyncio.wait_for(
                            request.app.state.model.suggest_live(
                                candidate["model_text"], candidate["description"], source,
                                observed_at, context=candidate["model_context"],
                            ), timeout=cfg.model_timeout_seconds,
                        )
                except (asyncio.TimeoutError, ModelTimeoutError):
                    logger.info("live_fusion outcome=timeout")
                    return {"reason": "model_unavailable", "prompt_id": None}
                except ModelUnavailableError:
                    logger.info("live_fusion outcome=model_unavailable")
                    return {"reason": "model_unavailable", "prompt_id": None}
                if candidate.get("prompt_type") == "comment_reply" and suggestion is None:
                    return {"reason": "quiet", "prompt_id": None}
                if candidate.get("prompt_type") != "comment_reply" and not valid_operation_suggestion(suggestion):
                    return {"reason": "model_output_invalid", "prompt_id": None}
            elif not suggestion or len(suggestion) > 100 or "\n" in suggestion or "\r" in suggestion:
                return {"reason": "candidate_invalid", "prompt_id": None}
            async with self.lock:
                if self.fusion_closed or (sid in self.fusion_stopping and not allow_stopping):
                    return {"reason": "cancelled", "prompt_id": None}
                result = await self.call("save_fusion_candidate", sid, token, candidate, suggestion)
                if result.get("prompt_id"):
                    for future in self.waiters.get(sid, ()):
                        if not future.done():
                            future.set_result(None)
                return result

        if urgent:
            return await deliver()
        async with self.fusion_slots:
            return await deliver()


def utc_ms() -> int:
    return time.time_ns() // 1_000_000


def valid_operation_suggestion(value: str | None) -> bool:
    if not value or len(value) > 80 or "\n" in value or "\r" in value:
        return False
    if re.search(r"(?:现象|建议)\s*[:：]", value):
        return False
    return len(re.findall(r"[。！？!?]", value)) <= 1


@router.post("", status_code=201)
async def create_session(request: Request, payload: CreateSession | None = None):
    return await request.app.state.live.run(
        "create", payload.description if payload else "",
        payload.douyin_account if payload else None,
    )


@router.post("/{session_id}/start")
async def start_session(session_id: str, request: Request,
                        payload: SessionSignal | None = None,
                        token: str | None = Header(None, alias="X-Live-Session-Token")):
    return await request.app.state.live.run(
        "start_backstage", session_id, token, payload.source if payload else "ios_app",
    )


@router.post("/{session_id}/stop")
async def stop_session(session_id: str, request: Request,
                       payload: SessionSignal | None = None,
                       token: str | None = Header(None, alias="X-Live-Session-Token")):
    # 幂等stop保留；有音频时先处理已收到的尾窗，再关闭M4收件。
    service = request.app.state.live
    async with service.lock:
        row = await service.call("authorize", session_id, token)
        app_source = await service.call("session_signal_source", session_id, token)
        if payload is not None and payload.source != app_source:
            raise HTTPException(409, "stop signal source does not match app session")
        stopped = row["stopped_at_ms"] is not None
    audio_result = None
    if session_id in service.fusion_stopping:
        raise HTTPException(409, "live session already stopping")
    service.fusion_stopping.add(session_id)
    pending_audio = service.fusion_audio_pending.pop(session_id, None) is not None
    if not stopped:
        request.app.state.audio.begin_fusion_stop(session_id)
    try:
        await service.stop_fusion(session_id)
        if not stopped:
            audio_result = await request.app.state.audio.finish_session(session_id, token)
        request.app.state.audio.closing.add(session_id)
        if not stopped and (pending_audio or request.app.state.audio.stop_transcripts.get(session_id)):
            try:
                await service.deliver_fusion(session_id, token, request, allow_stopping=True)
            except HTTPException as error:
                if error.status_code not in (429, 503):
                    raise
                logger.warning("live_fusion outcome=stop_candidate_failed status=%s", error.status_code)
        result = await service.run(
            "stop", session_id, token, payload.source if payload else None,
        )
    finally:
        request.app.state.audio.closing.discard(session_id)
        request.app.state.audio.stop_transcripts.pop(session_id, None)
        service.fusion_stopping.discard(session_id)
    if audio_result is not None and (audio_result["transcript_count"] or audio_result["incomplete"]):
        result["audio"] = audio_result
    return result


def _authorize_backstage_worker(request: Request, token: str | None) -> None:
    expected = request.app.state.settings.backstage_worker_token
    if not expected:
        raise HTTPException(503, "backstage worker is not connected")
    import hmac
    if not token or not hmac.compare_digest(expected, token):
        raise HTTPException(403, "backstage worker access denied")


@router.post("/backstage/tasks/claim")
async def claim_backstage_task(request: Request,
                               token: str | None = Header(None, alias="X-Backstage-Worker-Token")):
    _authorize_backstage_worker(request, token)
    return await request.app.state.live.run("claim_backstage_task")


@router.get("/backstage/tasks/{task_id}")
async def get_backstage_task(task_id: str, request: Request,
                             token: str | None = Header(None, alias="X-Backstage-Worker-Token")):
    _authorize_backstage_worker(request, token)
    return await request.app.state.live.run("backstage_task", task_id)


@router.post("/{session_id}/backstage/result")
async def save_backstage_result(session_id: str, payload: BackstageResultPayload, request: Request,
                               token: str | None = Header(None, alias="X-Backstage-Worker-Token")):
    _authorize_backstage_worker(request, token)
    return await request.app.state.live.run(
        "save_backstage_result", session_id, payload.model_dump(),
    )


@router.get("/{session_id}/events")
async def list_events(session_id: str, request: Request,
                      after: str = Query("", max_length=128),
                      limit: int = Query(50, ge=1, le=100),
                      token: str | None = Header(None, alias="X-Live-Session-Token")):
    return await request.app.state.live.run("events", session_id, token, after, limit)


@router.get("/{session_id}/summary")
async def session_summary(session_id: str, request: Request,
                          token: str | None = Header(None, alias="X-Live-Session-Token")):
    return await request.app.state.live.run("session_summary", session_id, token)


@router.post("/{session_id}/comments")
async def add_comment(session_id: str, payload: CommentObservation, request: Request,
                      token: str | None = Header(None, alias="X-Live-Session-Token")):
    service = request.app.state.live
    received_at_ms = service.clock()
    body = payload.model_dump()
    if body["input_mode"] == "test_fixture":
        return await service.run("add_comment", session_id, token, body, received_at_ms)
    typed = {
        "kind": "comment", "event_id": body["event_id"], "source": body["source"],
        "observed_at_ms": body["observed_at_ms"], "availability": "available",
        "coverage": body["coverage"], "reliability": body["reliability"],
        "confidence": body["confidence"],
        "content": {"text": body["text"], "text_position": body["text_position"],
                    "author_id": body["author_id"],
                    "platform_message_id": body["platform_message_id"]},
    }
    result = await service.run("add_observation", session_id, token, typed, received_at_ms)
    result = await process_fusion_observation(
        service, session_id, token, request, result, typed, urgent=False
    )
    if result.get("reason") == "quiet":
        result["reason"] = "not_a_question"
    return result


async def process_fusion_observation(service: LiveService, session_id: str,
                                     token: str | None, request: Request,
                                     result: dict, body: dict, *, urgent: bool):
    batch_ready = result.pop("_batch_ready", False)
    if result.get("reason") != "windowed" and not batch_ready:
        return result
    if not urgent:
        scheduled = service.schedule_fusion(session_id, token, request)
        if result.get("reason") == "windowed":
            result.update(reason="scheduled" if scheduled else "analysis_busy", prompt_id=None)
        return result
    delivered = await service.deliver_fusion(session_id, token, request, urgent=urgent)
    if delivered.get("reason") == "generated":
        result.update(delivered)
    elif delivered.get("reason") not in {"quiet", "suppressed_by_priority", "cooldown"}:
        result.update(delivered)
    elif body["kind"] == "comment" and delivered.get("reason") == "quiet":
        result["reason"] = "not_a_question"
    elif body["kind"] == "person_state" and delivered.get("reason") == "quiet":
        result["reason"] = "cooldown"
    return result


@router.post("/{session_id}/observations")
async def add_typed_observation(session_id: str, payload: Observation, request: Request,
                                token: str | None = Header(None, alias="X-Live-Session-Token")):
    service = request.app.state.live
    body = payload.model_dump()
    # 新字段缺省时保留旧版本的指纹输入，避免跨版本重试冲突。
    for key in ("capture_batch_id", "line_index", "capture_comment_count"):
        if key in body and body[key] is None:
            del body[key]
    result = await service.run("add_observation", session_id, token, body, service.clock())
    return await process_fusion_observation(
        service, session_id, token, request, result, body,
        urgent=body["kind"] == "platform_notice",
    )


@router.post("/{session_id}/person-observations")
async def add_person_observation(session_id: str, payload: PersonObservationPayload, request: Request,
                                 token: str | None = Header(None, alias="X-Live-Session-Token")):
    service = request.app.state.live
    body = payload.model_dump()
    outcome = await service.observe_person(session_id, token, body, service.clock())
    if not outcome.get("candidate"):
        return outcome
    candidate_result = await process_fusion_observation(
        service, session_id, token, request, outcome["result"], outcome["body"], urgent=False
    )
    return candidate_result


@router.post("/{session_id}/signals")
async def add_signal(session_id: str, payload: PersonSignal, request: Request,
                     token: str | None = Header(None, alias="X-Live-Session-Token")):
    service = request.app.state.live
    body = payload.model_dump()
    typed = {
        "kind": "person_state", "event_id": body["event_id"],
        "source": body["source"], "observed_at_ms": body["observed_at_ms"],
        "started_at_ms": body["started_at_ms"], "ended_at_ms": body["observed_at_ms"],
        "availability": "available", "coverage": "vision_pose_sample",
        "reliability": "known", "confidence": body["confidence"],
        "content": {"signal": body["signal"], "sample_count": body["sample_count"]},
    }
    result = await service.run("add_observation", session_id, token, typed, service.clock())
    return await process_fusion_observation(
        service, session_id, token, request, result, typed, urgent=False
    )


@router.get("/{session_id}/prompts")
async def poll_prompts(session_id: str, request: Request, response: Response,
                       after: str = Query("", max_length=128),
                       wait_ms: int = Query(0, ge=0, le=MAX_WAIT_MS),
                       token: str | None = Header(None, alias="X-Live-Session-Token")):
    started_ns = time.monotonic_ns()
    service = request.app.state.live
    result = await service.poll(session_id, token, after, wait_ms)
    finished_ns = time.monotonic_ns()
    result["server_time_ms"] = service.clock()
    result["server_processing_ms"] = (finished_ns - started_ns) // 1_000_000
    response.headers["Cache-Control"] = "no-store"
    return result
