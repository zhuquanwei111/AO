"""环境变量配置。

所有配置只从环境变量读取；不打印、不落盘任何密钥。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    """服务运行配置。"""

    redis_url: str
    tianyi_api_key: str
    tianyi_base_url: str
    tianyi_model: str
    dedup_ttl_seconds: int
    max_sentence_chars: int
    model_timeout_seconds: float
    model_connect_timeout_seconds: float
    live_db_path: str = "data/live.sqlite3"
    audio_enabled: bool = False
    # 保留旧配置形状；生产音频已改用funasr_model_path，不再连接WebSocket。
    funasr_url: str = ""
    funasr_mode: str = "online"
    silero_model_path: str = ""
    funasr_model_path: str = ""
    backstage_worker_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
            tianyi_api_key=os.getenv("TIANYI_API_KEY", ""),
            tianyi_base_url=os.getenv("TIANYI_BASE_URL", "https://ai.ctaigw.cn/v1"),
            tianyi_model=os.getenv("TIANYI_MODEL", "glm-5.3-flash"),
            # 执行计划固定默认 24 小时 TTL
            dedup_ttl_seconds=_env_int("DEDUP_TTL_SECONDS", 86400),
            max_sentence_chars=_env_int("MAX_SENTENCE_CHARS", 2000),
            model_timeout_seconds=_env_float("MODEL_TIMEOUT_SECONDS", 30.0),
            model_connect_timeout_seconds=_env_float("MODEL_CONNECT_TIMEOUT_SECONDS", 5.0),
            live_db_path=os.getenv("LIVE_DB_PATH", "data/live.sqlite3"),
            audio_enabled=os.getenv("AUDIO_ENABLED", "0") == "1",
            funasr_url=os.getenv("FUNASR_WS_URL", ""),
            funasr_mode=os.getenv("FUNASR_MODE", "online"),
            silero_model_path=os.getenv("SILERO_ONNX_PATH", ""),
            funasr_model_path=os.getenv("FUNASR_MODEL_PATH", ""),
            backstage_worker_token=os.getenv("BACKSTAGE_WORKER_TOKEN", ""),
        )
