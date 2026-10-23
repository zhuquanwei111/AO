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

import asyncio
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import Settings
from .live import LiveService, router as live_router, utc_ms
from .live_store import LiveStore
from .dedup import AnswerDeduper, DedupStoreUnavailableError
from .model_client import (
    MAX_OUTPUT_TOKENS,
    REASONING_EFFORT,
    ModelTimeoutError,
    ModelUnavailableError,
    TianyiChatClient,
)

logger = logging.getLogger("screen_assistant.app")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _diagnostic_handler = logging.StreamHandler(sys.stdout)
    _diagnostic_handler.setLevel(logging.INFO)
    _diagnostic_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_diagnostic_handler)
logger.propagate = False

KEY_PREFIX = "sa:answer:v1"


class AnswerRequest(BaseModel):
    """请求对象只含 sentence；多余字段按无效输入拒绝（422）。"""

    model_config = ConfigDict(extra="forbid")

    sentence: str = Field(min_length=1, description="OCR 得到的完整中文句子")


def _json_error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _diagnostic_value(value: object) -> object:
    return "unknown" if value is None else value


def create_app(settings: Settings | None = None, *, live_clock=None) -> FastAPI:
    cfg = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = await asyncio.to_thread(LiveStore, cfg.live_db_path, live_clock or utc_ms)
        app.state.live = LiveService(store)
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
            await app.state.live.run("close")
            await app.state.model.aclose()
            await redis.aclose()

    app = FastAPI(title="screen-assistant server", version="1.0.0", lifespan=lifespan)
    app.include_router(live_router)

    @app.middleware("http")
    async def answer_request_id(request: Request, call_next):
        if request.url.path != "/v1/answer":
            return await call_next(request)
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        request_started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "answer request_id=%s stage=http_complete status=%d request_elapsed_ms=%d",
            request_id,
            response.status_code,
            round((time.perf_counter() - request_started) * 1000),
        )
        return response

    @app.post("/v1/answer")
    async def answer(payload: AnswerRequest, request: Request):
        cfg: Settings = request.app.state.settings
        normalized = payload.sentence.strip()
        if not normalized:
            raise HTTPException(status_code=422, detail="sentence is blank after normalization")
        if len(normalized) > cfg.max_sentence_chars:
            raise HTTPException(status_code=422, detail="sentence exceeds maximum length")
        if not cfg.tianyi_api_key:
            logger.warning(
                "answer failure request_id=%s stage=configuration error_code=model_credential_missing requested_reasoning_effort=%s model_elapsed_ms=unknown output_limit=%s completion_tokens=unknown reasoning_tokens=unknown finish_reason=unknown",
                request.state.request_id,
                REASONING_EFFORT,
                MAX_OUTPUT_TOKENS,
            )
            raise HTTPException(status_code=503, detail="model credential not configured")

        dedup: AnswerDeduper = request.app.state.dedup
        key = dedup.key_for(normalized)
        token = await dedup.try_acquire(key)  # Redis 失败在此抛出，经 handler 转 503
        if token is None:
            logger.info(
                "answer request_id=%s stage=dedup outcome=duplicate requested_reasoning_effort=%s model_elapsed_ms=unknown output_limit=%s completion_tokens=unknown reasoning_tokens=unknown finish_reason=unknown",
                request.state.request_id,
                REASONING_EFFORT,
                MAX_OUTPUT_TOKENS,
            )
            return Response(status_code=204)

        model_started = time.perf_counter()
        try:
            result = await request.app.state.model.ask(normalized)
            model_elapsed_ms = round((time.perf_counter() - model_started) * 1000)
        except ModelTimeoutError as exc:
            await dedup.release(key, token)
            logger.warning(
                "answer failure request_id=%s stage=provider error_code=%s requested_reasoning_effort=%s model_elapsed_ms=%d upstream_status=unknown finish_reason=unknown output_limit=%s completion_tokens=unknown reasoning_tokens=unknown",
                request.state.request_id,
                exc.error_code,
                REASONING_EFFORT,
                round((time.perf_counter() - model_started) * 1000),
                MAX_OUTPUT_TOKENS,
            )
            raise HTTPException(status_code=504, detail="model request timed out") from None
        except ModelUnavailableError as exc:
            await dedup.release(key, token)
            logger.warning(
                "answer failure request_id=%s stage=provider error_code=%s requested_reasoning_effort=%s model_elapsed_ms=%d upstream_status=%s finish_reason=%s empty_content=%s completion_tokens=%s reasoning_tokens=%s reasoning_field_present=%s reasoning_field_nonempty=%s output_limit=%s",
                request.state.request_id,
                exc.error_code,
                REASONING_EFFORT,
                round((time.perf_counter() - model_started) * 1000),
                _diagnostic_value(exc.upstream_status),
                _diagnostic_value(exc.finish_reason),
                _diagnostic_value(exc.empty_content),
                _diagnostic_value(exc.completion_tokens),
                _diagnostic_value(exc.reasoning_tokens),
                _diagnostic_value(exc.reasoning_field_present),
                _diagnostic_value(exc.reasoning_field_nonempty),
                _diagnostic_value(exc.output_limit),
            )
            raise HTTPException(status_code=503, detail="model unavailable") from None

        logger.info(
            "answer request_id=%s stage=complete outcome=success requested_reasoning_effort=%s model_elapsed_ms=%d output_limit=%s finish_reason=%s completion_tokens=%s reasoning_tokens=%s reasoning_field_present=%s reasoning_field_nonempty=%s",
            request.state.request_id,
            REASONING_EFFORT,
            model_elapsed_ms,
            MAX_OUTPUT_TOKENS,
            result.finish_reason,
            _diagnostic_value(result.completion_tokens),
            _diagnostic_value(result.reasoning_tokens),
            result.reasoning_field_present,
            result.reasoning_field_nonempty,
        )
        return {"status": "ok", "reply": result.reply}

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
        logger.warning(
            "answer failure request_id=%s stage=dedup error_code=dedup_store_unavailable requested_reasoning_effort=%s model_elapsed_ms=unknown output_limit=%s completion_tokens=unknown reasoning_tokens=unknown finish_reason=unknown",
            getattr(request.state, "request_id", "unknown"),
            REASONING_EFFORT,
            MAX_OUTPUT_TOKENS,
        )
        return _json_error(503, "dedup store unavailable")

    return app


app = create_app()
