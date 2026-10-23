"""独立运营路由；显式区分测试 fixture 与 iOS 实时观察。"""
from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .live_rules import FUSION_SETTLE_MS, MAX_SESSION_WAITERS, MAX_WAITERS, MAX_WAIT_MS
from .live_store import LiveStore
from .model_client import ModelTimeoutError, ModelUnavailableError
from .live_fusion import Observation

router = APIRouter(prefix="/v1/live/sessions", tags=["live-test-fixture"])


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field(default="", max_length=500)


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
    source: str = Field(min_length=1, max_length=64)
    coverage: Literal["structured_sample", "ocr_visible_sample", "unknown"]
    reliability: Literal["known", "unknown"] = "unknown"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    text_position: TextPosition | None = None
    text: str = Field(min_length=1, max_length=500)
    author_id: str | None = Field(default=None, min_length=1, max_length=128)
    platform_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    observed_at_ms: int | None = Field(default=None, ge=0, le=2**63-1, strict=True)


class PersonSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event_id: str = Field(min_length=1, max_length=128)
    source: Literal["ios_replaykit_vision"]
    signal: Literal["head_down"]
    confidence: float = Field(ge=0.5, le=1.0)
    sample_count: int = Field(ge=5, le=100)
    started_at_ms: int = Field(ge=0, le=2**63-1, strict=True)
    observed_at_ms: int = Field(ge=0, le=2**63-1, strict=True)


class LiveService:
    def __init__(self, path: str, clock):
        self.path, self.clock = path, clock
        self.store: LiveStore | None = None
        self.lock = asyncio.Lock()
        self.fusion_lock = asyncio.Lock()
        self.waiters: dict[str, set[asyncio.Future]] = {}

    def dispatch(self, method: str, *args):
        # 延迟初始化，使新存储不可写时不影响旧问答/健康检查的生命周期。
        if method == "close":
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
            if method == "stop" or (method in {
                "add_comment", "save_live_prompt", "add_signal", "save_fusion_candidate"
            } and result.get("prompt_id")):
                for future in self.waiters.get(args[0], ()):
                    if not future.done():
                        future.set_result(None)
            return result

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
                             *, urgent: bool = False):
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
                    source = ",".join(dict.fromkeys(
                        row["source"] for row in candidate["observations"]
                    ))
                    observed_at = max(row["effective_at_ms"] for row in candidate["observations"])
                    suggestion = await request.app.state.model.suggest_live(
                        candidate["model_text"], candidate["description"], source,
                        observed_at, context=candidate["model_context"],
                    )
                except (ModelTimeoutError, ModelUnavailableError):
                    return {"reason": "model_unavailable", "prompt_id": None}
                if not valid_operation_suggestion(suggestion):
                    return {"reason": "model_output_invalid", "prompt_id": None}
            elif not suggestion or len(suggestion) > 100 or "\n" in suggestion or "\r" in suggestion:
                return {"reason": "candidate_invalid", "prompt_id": None}
            return await self.run("save_fusion_candidate", sid, token, candidate, suggestion)

        if urgent:
            return await deliver()
        async with self.fusion_lock:
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
    return await request.app.state.live.run("create", payload.description if payload else "")


@router.post("/{session_id}/stop")
async def stop_session(session_id: str, request: Request,
                       token: str | None = Header(None, alias="X-Live-Session-Token")):
    # 幂等stop保留；有音频时先处理已收到的尾窗，再关闭M4收件。
    service = request.app.state.live
    async with service.lock:
        row = await service.call("authorize", session_id, token)
        stopped = row["stopped_at_ms"] is not None
    audio_result = None
    if not stopped:
        audio_result = await request.app.state.audio.finish_session(session_id, token)
    request.app.state.audio.closing.add(session_id)
    try:
        if audio_result and audio_result["transcript_count"]:
            await service.deliver_fusion(session_id, token, request)
        result = await service.run("stop", session_id, token)
    finally:
        request.app.state.audio.closing.discard(session_id)
    if audio_result is not None and (audio_result["transcript_count"] or audio_result["incomplete"]):
        result["audio"] = audio_result
    return result


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
    if result.get("reason") != "windowed":
        return result
    if not urgent and FUSION_SETTLE_MS:
        await asyncio.sleep(FUSION_SETTLE_MS / 1000)
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
    result = await service.run("add_observation", session_id, token, body, service.clock())
    return await process_fusion_observation(
        service, session_id, token, request, result, body,
        urgent=body["kind"] == "platform_notice",
    )


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
async def poll_prompts(session_id: str, request: Request,
                       after: str = Query("", max_length=128),
                       wait_ms: int = Query(0, ge=0, le=MAX_WAIT_MS),
                       token: str | None = Header(None, alias="X-Live-Session-Token")):
    return await request.app.state.live.poll(session_id, token, after, wait_ms)
