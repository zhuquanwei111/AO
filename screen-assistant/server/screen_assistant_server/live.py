"""独立运营路由；显式区分测试 fixture 与 iOS 实时观察。"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .live_rules import MAX_SESSION_WAITERS, MAX_WAITERS, MAX_WAIT_MS
from .live_store import LiveStore
from .model_client import ModelTimeoutError, ModelUnavailableError

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
            if method == "stop" or (method in {"add_comment", "save_live_prompt", "add_signal"} and result.get("prompt_id")):
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


def utc_ms() -> int:
    return time.time_ns() // 1_000_000


@router.post("", status_code=201)
async def create_session(request: Request, payload: CreateSession | None = None):
    return await request.app.state.live.run("create", payload.description if payload else "")


@router.post("/{session_id}/stop")
async def stop_session(session_id: str, request: Request,
                       token: str | None = Header(None, alias="X-Live-Session-Token")):
    return await request.app.state.live.run("stop", session_id, token)


@router.get("/{session_id}/events")
async def list_events(session_id: str, request: Request,
                      after: str = Query("", max_length=128),
                      limit: int = Query(50, ge=1, le=100),
                      token: str | None = Header(None, alias="X-Live-Session-Token")):
    return await request.app.state.live.run("events", session_id, token, after, limit)


@router.post("/{session_id}/comments")
async def add_comment(session_id: str, payload: CommentObservation, request: Request,
                      token: str | None = Header(None, alias="X-Live-Session-Token")):
    service = request.app.state.live
    received_at_ms = service.clock()
    body = payload.model_dump()
    result = await service.run("add_comment", session_id, token, body, received_at_ms)
    if body["input_mode"] == "live_observation" and result.get("candidate"):
        cfg = request.app.state.settings
        if not cfg.tianyi_api_key:
            return {k: v for k, v in result.items() if k != "candidate"} | {"reason": "model_unavailable"}
        try:
            description = await service.run("description", session_id, token)
            suggestion = await request.app.state.model.suggest_live(
                body["text"], description, body["source"], result["effective_at_ms"]
            )
        except (ModelTimeoutError, ModelUnavailableError):
            return {k: v for k, v in result.items() if k != "candidate"} | {"reason": "model_unavailable"}
        prompt = await service.run(
            "save_live_prompt", session_id, token, body, result["effective_at_ms"], suggestion
        )
        return {k: v for k, v in result.items() if k != "candidate"} | prompt
    return result


@router.post("/{session_id}/signals")
async def add_signal(session_id: str, payload: PersonSignal, request: Request,
                     token: str | None = Header(None, alias="X-Live-Session-Token")):
    service = request.app.state.live
    return await service.run("add_signal", session_id, token, payload.model_dump(), service.clock())


@router.get("/{session_id}/prompts")
async def poll_prompts(session_id: str, request: Request,
                       after: str = Query("", max_length=128),
                       wait_ms: int = Query(0, ge=0, le=MAX_WAIT_MS),
                       token: str | None = Header(None, alias="X-Live-Session-Token")):
    return await request.app.state.live.poll(session_id, token, after, wait_ms)
