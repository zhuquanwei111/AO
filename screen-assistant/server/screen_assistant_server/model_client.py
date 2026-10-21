"""天翼 OpenAI 兼容单轮短回答客户端。

端点：{TIANYI_BASE_URL}/chat/completions，Bearer 认证。
仅单条系统指令 + 单条用户句子，无历史、无多轮、无 agent。
日志与异常信息不包含句子内容、密钥或完整响应体。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

SYSTEM_PROMPT = (
    "你是屏幕识字助手。用户发来一句从屏幕上识别出的中文句子，"
    "请用中文简短直接回答；信息不足或不确定时明确说不知道，"
    "不要编造事实，不要输出多余解释。"
)


class ModelUnavailableError(Exception):
    """模型端点不可用（网络/认证/非 200/空回复）。调用方返回 503。"""

    def __init__(
        self,
        error_code: str,
        *,
        upstream_status: int | None = None,
        finish_reason: str | None = None,
        empty_content: bool | None = None,
        completion_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        output_limit: int | None = None,
        reasoning_field_present: bool | None = None,
        reasoning_field_nonempty: bool | None = None,
    ) -> None:
        self.error_code = error_code
        self.upstream_status = upstream_status
        self.finish_reason = finish_reason
        self.empty_content = empty_content
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens
        self.output_limit = output_limit
        self.reasoning_field_present = reasoning_field_present
        self.reasoning_field_nonempty = reasoning_field_nonempty


class ModelTimeoutError(Exception):
    """模型请求超时。调用方返回 504。"""

    def __init__(self, error_code: str = "upstream_timeout") -> None:
        self.error_code = error_code


_PROVIDER_ERROR_CODES = {
    "invalid_api_key": "provider_api_key_invalid",
    "insufficient_quota": "provider_quota_exhausted",
    "rate_limit_exceeded": "provider_rate_limited",
    "10011021": "provider_api_key_missing",
    "10011022": "provider_api_key_invalid",
    "10011023": "provider_api_key_forbidden",
    "10011041": "provider_no_balance",
    "10011001": "provider_rate_limited",
}
_FINISH_REASONS = {"stop", "length", "content_filter", "tool_calls", "function_call"}
MAX_OUTPUT_TOKENS = 512
REASONING_EFFORT = "low"


@dataclass(frozen=True)
class ModelAnswer:
    reply: str
    finish_reason: str
    completion_tokens: int | None
    reasoning_tokens: int | None
    reasoning_field_present: bool
    reasoning_field_nonempty: bool


def _token_count(value: object) -> int | None:
    """Return a non-negative integer count from provider usage metadata only."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _provider_error_code(resp: httpx.Response) -> str:
    """Return only allowlisted provider codes; never log arbitrary error text."""
    try:
        data = resp.json()
        error = data.get("error", {}) if isinstance(data, dict) else {}
        raw_code = error.get("code") if isinstance(error, dict) else None
        if raw_code is None and isinstance(data, dict):
            raw_code = data.get("code")
        code = str(raw_code) if raw_code is not None else ""
    except (ValueError, TypeError):
        code = ""
    return _PROVIDER_ERROR_CODES.get(code, "provider_http_error")


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

    async def ask(self, sentence: str) -> ModelAnswer:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": sentence},
            ],
            "temperature": 0.2,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "reasoning_effort": REASONING_EFFORT,
            "stream": False,
        }
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException:
            raise ModelTimeoutError() from None
        except httpx.HTTPError:
            raise ModelUnavailableError("upstream_transport_error") from None

        if resp.status_code != 200:
            raise ModelUnavailableError(
                _provider_error_code(resp), upstream_status=resp.status_code
            )

        try:
            data = resp.json()
            choice = data["choices"][0]
            message = choice["message"]
            content = message["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelUnavailableError("model_response_malformed") from None

        raw_finish_reason = choice.get("finish_reason")
        finish_reason = (
            raw_finish_reason
            if isinstance(raw_finish_reason, str) and raw_finish_reason in _FINISH_REASONS
            else "other"
        )
        reply = content.strip() if isinstance(content, str) else ""
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        token_details = usage.get("completion_tokens_details")
        token_details = token_details if isinstance(token_details, dict) else {}
        reasoning_fields = [
            message.get(name)
            for name in ("reasoning_content", "reasoning", "reasoning_details")
            if name in message
        ]
        completion_tokens = _token_count(usage.get("completion_tokens"))
        reasoning_tokens = _token_count(token_details.get("reasoning_tokens"))
        reasoning_field_present = bool(reasoning_fields)
        reasoning_field_nonempty = any(bool(value) for value in reasoning_fields)
        if finish_reason == "length":
            raise ModelUnavailableError(
                "model_output_truncated",
                finish_reason=finish_reason,
                empty_content=not bool(reply),
                completion_tokens=completion_tokens,
                reasoning_tokens=reasoning_tokens,
                output_limit=MAX_OUTPUT_TOKENS,
                reasoning_field_present=reasoning_field_present,
                reasoning_field_nonempty=reasoning_field_nonempty,
            )
        if not reply:
            error_code = (
                "provider_content_filtered"
                if finish_reason == "content_filter"
                else "model_empty_content"
            )
            raise ModelUnavailableError(
                error_code,
                finish_reason=finish_reason,
                empty_content=True,
            )
        return ModelAnswer(
            reply=reply,
            finish_reason=finish_reason,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            reasoning_field_present=reasoning_field_present,
            reasoning_field_nonempty=reasoning_field_nonempty,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
