"""基于真实 Redis 的原子去重。

判重：SET key token NX EX ttl —— 单命令原子完成，O(1)，
  https://redis.io/docs/latest/commands/set/
释放：官方 Patterns 章节的 compare-and-delete Lua 脚本，
  只有当前 key 的值仍等于本请求的随机 token 才 DEL，
  避免误删其他请求写入的状态。
  https://redis.io/docs/latest/commands/set/ （Patterns: release with token check）

key 由规范化句子的 SHA-256 摘要构成：完整覆盖规范化句子本身
（而非截断前缀），同时不把原文写入 Redis key，并限定 key 长度。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

# 值匹配才删除；EVAL 时 KEYS[1]=key, ARGV[1]=token
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class DedupStoreUnavailableError(Exception):
    """Redis 连接/命令失败。调用方必须返回 503，不得降级为进程内去重。"""


class AnswerDeduper:
    """句子级 24 小时去重占位器。"""

    def __init__(self, redis: Redis, key_prefix: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
        self._release_script = redis.register_script(_RELEASE_SCRIPT)

    def key_for(self, normalized_sentence: str) -> str:
        digest = hashlib.sha256(normalized_sentence.encode("utf-8")).hexdigest()
        return f"{self._key_prefix}:{digest}"

    async def try_acquire(self, key: str) -> str | None:
        """尝试占位。成功返回本次随机 token；句子重复返回 None。"""
        token = uuid.uuid4().hex
        try:
            acquired = await self._redis.set(key, token, nx=True, ex=self._ttl_seconds)
        except RedisError as exc:
            raise DedupStoreUnavailableError(str(type(exc).__name__)) from exc
        return token if acquired else None

    async def release(self, key: str, token: str) -> bool:
        """尽力释放本次占位（模型失败时允许后续重试该句）。"""
        release: Callable[..., Awaitable[int]] = self._release_script
        try:
            deleted = await release(keys=[key], args=[token])
        except RedisError:
            # Redis 此刻不可用时无法释放；key 持有随机 token 且带 TTL，
            # 不会与其他请求状态混淆，到期的边界行为在 README 说明。
            return False
        return bool(deleted)
