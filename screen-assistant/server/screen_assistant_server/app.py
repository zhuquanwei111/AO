"""FastAPI 应用：固定契约 POST /v1/answer 与 GET /healthz。

状态码契约（执行计划固定）：
- 200 {"status":"ok","reply":非空字符串}
- 204 重复句子（无响应体，客户端保留当前展示）
- 422 空白/无效/超限/额外字段输入
- 503 Redis 不可用 / 模型凭据缺失 / 模型失败
- 504 模型请求超时

日志只记录事件与长度，不记录句子内容、回复内容或密钥。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import Settings
from .dedup import AnswerDeduper, DedupStoreUnavailableError
from .model_client import ModelTimeoutError, ModelUnavailableError, TianyiChatClient

logger = logging.getLogger("screen_assistant.app")

KEY_PREFIX = "sa:answer:v1"


class AnswerRequest(BaseModel):
    """请求对象只含 sentence；多余字段按无效输入拒绝（422）。"""

    model_config = ConfigDict(extra="forbid")

    sentence: str = Field(min_length=1, description="OCR 得到的完整中文句子")


def _json_error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis = Redis.from_url(cfg.redis_url, decode_responses=True)
        app.state.redis = redis
        app.state.dedup = AnswerDeduper(
            redis, key_prefix=KEY_PREFIX, ttl_seconds=cfg.dedup_ttl_seconds
        )
        app.state.settings = cfg
        # 模型客户端依赖真实凭据；无凭据时保持实例可创建，
        # /v1/answer 按契约返回 503，healthz 明确标出。
        app.state.model = TianyiChatClient(
            base_url=cfg.tianyi_base_url,
            api_key=cfg.tianyi_api_key,
            model=cfg.tianyi_model,
            timeout_seconds=cfg.model_timeout_seconds,
            connect_timeout_seconds=cfg.model_connect_timeout_seconds,
        )
        logger.info(
            "service started model_key_configured=%s model=%s ttl=%d",
            bool(cfg.tianyi_api_key),
            cfg.tianyi_model,
            cfg.dedup_ttl_seconds,
        )
        try:
            yield
        finally:
            await app.state.model.aclose()
            await redis.aclose()

    app = FastAPI(title="screen-assistant server", version="1.0.0", lifespan=lifespan)

    @app.post("/v1/answer")
    async def answer(payload: AnswerRequest, request: Request):
        cfg: Settings = request.app.state.settings
        normalized = payload.sentence.strip()
        if not normalized:
            raise HTTPException(status_code=422, detail="sentence is blank after normalization")
        if len(normalized) > cfg.max_sentence_chars:
            raise HTTPException(status_code=422, detail="sentence exceeds maximum length")
        if not cfg.tianyi_api_key:
            raise HTTPException(status_code=503, detail="model credential not configured")

        dedup: AnswerDeduper = request.app.state.dedup
        key = dedup.key_for(normalized)
        token = await dedup.try_acquire(key)  # Redis 失败在此抛出，经 handler 转 503
        if token is None:
            logger.info("answer duplicate chars=%d", len(normalized))
            return Response(status_code=204)

        try:
            reply = await request.app.state.model.ask(normalized)
        except ModelTimeoutError:
            await dedup.release(key, token)
            logger.info("answer timeout chars=%d", len(normalized))
            raise HTTPException(status_code=504, detail="model request timed out") from None
        except ModelUnavailableError:
            await dedup.release(key, token)
            logger.info("answer model failure chars=%d", len(normalized))
            raise HTTPException(status_code=503, detail="model unavailable") from None

        logger.info("answer ok chars=%d reply_chars=%d", len(normalized), len(reply))
        return {"status": "ok", "reply": reply}

    @app.get("/healthz")
    async def healthz(request: Request):
        cfg: Settings = request.app.state.settings
        redis_ok = False
        redis_error: str | None = None
        redis: Redis = request.app.state.redis
        try:
            redis_ok = await redis.ping()
        except RedisError as exc:
            redis_error = type(exc).__name__
        model_key_ok = bool(cfg.tianyi_api_key)
        return {
            "status": "ok" if (redis_ok and model_key_ok) else "degraded",
            "redis": "ok" if redis_ok else f"unavailable:{redis_error or 'ping failed'}",
            "model_key_configured": model_key_ok,
            "model": cfg.tianyi_model,
            "dedup_ttl_seconds": cfg.dedup_ttl_seconds,
        }

    @app.exception_handler(DedupStoreUnavailableError)
    async def dedup_unavailable_handler(request: Request, exc: DedupStoreUnavailableError):
        logger.warning("dedup store unavailable: %s", exc)
        return _json_error(503, "dedup store unavailable")

    return app


app = create_app()
