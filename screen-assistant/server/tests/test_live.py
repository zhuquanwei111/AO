"""M2 契约：显式测试观察、临时 SQLite；不调用模型或 Redis。"""
from __future__ import annotations

import sqlite3
import asyncio

import httpx
import pytest

from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings


class Clock:
    def __init__(self):
        self.now = 1_790_000_000_000

    def __call__(self):
        return self.now


def settings(path):
    return Settings(
        redis_url="redis://127.0.0.1:1/0",
        tianyi_api_key="", tianyi_base_url="http://model.invalid",
        tianyi_model="unused", dedup_ttl_seconds=86400,
        max_sentence_chars=2000, model_timeout_seconds=1,
        model_connect_timeout_seconds=1, live_db_path=str(path),
    )


def create(client, **body):
    response = client.post("/v1/live/sessions", json=body)
    assert response.status_code == 201
    data = response.json()
    return data, {"X-Live-Session-Token": data["session_token"]}


def test_session_auth_stop_and_reopen(tmp_path):
    path = tmp_path / "live.sqlite3"
    clock = Clock()
    with TestClient(create_app(settings(path), live_clock=clock)) as client:
        session, headers = create(client)
        sid = session["session_id"]
        assert session["started_at_ms"] == clock.now
        url = f"/v1/live/sessions/{sid}"
        assert client.get(url + "/events").status_code == 403
        assert client.get(url + "/events", headers=headers).json()["events"] == []
        other, _ = create(client, description="测试场：聊天")
        assert client.get(f"/v1/live/sessions/{other['session_id']}/events",
                          headers=headers).status_code == 403
        first = client.post(url + "/stop", headers=headers)
        clock.now += 500
        assert client.post(url + "/stop", headers=headers).json() == first.json()
    with sqlite3.connect(path) as db:
        dump = "\n".join(db.iterdump())
        assert session["session_token"] not in dump
    with TestClient(create_app(settings(path), live_clock=clock)) as client:
        result = client.get(url + "/events", headers=headers)
        assert result.status_code == 200
        assert result.json()["stopped_at_ms"] == first.json()["stopped_at_ms"]


def observation(event_id, **changes):
    value = {"event_id": event_id, "input_mode": "test_fixture",
             "source": "fixture:chat", "coverage": "structured_sample",
             "reliability": "known", "text": "主播你是哪里人？",
             "platform_message_id": event_id}
    value.update(changes)
    return value


def post_comment(client, sid, headers, event_id, **changes):
    response = client.post(f"/v1/live/sessions/{sid}/comments", headers=headers,
                           json=observation(event_id, **changes))
    assert response.status_code == 200, response.text
    return response.json()


def test_fixture_loop_dedup_independent_questions_and_reopen(tmp_path):
    path, clock = tmp_path / "live.sqlite3", Clock()
    with TestClient(create_app(settings(path), live_clock=clock)) as client:
        session, headers = create(client)
        sid, started = session["session_id"], session["started_at_ms"]
        url = f"/v1/live/sessions/{sid}"
        first = post_comment(client, sid, headers, "m1", author_id="alice")
        assert first["counted"] and first["prompt_id"] is None
        assert post_comment(client, sid, headers, "m1", author_id="alice")["status"] == "duplicate"
        assert post_comment(client, sid, headers, "retry-new-id", platform_message_id="m1")["reason"] == "duplicate_message"
        assert client.get(url + "/prompts", headers=headers).json()["prompts"] == []
        clock.now += 1000
        second = post_comment(client, sid, headers, "m2", text="你来自哪里？", author_id="bob")
        assert second["prompt_id"]
        prompts = client.get(url + "/prompts", headers=headers).json()
        assert len(prompts["prompts"]) == 1
        event = prompts["prompts"][0]
        assert event["observation_count"] == 2
        assert event["start_offset_ms"] == 0 and event["end_offset_ms"] == 1000
        assert event["time_basis"] == "received"
        assert event["display_text"] == "公屏反复问“来自哪里”，可以集中答一下。"
        assert event["delivery_state"] == "returned_to_client"
        assert event["start_at_ms"] == started
        assert client.get(url + "/prompts", headers=headers,
                          params={"after": prompts["next_cursor"]}).json()["prompts"] == []
        other, other_headers = create(client)
        post_comment(client, other["session_id"], other_headers, "m1")
        assert client.get(f"/v1/live/sessions/{other['session_id']}/events", headers=other_headers).json()["events"] == []
        assert client.get(f"/v1/live/sessions/{other['session_id']}/prompts", headers=other_headers,
                          params={"after": prompts["next_cursor"]}).status_code == 422
        client.post(url + "/stop", headers=headers)
        assert client.post(url + "/comments", headers=headers,
                           json=observation("after-stop")).status_code == 409
        assert client.get(url + "/prompts", headers=headers).json()["prompts"] == []
    with TestClient(create_app(settings(path), live_clock=clock)) as client:
        saved = client.get(url + "/events", headers=headers).json()["events"]
        assert saved == [event]
    with sqlite3.connect(path) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(live_observations)")]
        assert "text" not in columns and "token" not in columns
        assert db.execute("SELECT COUNT(*) FROM live_observations WHERE session_id=?", (sid,)).fetchone()[0] == 3


def test_unknown_ocr_no_evidence_and_opposite_questions_are_quiet(tmp_path):
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())) as client:
        session, headers = create(client)
        sid = session["session_id"]
        assert post_comment(client, sid, headers, "u", reliability="unknown")["reason"] == "unreliable"
        assert post_comment(client, sid, headers, "o", coverage="ocr_visible_sample")["reason"] == "ocr_tracking_unavailable"
        assert post_comment(client, sid, headers, "n", platform_message_id=None)["reason"] == "no_independent_evidence"
        post_comment(client, sid, headers, "positive", text="你是北京人吗？")
        post_comment(client, sid, headers, "negative", text="你不是北京人吗？")
        post_comment(client, sid, headers, "chat", text="这首歌真好听")
        for i in range(2):
            post_comment(client, sid, headers, f"statement-{i}", text="我知道你来自哪里")
        assert client.get(f"/v1/live/sessions/{sid}/prompts", headers=headers).json()["prompts"] == []


def test_cooldown_expiry_late_window_and_source_time(tmp_path):
    from screen_assistant_server.live_rules import COOLDOWN_MS, PROMPT_TTL_MS, WINDOW_MS
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        sid, start = session["session_id"], clock.now
        url = f"/v1/live/sessions/{sid}"
        post_comment(client, sid, headers, "a", observed_at_ms=start)
        clock.now += 400
        post_comment(client, sid, headers, "b", observed_at_ms=clock.now)
        generated = client.get(url + "/events", headers=headers).json()["events"][0]
        assert generated["time_basis"] == "observed"
        assert generated["delivery_state"] == "generated" and generated["returned_at_ms"] is None
        clock.now += PROMPT_TTL_MS
        assert client.get(url + "/prompts", headers=headers).json()["prompts"] == []
        assert post_comment(client, sid, headers, "late", observed_at_ms=start)["reason"] == "late"
        assert post_comment(client, sid, headers, "cooldown")["reason"] == "cooldown"
        clock.now = start + COOLDOWN_MS + 1000
        assert post_comment(client, sid, headers, "new1")["prompt_id"] is None
        assert post_comment(client, sid, headers, "new2")["prompt_id"]
        assert len(client.get(url + "/events", headers=headers).json()["events"]) == 2
        other, oh = create(client)
        osid = other["session_id"]
        post_comment(client, osid, oh, "window1")
        clock.now += WINDOW_MS + 1
        assert post_comment(client, osid, oh, "window2")["prompt_id"] is None
        for timestamp in (-1, start-1, clock.now+1, True, 1.5):
            assert client.post(url + "/comments", headers=headers,
                               json=observation("bad-time", observed_at_ms=timestamp)).status_code == 422


def test_point_event_anonymous_authors_and_idempotency_conflict(tmp_path):
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        sid, url = session["session_id"], f"/v1/live/sessions/{session['session_id']}"
        post_comment(client, sid, headers, "one")
        post_comment(client, sid, headers, "two")
        event = client.get(url + "/events", headers=headers).json()["events"][0]
        assert event["start_offset_ms"] == event["end_offset_ms"] == 0
        assert "多位" not in event["display_text"] and "人" not in event["display_text"]
        assert client.post(url + "/comments", headers=headers,
                           json=observation("one", text=" changed ")).status_code == 409
        assert client.get(url + "/events", headers={"X-Live-Session-Token": "wrong"}).status_code == 403
        assert client.get(url + "/prompts", headers=headers, params={"after": "bad!"}).status_code == 422
        assert client.get(url + "/prompts", headers=headers, params={"wait_ms": 15001}).status_code == 422


def test_only_author_evidence_counts_each_author_once(tmp_path):
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())) as client:
        session, headers = create(client)
        sid = session["session_id"]
        post_comment(client, sid, headers, "a1", platform_message_id=None, author_id="a")
        assert post_comment(client, sid, headers, "a2", platform_message_id=None,
                            author_id="a")["reason"] == "same_author_without_message_id"
        assert post_comment(client, sid, headers, "b1", platform_message_id=None, author_id="b")["prompt_id"]


def test_restart_keeps_idempotency_but_does_not_replay_raw_window(tmp_path):
    path, clock = tmp_path / "live.sqlite3", Clock()
    for index in range(2):
        with TestClient(create_app(settings(path), live_clock=clock)) as client:
            if index == 0:
                session, headers = create(client)
                sid = session["session_id"]
                post_comment(client, sid, headers, "one")
            else:
                assert post_comment(client, sid, headers, "one")["status"] == "duplicate"
                assert post_comment(client, sid, headers, "two")["prompt_id"] is None


def test_comment_and_event_are_one_transaction(tmp_path):
    app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())
    with TestClient(app, raise_server_exceptions=False) as client:
        session, headers = create(client)
        sid = session["session_id"]
        post_comment(client, sid, headers, "first")
        db = app.state.live.store.db
        db.execute("CREATE TRIGGER reject_observation BEFORE INSERT ON live_observations BEGIN SELECT RAISE(FAIL, 'fixture'); END")
        response = client.post(f"/v1/live/sessions/{sid}/comments", headers=headers,
                               json=observation("second"))
        assert response.status_code == 503
        assert client.get(f"/v1/live/sessions/{sid}/events", headers=headers).json()["events"] == []
        db.execute("DROP TRIGGER reject_observation")
        assert post_comment(client, sid, headers, "second")["prompt_id"]


def test_create_without_description_body(tmp_path):
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"))) as client:
        assert client.post("/v1/live/sessions").status_code == 201


async def wait_for_waiters(app, count):
    async def ready():
        while sum(len(v) for v in app.state.live.waiters.values()) != count:
            await asyncio.sleep(0.001)
    await asyncio.wait_for(ready(), 2)


def test_long_poll_wakeup_stop_timeout_waiter_limit_and_cancel(tmp_path):
    from screen_assistant_server.live_rules import MAX_SESSION_WAITERS

    async def run():
        app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                sid = session["session_id"]
                headers = {"X-Live-Session-Token": session["session_token"]}
                url = f"/v1/live/sessions/{sid}"
                poll = asyncio.create_task(client.get(url + "/prompts", headers=headers, params={"wait_ms": 1000}))
                await wait_for_waiters(app, 1)
                for mid in ("first", "second"):
                    r = await asyncio.wait_for(client.post(url + "/comments", headers=headers, json=observation(mid)), 0.5)
                    assert r.status_code == 200
                result = (await asyncio.wait_for(poll, 0.5)).json()
                assert len(result["prompts"]) == 1
                after = result["next_cursor"]
                start = asyncio.get_running_loop().time()
                empty = await client.get(url + "/prompts", headers=headers, params={"after": after, "wait_ms": 40})
                assert empty.json()["prompts"] == []
                assert 0.025 <= asyncio.get_running_loop().time()-start < 1
                polls = [asyncio.create_task(client.get(url + "/prompts", headers=headers,
                         params={"after": after, "wait_ms": 1000})) for _ in range(MAX_SESSION_WAITERS)]
                await wait_for_waiters(app, MAX_SESSION_WAITERS)
                assert (await client.get(url + "/prompts", headers=headers, params={"after": after, "wait_ms": 1000})).status_code == 429
                polls[0].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await polls[0]
                await wait_for_waiters(app, MAX_SESSION_WAITERS-1)
                assert (await client.post(url + "/stop", headers=headers)).status_code == 200
                for pending in polls[1:]:
                    stopped = (await asyncio.wait_for(pending, 0.5)).json()
                    assert stopped["stopped"] and stopped["prompts"] == []
                assert not app.state.live.waiters
    asyncio.run(run())


@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "/events", None), ("GET", "/prompts", None),
    ("POST", "/stop", None), ("POST", "/comments", observation("x")),
])
def test_all_session_routes_require_matching_token(tmp_path, method, suffix, body):
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"))) as client:
        session, _ = create(client)
        url = f"/v1/live/sessions/{session['session_id']}" + suffix
        assert client.request(method, url, json=body).status_code == 403
        assert client.request(method, url, headers={"X-Live-Session-Token": "bad"}, json=body).status_code == 403


def test_concurrent_duplicate_submissions_create_one_event(tmp_path):
    async def run():
        app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                url = f"/v1/live/sessions/{session['session_id']}"
                headers = {"X-Live-Session-Token": session["session_token"]}
                requests = [client.post(url + "/comments", headers=headers, json=observation(f"m{i % 2}")) for i in range(12)]
                responses = await asyncio.gather(*requests)
                assert all(r.status_code == 200 for r in responses)
                assert sum(r.json()["counted"] for r in responses) == 2
                events = (await client.get(url + "/events", headers=headers)).json()["events"]
                assert len(events) == 1 and events[0]["observation_count"] == 2
    asyncio.run(run())


def test_pending_window_and_idempotency_metadata_limits(tmp_path, monkeypatch):
    import screen_assistant_server.live_store as store_module
    from screen_assistant_server.live_rules import PROMPT_TTL_MS
    monkeypatch.setattr(store_module, "MAX_PENDING", 2)
    monkeypatch.setattr(store_module, "MAX_WINDOW_OBSERVATIONS", 4)
    monkeypatch.setattr(store_module, "MAX_OBSERVATIONS", 8)
    clock = Clock()
    app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)
    with TestClient(app) as client:
        session, headers = create(client)
        sid, url = session["session_id"], f"/v1/live/sessions/{session['session_id']}"
        for i in range(3):
            for j in range(2):
                result = post_comment(client, sid, headers, f"{i}-{j}", text=f"问题{i}怎么做？")
            assert result["reason"] == ("generated" if i < 2 else "pending_limit")
        assert len(app.state.live.store.windows[sid]) <= 4
        clock.now += PROMPT_TTL_MS
        # 过期候选不占待返回名额；此处换一个新问题以避开主题冷却。
        for j in range(2):
            result = post_comment(client, sid, headers, f"new-{j}", text="问题3怎么做？")
        assert result["prompt_id"]
        assert client.post(url + "/comments", headers=headers, json=observation("overflow")).status_code == 429
        # 达到上限仍允许完全相同的幂等重试。
        assert post_comment(client, sid, headers, "new-1", text="问题3怎么做？")["status"] == "duplicate"


def test_no_original_comment_stream_or_token_in_storage_or_logs(tmp_path, caplog):
    path = tmp_path / "live.sqlite3"
    with TestClient(create_app(settings(path))) as client:
        session, headers = create(client)
        post_comment(client, session["session_id"], headers, "private", text="fixture-private-comment-123")
    with sqlite3.connect(path) as db:
        dump = "\n".join(db.iterdump())
    assert session["session_token"] not in dump + caplog.text
    assert "fixture-private-comment-123" not in dump + caplog.text


def test_commit_failure_does_not_add_ghost_window_observation(tmp_path):
    app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())
    with TestClient(app) as client:
        session, headers = create(client)
        sid = session["session_id"]
        db = app.state.live.store.db

        def deny_commit(action, first, *_):
            if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(deny_commit)
        response = client.post(f"/v1/live/sessions/{sid}/comments", headers=headers,
                               json=observation("one"))
        assert response.status_code == 503
        db.set_authorizer(None)
        assert post_comment(client, sid, headers, "one")["prompt_id"] is None
        assert post_comment(client, sid, headers, "two")["prompt_id"]


def test_sqlite_io_runs_off_the_event_loop(tmp_path):
    import time

    async def run():
        app = create_app(settings(tmp_path / "live.sqlite3"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                original = app.state.live.store.events
                started = asyncio.Event()
                loop = asyncio.get_running_loop()

                def slow(*args):
                    loop.call_soon_threadsafe(started.set)
                    time.sleep(0.15)
                    return original(*args)

                app.state.live.store.events = slow
                task = asyncio.create_task(client.get(f"/v1/live/sessions/{session['session_id']}/events",
                    headers={"X-Live-Session-Token": session["session_token"]}))
                await asyncio.wait_for(started.wait(), 1)
                await asyncio.sleep(0.01)
                assert not task.done()
                assert (await task).status_code == 200
    asyncio.run(run())


def test_live_storage_failure_does_not_break_legacy_contract(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("fixture", encoding="utf-8")
    with TestClient(create_app(settings(blocked / "live.sqlite3"))) as client:
        assert client.post("/v1/live/sessions", json={}).status_code == 503
        # 旧接口在调用模型/Redis 前的输入校验仍可独立执行。
        assert client.post("/v1/answer", json={"sentence": " "}).status_code == 422


def test_event_pagination_and_fresh_prompts_survive_reopen(tmp_path):
    path, clock = tmp_path / "live.sqlite3", Clock()
    with TestClient(create_app(settings(path), live_clock=clock)) as client:
        session, headers = create(client)
        sid, url = session["session_id"], f"/v1/live/sessions/{session['session_id']}"
        for i in range(3):
            for j in range(2):
                post_comment(client, sid, headers, f"{i}-{j}", text=f"问题{i}怎么做？")
        after, ids = "", []
        for i in range(3):
            page = client.get(url + "/events", headers=headers,
                              params={"after": after, "limit": 1}).json()
            assert len(page["events"]) == 1
            assert page["has_more"] is (i < 2)
            ids.append(page["events"][0]["prompt_id"])
            assert page["next_cursor"] != after
            after = page["next_cursor"]
        assert len(set(ids)) == 3
    with TestClient(create_app(settings(path), live_clock=clock)) as client:
        result = client.get(url + "/prompts", headers=headers).json()
        assert [p["prompt_id"] for p in result["prompts"]] == ids
        assert all(p["delivery_state"] == "returned_to_client" for p in result["prompts"])
        assert client.get(url + "/prompts", headers=headers,
                          params={"after": result["next_cursor"]}).json()["prompts"] == []


def test_poll_global_waiter_bound(tmp_path):
    from screen_assistant_server.live_rules import MAX_WAITERS, MAX_SESSION_WAITERS

    async def run():
        app = create_app(settings(tmp_path / "live.sqlite3"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                polls = []
                try:
                    for _ in range(MAX_WAITERS // MAX_SESSION_WAITERS):
                        session = (await client.post("/v1/live/sessions", json={})).json()
                        for _ in range(MAX_SESSION_WAITERS):
                            polls.append(asyncio.create_task(client.get(
                                f"/v1/live/sessions/{session['session_id']}/prompts",
                                headers={"X-Live-Session-Token": session["session_token"]}, params={"wait_ms": 5000})))
                    await wait_for_waiters(app, MAX_WAITERS)
                    other = (await client.post("/v1/live/sessions", json={})).json()
                    response = await client.get(f"/v1/live/sessions/{other['session_id']}/prompts",
                        headers={"X-Live-Session-Token": other["session_token"]}, params={"wait_ms": 100})
                    assert response.status_code == 429
                finally:
                    for task in polls:
                        task.cancel()
                    await asyncio.gather(*polls, return_exceptions=True)
                assert not app.state.live.waiters
    asyncio.run(run())


@pytest.mark.parametrize("changes", [
    {"input_mode": "real_capture"}, {"input_mode": None}, {"reliability": "guessed"},
    {"coverage": "full_platform_stream"}, {"text": " "}, {"text": "长" * 501},
    {"event_id": ""}, {"extra": "unexpected"},
])
def test_fixture_input_validation(tmp_path, changes):
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"))) as client:
        session, headers = create(client)
        response = client.post(f"/v1/live/sessions/{session['session_id']}/comments",
                               headers=headers, json={**observation("bad"), **changes})
        assert response.status_code == 422


def test_received_time_is_captured_before_store_queue_delay(tmp_path):
    clock = Clock()
    app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)
    with TestClient(app) as client:
        session, headers = create(client)
        started, sid = clock.now, session["session_id"]
        original = app.state.live.run

        async def delayed(method, *args):
            if method == "add_comment":
                clock.now += 500
            return await original(method, *args)

        app.state.live.run = delayed
        first = post_comment(client, sid, headers, "first")
        assert first["received_at_ms"] == first["effective_at_ms"] == started
        assert first["time_basis"] == "received"
        second = post_comment(client, sid, headers, "second")
        assert second["received_at_ms"] == started + 500
        duplicate = post_comment(client, sid, headers, "first")
        assert duplicate["received_at_ms"] == started
        event = client.get(f"/v1/live/sessions/{sid}/events", headers=headers).json()["events"][0]
        assert event["first_received_at_ms"] == started
        assert event["last_received_at_ms"] == started + 500
        assert event["start_offset_ms"] == 0 and event["end_offset_ms"] == 500
