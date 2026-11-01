"""隔离验证现有适配器及产品链的契约；替身输出不证明话术质量。"""
import asyncio
import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings
from screen_assistant_server.model_client import (
    COMMENT_REPLY_SYSTEM_PROMPT, LIVE_SYSTEM_PROMPT, TianyiChatClient,
)


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo

    def checked_address(address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise PermissionError("测试禁止连接外部网络")

    def guarded_connect(sock, address):
        checked_address(address)
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        checked_address(address)
        return connect_ex(sock, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise PermissionError("测试禁止解析外部主机")
        return getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    with socket.socket() as sock, pytest.raises(PermissionError):
        sock.connect(("203.0.113.1", 443))


def make_client(handler) -> TianyiChatClient:
    model = object.__new__(TianyiChatClient)
    model._model = "fake"
    model._client = httpx.AsyncClient(
        base_url="http://model.invalid",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer synthetic"},
        timeout=httpx.Timeout(2, connect=1),
        trust_env=False,
    )
    return model


@pytest.mark.parametrize("text", ["这首歌很动听", "今天特别累", "你先把自己逗笑了吧", "我喜欢听民谣"])
def test_comment_adapter_is_used_by_real_reconstruction_and_prompt_endpoint(tmp_path, text):
    now = 1_790_000_000_000
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="synthetic",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake", dedup_ttl_seconds=60,
                   max_sentence_chars=2000, model_timeout_seconds=2, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "coach.sqlite3"))
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"respond":true,"reply":"谢谢分享。"}'}}]})

    app = create_app(cfg, live_clock=lambda: now)
    with TestClient(app) as client:
        client.portal.call(app.state.model.aclose)
        app.state.model = make_client(handler)
        session = client.post("/v1/live/sessions", json={}).json()
        sid = session["session_id"]
        headers = {"X-Live-Session-Token": session["session_token"]}
        url = f"/v1/live/sessions/{sid}"
        row = {"kind": "comment", "event_id": "synthetic", "source": "ios_replaykit_ocr",
               "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
               "reliability": "known", "capture_batch_id": "synthetic-batch", "line_index": 0,
               "capture_comment_count": 1, "content": {"text": "小林：" + text, "text_position":
                   {"x": 0.1, "y": 0.5, "width": 0.4, "height": 0.04}}}
        assert client.post(url + "/observations", headers=headers, json=row).json()["reason"] == "scheduled"
        async def wait_idle():
            async with asyncio.timeout(3):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(0.005)
        client.portal.call(wait_idle)
        result = client.get(url + "/prompts", headers=headers).json()
        assert len(calls) == 1 and calls[0]["max_tokens"] == 512
        assert calls[0]["messages"][0]["content"] == COMMENT_REPLY_SYSTEM_PROMPT
        assert json.loads(calls[0]["messages"][1]["content"])["text"] == text
        prompt = result["prompts"][0]
        assert prompt["prompt_type"] == "comment_reply" and prompt["reply_to"]["text"] == text
        assert prompt["display_text"] == "谢谢分享。"
        assert prompt["expires_at_ms"] - prompt["generated_at_ms"] == 15_000


def test_existing_operation_model_path_keeps_its_http_contract(tmp_path):
    now = 1_790_000_000_000
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="synthetic",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake", dedup_ttl_seconds=60,
                   max_sentence_chars=2000, model_timeout_seconds=2, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "notice.sqlite3"))
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": "可以先回应这个问题，再邀请大家补充。"}}]})

    app = create_app(cfg, live_clock=lambda: now)
    with TestClient(app) as client:
        client.portal.call(app.state.model.aclose)
        app.state.model = make_client(handler)
        session = client.post("/v1/live/sessions", json={}).json()
        sid = session["session_id"]
        headers = {"X-Live-Session-Token": session["session_token"]}
        url = f"/v1/live/sessions/{sid}"
        row = {"event_id": "synthetic", "input_mode": "live_observation", "source": "ios_replaykit_ocr",
               "coverage": "ocr_visible_sample", "reliability": "known", "text": "主播你喜欢什么音乐？"}
        assert client.post(url + "/comments", headers=headers, json=row).json()["reason"] == "scheduled"
        async def wait_idle():
            async with asyncio.timeout(3):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(0.005)
        client.portal.call(wait_idle)
        result = client.get(url + "/prompts", headers=headers).json()
        assert len(calls) == 1 and calls[0]["max_tokens"] == 512
        assert calls[0]["messages"][0]["content"] == LIVE_SYSTEM_PROMPT
        assert "主播你喜欢什么音乐？" in calls[0]["messages"][1]["content"]
        prompt = result["prompts"][0]
        assert prompt["prompt_type"] == "operation_notice"
        assert prompt["display_text"] == "可以先回应这个问题，再邀请大家补充。"
        assert prompt["expires_at_ms"] - prompt["generated_at_ms"] == 15_000
