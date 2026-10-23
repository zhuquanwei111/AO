"""独立运营 HTTP 路由；M2 只处理显式 fixture，不接真实采集或模型。"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from .live_store import LiveStore

router = APIRouter(prefix="/v1/live/sessions", tags=["live-test-fixture"])


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field(default="", max_length=500)


class LiveService:
    def __init__(self, store: LiveStore):
        self.store = store
        self.lock = asyncio.Lock()

    async def run(self, method: str, *args):
        async with self.lock:
            # 请求取消时等待正在进行的数据库操作结束，再释放锁/关闭连接。
            task = asyncio.create_task(asyncio.to_thread(getattr(self.store, method), *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise


def utc_ms() -> int:
    return time.time_ns() // 1_000_000


@router.post("", status_code=201)
async def create_session(payload: CreateSession, request: Request):
    return await request.app.state.live.run("create", payload.description)


@router.post("/{session_id}/stop")
async def stop_session(session_id: str, request: Request,
                       token: str | None = Header(None, alias="X-Live-Session-Token")):
    return await request.app.state.live.run("stop", session_id, token)


@router.get("/{session_id}/events")
async def list_events(session_id: str, request: Request,
                      token: str | None = Header(None, alias="X-Live-Session-Token")):
    return await request.app.state.live.run("events", session_id, token)
