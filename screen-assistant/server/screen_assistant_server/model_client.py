"""天翼 OpenAI 兼容单轮短回答客户端。

端点：{TIANYI_BASE_URL}/chat/completions，Bearer 认证。
仅单条系统指令 + 单条用户句子，无历史、无多轮、无 agent。
日志与异常信息不包含句子内容、密钥或完整响应体。
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("screen_assistant.model")

SYSTEM_PROMPT = (
    "你是屏幕识字助手。用户发来一句从屏幕上识别出的中文句子，"
    "请用中文简短直接回答；信息不足或不确定时明确说不知道，"
    "不要编造事实，不要输出多余解释。"
)


class ModelUnavailableError(Exception):
    """模型端点不可用（网络/认证/非 200/空回复）。调用方返回 503。"""


class ModelTimeoutError(Exception):
    """模型请求超时。调用方返回 504。"""


class TianyiChatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        connect_timeout_seconds: float,
    ) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds),
        )

    async def ask(self, sentence: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": sentence},
            ],
            "temperature": 0.2,
            "max_tokens": 256,
            "stream": False,
        }
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError("model request timed out") from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(f"model request failed: {type(exc).__name__}") from exc

        if resp.status_code != 200:
            logger.warning("model http status=%d", resp.status_code)
            raise ModelUnavailableError(f"model http status={resp.status_code}")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailableError("model response malformed") from exc

        reply = content.strip() if isinstance(content, str) else ""
        if not reply:
            raise ModelUnavailableError("model returned empty reply")
        return reply

    async def aclose(self) -> None:
        await self._client.aclose()
