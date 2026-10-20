"""契约测试：本机真实 Redis + 假模型客户端（不消耗模型 token）。

真实模型接线另行单独验证（见 README「验证证据」）。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Iterator

import httpx
import pytest
import redis as sync_redis
from fastapi.testclient import TestClient

from screen_assistant_server.app import KEY_PREFIX, create_app
from screen_assistant_server.config import Settings
from screen_assistant_server.model_client import ModelTimeoutError, ModelUnavailableError

TEST_REDIS_URL = "redis://127.0.0.1:6390/9"


def make_settings(**overrides) -> Settings:
    base = dict(
        redis_url=TEST_REDIS_URL,
        tianyi_api_key="test-key-not-real",
        tianyi_base_url="http://model.unused",
        tianyi_model="glm-5.3-flash",
        dedup_ttl_seconds=86400,
        max_sentence_chars=50,
        model_timeout_seconds=30.0,
        model_connect_timeout_seconds=5.0,
    )
    base.update(overrides)
    return Settings(**base)


class FakeModel:
    """可编程假模型：ok / fail / timeout。"""

    def __init__(self, behavior: str = "ok") -> None:
        self.behavior = behavior
        self.calls = 0

    async def ask(self, sentence: str) -> str:
        self.calls += 1
        if self.behavior == "fail":
            raise ModelUnavailableError("fake unavailable")
        if self.behavior == "timeout":
            raise ModelTimeoutError("fake timeout")
        return "好的"

    async def aclose(self) -> None:
        return None


def flush_test_db() -> None:
    r = sync_redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    r.flushdb()
    r.close()


@contextmanager
def client_for(settings: Settings, model: FakeModel) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.model = model
        yield client


def key_of(normalized: str) -> str:
    import hashlib

    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{KEY_PREFIX}:{digest}"


@pytest.fixture(autouse=True)
def _clean_redis():
    flush_test_db()
    yield
    flush_test_db()


def test_new_sentence_200_then_duplicate_204():
    model = FakeModel("ok")
    with client_for(make_settings(), model) as client:
        resp = client.post("/v1/answer", json={"sentence": "  今天天气怎么样？  "})
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"status": "ok", "reply": "好的"}

        # 相同句子（不同首尾空白，规范化后一致）→ 204 无响应体，且不调用模型
        resp2 = client.post("/v1/answer", json={"sentence": "今天天气怎么样？"})
        assert resp2.status_code == 204
        assert resp2.content == b""
        assert model.calls == 1


def test_invalid_inputs_422():
    model = FakeModel("ok")
    with client_for(make_settings(), model) as client:
        cases = [
            {"sentence": "   "},                      # 规范化后为空
            {"sentence": ""},                          # 空串
            {},                                        # 缺字段
            {"sentence": 123},                         # 非字符串
            {"sentence": "你好", "extra": 1},          # 额外字段
            {"sentence": "好" * 51},                   # 超过 max_sentence_chars=50
        ]
        for payload in cases:
            resp = client.post("/v1/answer", json=payload)
            assert resp.status_code == 422, f"payload={payload!r} status={resp.status_code}"
        assert model.calls == 0


def test_model_failure_releases_placeholder_and_allows_retry():
    failing = FakeModel("fail")
    ok_model = FakeModel("ok")
    settings = make_settings()
    sentence = "这是什么意思？"
    with client_for(settings, failing) as client:
        resp = client.post("/v1/answer", json={"sentence": sentence})
        assert resp.status_code == 503

    r = sync_redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    # 占位应已释放：模型失败后允许后续重试该句
    assert r.exists(key_of(sentence)) == 0
    r.close()

    # 换真实可用模型后同句可重试成功
    with client_for(settings, ok_model) as client:
        resp = client.post("/v1/answer", json={"sentence": sentence})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert ok_model.calls == 1

    # 成功后 key 在 TTL 内合法占用（重复句 204）
    r = sync_redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    assert r.exists(key_of(sentence)) == 1
    r.close()


def test_model_timeout_504_and_release():
    timeout_model = FakeModel("timeout")
    ok_model = FakeModel("ok")
    settings = make_settings()
    with client_for(settings, timeout_model) as client:
        resp = client.post("/v1/answer", json={"sentence": "帮我解释一下。"})
        assert resp.status_code == 504
    with client_for(settings, ok_model) as client:
        resp = client.post("/v1/answer", json={"sentence": "帮我解释一下。"})
        assert resp.status_code == 200
        assert ok_model.calls == 1


def test_missing_api_key_503():
    model = FakeModel("ok")
    with client_for(make_settings(tianyi_api_key=""), model) as client:
        resp = client.post("/v1/answer", json={"sentence": "你好"})
        assert resp.status_code == 503
        assert model.calls == 0


def test_redis_down_503_no_inprocess_fallback():
    model = FakeModel("ok")
    settings = make_settings(redis_url="redis://127.0.0.1:6399/0")  # 无实例监听
    with client_for(settings, model) as client:
        for _ in range(2):  # 两次都 503：不得悄悄降级为进程内去重
            resp = client.post("/v1/answer", json={"sentence": "你好"})
            assert resp.status_code == 503
        assert model.calls == 0


def test_healthz():
    model = FakeModel("ok")
    with client_for(make_settings(), model) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["redis"] == "ok"
        assert body["model_key_configured"] is True
        assert "key" not in resp.text.lower().replace("model_key_configured", "")

    with client_for(make_settings(tianyi_api_key=""), model) as client:
        body = client.get("/healthz").json()
        assert body["status"] == "degraded"
        assert body["model_key_configured"] is False


def test_concurrent_duplicates_single_winner():
    """并发同句：恰好一个 200，其余 204（真实 Redis SET NX 原子性）。"""

    async def run() -> list[int]:
        app = create_app(make_settings())
        async with app.router.lifespan_context(app):
            app.state.model = FakeModel("ok")
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                responses = await asyncio.gather(
                    *[c.post("/v1/answer", json={"sentence": "并发去重测试句"}) for _ in range(10)]
                )
                return [r.status_code for r in responses]

    statuses = asyncio.run(run())
    assert statuses.count(200) == 1
    assert statuses.count(204) == 9
