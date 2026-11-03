import asyncio
import socket
import time
import types

import httpx
import uvicorn
from fastapi.testclient import TestClient

import screen_assistant_server.live as live
from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings


def test_empty_stopped_and_unauthorized_poll_metadata(tmp_path):
    clock = [1_790_000_000_000]
    config = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="",
                      tianyi_base_url="http://model.invalid", tianyi_model="unused",
                      dedup_ttl_seconds=60, max_sentence_chars=2000,
                      model_timeout_seconds=1, model_connect_timeout_seconds=1,
                      live_db_path=str(tmp_path / "live.sqlite3"))
    with TestClient(create_app(config, live_clock=lambda: clock[0])) as client:
        session = client.post("/v1/live/sessions", json={}).json()
        url = f"/v1/live/sessions/{session['session_id']}"
        headers = {"X-Live-Session-Token": session["session_token"]}
        empty = client.get(url + "/prompts", headers=headers)
        assert empty.status_code == 200
        assert empty.json()["server_time_ms"] == clock[0]
        assert isinstance(empty.json()["server_processing_ms"], int)
        assert empty.headers["cache-control"] == "no-store"
        assert client.get(url + "/prompts").status_code == 403
        clock[0] += 1000
        refreshed = client.get(url + "/prompts", headers=headers).json()
        assert refreshed["server_time_ms"] == clock[0]
        assert refreshed["prompts"] == [] and not refreshed["stopped"]
        assert client.post(url + "/stop", headers=headers).status_code == 200
        clock[0] += 1000
        stopped = client.get(url + "/prompts", headers=headers)
        assert stopped.status_code == 200 and stopped.json()["stopped"]
        assert stopped.json()["server_time_ms"] == clock[0]
        assert stopped.headers["cache-control"] == "no-store"


def test_long_poll_clock_metadata_and_old_event_reread(tmp_path, monkeypatch):
    wall = [1_790_000_000_000]
    monotonic = [0]
    config = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="",
                      tianyi_base_url="http://model.invalid", tianyi_model="unused",
                      dedup_ttl_seconds=60, max_sentence_chars=2000,
                      model_timeout_seconds=1, model_connect_timeout_seconds=1,
                      live_db_path=str(tmp_path / "live.sqlite3"))
    with TestClient(create_app(config, live_clock=lambda: wall[0])) as client:
        session = client.post("/v1/live/sessions", json={}).json()
        url = f"/v1/live/sessions/{session['session_id']}"
        headers = {"X-Live-Session-Token": session["session_token"]}
        body = {"input_mode": "test_fixture", "source": "fixture:chat",
                "coverage": "structured_sample", "reliability": "known",
                "text": "主播你是哪里人？"}
        service = client.app.state.live
        original = service.poll
        first_poll = [True]

        async def controlled_poll(sid, token, after, wait_ms):
            assert wait_ms == 15000
            if first_poll[0]:
                first_poll[0] = False
                async def wake_with_new_prompt():
                    # 等待真实 waiter 注册，再推进受控时钟并创建提示唤醒它。
                    async with asyncio.timeout(2):
                        while not service.waiters.get(sid):
                            await asyncio.sleep(0.001)
                    monotonic[0] += 15_000_999_999
                    wall[0] += 15_000
                    for key in ("first", "second"):
                        payload = live.CommentObservation(**body, event_id=key,
                                                          platform_message_id=key).model_dump()
                        await service.run("add_comment", sid, token, payload, wall[0])
                feeder = asyncio.create_task(wake_with_new_prompt())
                try:
                    return await original(sid, token, after, wait_ms)
                finally:
                    await feeder
            return await original(sid, token, after, wait_ms)

        monkeypatch.setattr(live, "time", types.SimpleNamespace(monotonic_ns=lambda: monotonic[0]))
        monkeypatch.setattr(service, "poll", controlled_poll)
        response = client.get(url + "/prompts?wait_ms=15000", headers=headers)
        assert response.status_code == 200
        page = response.json()
        assert page["server_time_ms"] == wall[0]
        assert page["server_processing_ms"] == 15_000
        assert len(page["prompts"]) == 1 and not page["stopped"]
        prompt = page["prompts"][0]
        assert prompt["returned_at_ms"] == wall[0]
        remaining = prompt["expires_at_ms"] - page["server_time_ms"]
        assert remaining == 15_000
        assert remaining - (15_100 - page["server_processing_ms"]) - 1 == 14_899
        assert max(0, remaining - (31_000 - page["server_processing_ms"]) - 1) == 0
        wall[0] += 1000
        reread = client.get(url + "/prompts?wait_ms=15000", headers=headers).json()
        assert reread["prompts"][0]["returned_at_ms"] == prompt["returned_at_ms"]
        assert reread["server_time_ms"] > page["server_time_ms"]


def test_delayed_loopback_response_reduces_remaining_lifetime(tmp_path):
    async def run():
        wall = [1_790_000_000_000]
        config = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="",
                          tianyi_base_url="http://model.invalid", tianyi_model="unused",
                          dedup_ttl_seconds=60, max_sentence_chars=2000,
                          model_timeout_seconds=1, model_connect_timeout_seconds=1,
                          live_db_path=str(tmp_path / "slow.sqlite3"))
        app = create_app(config, live_clock=lambda: wall[0])

        async def delayed_body(scope, receive, send):
            async def delayed_send(message):
                if (scope.get("type") == "http" and scope.get("path", "").endswith("/prompts")
                        and message["type"] == "http.response.body"):
                    # 在真实回环 HTTP 响应体边界模拟慢传输。
                    await asyncio.sleep(0.4)
                await send(message)
            await app(scope, receive, delayed_send)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(delayed_body, log_level="critical", lifespan="on"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    await asyncio.sleep(0.005)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                url = f"/v1/live/sessions/{session['session_id']}"
                headers = {"X-Live-Session-Token": session["session_token"]}
                body = {"input_mode": "test_fixture", "source": "fixture:chat",
                        "coverage": "structured_sample", "reliability": "known",
                        "text": "主播你是哪里人？"}
                for key in ("first", "second"):
                    response = await client.post(url + "/comments", headers=headers,
                                                 json={**body, "event_id": key, "platform_message_id": key})
                    assert response.status_code == 200
                wall[0] += 14_800
                started = time.monotonic_ns()
                response = await client.get(url + "/prompts", headers=headers)
                rtt = (time.monotonic_ns() - started + 999_999) // 1_000_000
                page = response.json()
                assert response.status_code == 200 and len(page["prompts"]) == 1
                assert page["prompts"][0]["expires_at_ms"] - page["server_time_ms"] == 200
                assert page["server_processing_ms"] <= rtt
                assert max(0, 200 - (rtt - page["server_processing_ms"]) - 1) == 0
        finally:
            server.should_exit = True
            await task
            sock.close()
    asyncio.run(run())
