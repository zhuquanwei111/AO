"""Offline scheduling regression using a controlled model and isolated SQLite."""
import asyncio
import sqlite3

import httpx
import pytest

from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings


def test_fusion_limits_are_validated(tmp_path):
    args = dict(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="",
                tianyi_base_url="http://model.invalid", tianyi_model="fake",
                dedup_ttl_seconds=60, max_sentence_chars=2000, model_timeout_seconds=1,
                model_connect_timeout_seconds=1, live_db_path=str(tmp_path / "config.sqlite3"))
    cfg = Settings(**args)
    assert (cfg.live_fusion_concurrency, cfg.live_fusion_queue_limit) == (2, 16)
    for overrides in ({"live_fusion_concurrency": 0}, {"live_fusion_concurrency": 17},
                      {"live_fusion_queue_limit": 0}, {"live_fusion_queue_limit": 257}):
        with pytest.raises(ValueError):
            Settings(**args, **overrides)


def test_trigger_after_task_completion_is_not_lost(tmp_path, monkeypatch):
    import screen_assistant_server.live as live
    monkeypatch.setattr(live, "FUSION_SETTLE_MS", 0)

    async def run():
        service = live.LiveService(str(tmp_path / "unused.sqlite3"), lambda: 0)
        completed = asyncio.create_task(asyncio.sleep(0))
        await completed
        service.fusion_active["session"] = completed
        entered = asyncio.Event()
        async def deliver(*args, **kwargs):
            entered.set()
        service.deliver_fusion = deliver
        assert service.schedule_fusion("session", "token", None)
        # A delayed callback for the old task must not remove the replacement.
        service._fusion_done("session", completed)
        await asyncio.wait_for(entered.wait(), 1)
        await service.close_fusion()

    asyncio.run(run())


def test_comment_reply_candidate_uses_bounded_scheduler_and_prompt_dto(tmp_path, monkeypatch):
    import screen_assistant_server.live as live
    monkeypatch.setattr(live, "FUSION_SETTLE_MS", 0)
    now = 1_790_000_000_000

    async def run():
        cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="fake",
                       tianyi_base_url="http://model.invalid", tianyi_model="fake",
                       dedup_ttl_seconds=60, max_sentence_chars=2000,
                       model_timeout_seconds=2, model_connect_timeout_seconds=1,
                       live_db_path=str(tmp_path / "reply.sqlite3"))
        app = create_app(cfg, live_clock=lambda: now)
        calls = []
        class Model:
            async def reply_to_comment(self, text, *, nickname, priority_eligible):
                calls.append((text, nickname, priority_eligible))
                return "谢谢你的祝福。"
            async def aclose(self):
                pass
        async with app.router.lifespan_context(app):
            app.state.model = Model()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                sid, token = session["session_id"], session["session_token"]
                row = {"event_id": "e1", "kind": "comment", "source": "ios_replaykit_ocr",
                       "coverage": "ocr_visible_sample", "reliability": "known", "confidence": 0.9,
                       "observed_at_ms": now, "effective_at_ms": now, "received_at_ms": now,
                       "content": {"text": "小明：bless you", "text_position": None}}
                candidate = {"topic": "comment:c1", "priority": 25, "phenomenon": "公屏评论",
                             "observations": [row], "model_text": "bless you", "suggestion": None,
                             "prompt_type": "comment_reply", "priority_eligible": True,
                             "reply_to": {"comment_id": "c1", "nickname": "小明", "text": "bless you"}}
                app.state.live.store.fusion_candidate = lambda *_: candidate
                app.state.live.schedule_fusion(sid, token, type("Request", (), {"app": app})())
                task = app.state.live.fusion_active[sid]
                await asyncio.wait_for(task, 2)
                response = await client.get(f"/v1/live/sessions/{sid}/prompts",
                                            headers={"X-Live-Session-Token": token})
                prompt = response.json()["prompts"][0]
                assert calls == [("bless you", "小明", True)]
                assert prompt["prompt_type"] == "comment_reply"
                assert prompt["reply_to"] == candidate["reply_to"]
                assert prompt["display_text"] == "谢谢你的祝福。"
    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["quiet", "timeout", "invalid"])
def test_comment_reply_without_valid_model_result_writes_no_prompt(tmp_path, monkeypatch, outcome):
    import screen_assistant_server.live as live
    from screen_assistant_server.model_client import ModelUnavailableError
    monkeypatch.setattr(live, "FUSION_SETTLE_MS", 0)
    now = 1_790_000_000_000
    async def run():
        cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="fake",
                       tianyi_base_url="http://model.invalid", tianyi_model="fake",
                       dedup_ttl_seconds=60, max_sentence_chars=2000,
                       model_timeout_seconds=0.1, model_connect_timeout_seconds=0.1,
                       live_db_path=str(tmp_path / f"reply-{outcome}.sqlite3"))
        app = create_app(cfg, live_clock=lambda: now)
        class Model:
            async def reply_to_comment(self, *args, **kwargs):
                if outcome == "timeout":
                    await asyncio.Event().wait()
                if outcome == "invalid":
                    raise ModelUnavailableError("model_output_invalid")
                return None
            async def aclose(self):
                pass
        async with app.router.lifespan_context(app):
            app.state.model = Model()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                sid, token = session["session_id"], session["session_token"]
                candidate = {"model_text": "你好", "suggestion": None, "prompt_type": "comment_reply",
                             "reply_to": {"comment_id": "c1", "nickname": None, "text": "你好"}}
                app.state.live.store.fusion_candidate = lambda *_: candidate
                assert app.state.live.schedule_fusion(sid, token, type("Request", (), {"app": app})())
                await asyncio.wait_for(app.state.live.fusion_active[sid], 2)
                response = await client.get(f"/v1/live/sessions/{sid}/prompts",
                                            headers={"X-Live-Session-Token": token})
                assert response.json()["prompts"] == []
    asyncio.run(run())


def test_batch_does_not_call_model_until_late_second_observation(tmp_path):
    now = 1_790_000_000_000
    async def run():
        cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="fake",
                       tianyi_base_url="http://model.invalid", tianyi_model="fake",
                       dedup_ttl_seconds=60, max_sentence_chars=2000,
                       model_timeout_seconds=2, model_connect_timeout_seconds=1,
                       live_db_path=str(tmp_path / "batch-gate.sqlite3"))
        app = create_app(cfg, live_clock=lambda: now)
        calls = []
        class Model:
            async def suggest_live(self, text, *args, **kwargs):
                calls.append(text)
                return "可以顺着问题聊两句。"
            async def aclose(self):
                pass
        async with app.router.lifespan_context(app):
            app.state.model = Model()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                sid, token = session["session_id"], session["session_token"]
                url = f"/v1/live/sessions/{sid}/observations"
                headers = {"X-Live-Session-Token": token}
                first = {"kind": "comment", "event_id": "e1", "source": "ios_replaykit_ocr",
                         "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
                         "reliability": "known", "capture_batch_id": "batch", "line_index": 0,
                         "capture_comment_count": 2, "content": {"text": "主播你是哪里人？"}}
                assert (await client.post(url, headers=headers, json=first)).json()["reason"] == "batch_incomplete"
                await asyncio.sleep(0.25)
                assert calls == []
                second = {**first, "event_id": "e2", "line_index": 7, "content": {"text": "续句"}}
                assert (await client.post(url, headers=headers, json=second)).json()["reason"] == "scheduled"
                await asyncio.wait_for(app.state.live.fusion_active[sid], 2)
                assert len(calls) == 1
    asyncio.run(run())


def test_incomplete_batch_does_not_block_legacy_observation(tmp_path, monkeypatch):
    import screen_assistant_server.live as live
    monkeypatch.setattr(live, "FUSION_SETTLE_MS", 0)
    now = 1_790_000_000_000
    async def run():
        cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="fake",
                       tianyi_base_url="http://model.invalid", tianyi_model="fake",
                       dedup_ttl_seconds=60, max_sentence_chars=2000,
                       model_timeout_seconds=2, model_connect_timeout_seconds=1,
                       live_db_path=str(tmp_path / "batch-separate.sqlite3"))
        app = create_app(cfg, live_clock=lambda: now)
        calls = []
        class Model:
            async def suggest_live(self, text, *args, **kwargs):
                calls.append(text)
                return "可以顺着问题聊两句。"
            async def aclose(self):
                pass
        async with app.router.lifespan_context(app):
            app.state.model = Model()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                sid, token = session["session_id"], session["session_token"]
                headers = {"X-Live-Session-Token": token}
                partial = {"kind": "comment", "event_id": "batch-e1", "source": "ios_replaykit_ocr",
                           "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
                           "reliability": "known", "capture_batch_id": "batch", "line_index": 0,
                           "capture_comment_count": 2, "content": {"text": "主播几点开播？"}}
                response = await client.post(f"/v1/live/sessions/{sid}/observations", headers=headers, json=partial)
                assert response.json()["reason"] == "batch_incomplete"
                legacy = {"event_id": "legacy-e1", "input_mode": "live_observation",
                          "source": "ios_replaykit_ocr", "coverage": "ocr_visible_sample",
                          "reliability": "known", "text": "主播你是哪里人？"}
                response = await client.post(f"/v1/live/sessions/{sid}/comments", headers=headers, json=legacy)
                assert response.json()["reason"] == "scheduled"
                await asyncio.wait_for(app.state.live.fusion_active[sid], 2)
                assert calls == ["主播你是哪里人？"]
                assert app.state.live.store.comment_batches[sid][("ios_replaykit_ocr", "batch")]["complete"] is False
    asyncio.run(run())


def test_observation_returns_while_model_is_blocked(tmp_path):
    async def run():
        cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="fake",
                       tianyi_base_url="http://model.invalid", tianyi_model="fake",
                       dedup_ttl_seconds=60, max_sentence_chars=2000,
                       model_timeout_seconds=5, model_connect_timeout_seconds=1,
                       live_db_path=str(tmp_path / "live.sqlite3"))
        app = create_app(cfg)
        entered, release = asyncio.Event(), asyncio.Event()

        class Model:
            async def suggest_live(self, text, *args, **kwargs):
                entered.set()
                await release.wait()
                return "可以顺着问题聊两句。"

            async def aclose(self):
                pass

        async with app.router.lifespan_context(app):
            app.state.model = Model()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://fixture") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                url = f"/v1/live/sessions/{session['session_id']}/comments"
                headers = {"X-Live-Session-Token": session["session_token"]}
                body = {"event_id": "one", "input_mode": "live_observation",
                        "source": "ios_replaykit_ocr", "coverage": "ocr_visible_sample",
                        "reliability": "known", "text": "主播你是哪里人？"}
                task = asyncio.create_task(client.post(url, headers=headers, json=body))
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    done, _ = await asyncio.wait({task}, timeout=0.2)
                    assert task in done, "HTTP response still waits for the model"
                    assert task.result().status_code == 200
                finally:
                    release.set()
                    await task

    asyncio.run(run())


def test_bounded_cross_session_coalescing_and_stop(tmp_path, monkeypatch):
    import screen_assistant_server.live as live
    monkeypatch.setattr(live, "FUSION_SETTLE_MS", 0)

    async def run():
        cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="fake",
                       tianyi_base_url="http://model.invalid", tianyi_model="fake",
                       dedup_ttl_seconds=60, max_sentence_chars=2000,
                       model_timeout_seconds=5, model_connect_timeout_seconds=1,
                       live_db_path=str(tmp_path / "bounded.sqlite3"), live_fusion_queue_limit=1)
        app = create_app(cfg)
        entered = asyncio.Queue()
        release = asyncio.Event()
        active = 0
        peak = 0

        class Model:
            async def suggest_live(self, text, *args, **kwargs):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await entered.put(text)
                try:
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        # Simulate a remote operation whose result arrives after cancellation.
                        await release.wait()
                    return "可以顺着问题聊两句。"
                finally:
                    active -= 1

            async def aclose(self):
                pass

        async with app.router.lifespan_context(app):
            app.state.model = Model()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
                sessions = [(await client.post("/v1/live/sessions", json={})).json() for _ in range(4)]
                def target(index):
                    s = sessions[index]
                    return (f"/v1/live/sessions/{s['session_id']}",
                            {"X-Live-Session-Token": s["session_token"]})
                async def submit(index, event):
                    url, headers = target(index)
                    return await client.post(url + "/comments", headers=headers, json={
                        "event_id": event, "input_mode": "live_observation",
                        "source": "ios_replaykit_ocr", "coverage": "ocr_visible_sample",
                        "reliability": "known", "text": "主播你是哪里人？"})

                accepted = (await submit(0, "a")).json()
                assert accepted["status"] == "accepted" and accepted["reason"] == "scheduled"
                assert accepted["prompt_id"] is None and accepted["time_basis"] == "received"
                assert accepted["received_at_ms"] == accepted["effective_at_ms"]
                assert (await submit(0, "a")).json()["reason"] == "duplicate_event"
                await asyncio.wait_for(entered.get(), 2)
                assert (await submit(1, "b")).json()["reason"] == "scheduled"
                await asyncio.wait_for(entered.get(), 2)
                for i in range(10):
                    assert (await submit(0, f"a-{i}")).json()["reason"] == "scheduled"
                assert (await submit(2, "c")).json()["reason"] == "scheduled"
                busy = (await submit(3, "d")).json()
                assert busy["status"] == "accepted" and busy["reason"] == "analysis_busy"
                assert busy["prompt_id"] is None and peak == 2
                assert len(app.state.live.fusion_active) == 2
                assert len(app.state.live.fusion_queue) == 1
                assert len(app.state.live.fusion_dirty) == 1
                url_b, headers_b = target(1)
                notice = {"kind": "platform_notice", "event_id": "warning",
                          "source": "fixture:platform_notice_region",
                          "availability": "available", "coverage": "confirmed_notice_region",
                          "reliability": "known", "confidence": 0.9,
                          "content": {"text": "请查看直播提示", "region_confirmed": True,
                                      "explicit_warning_confirmed": False}}
                urgent = await client.post(url_b + "/observations", headers=headers_b, json=notice)
                assert urgent.json()["reason"] == "generated"
                poll = await client.get(url_b + "/prompts", headers=headers_b)
                assert len(poll.json()["prompts"]) == 1
                with sqlite3.connect(cfg.live_db_path) as db:
                    assert db.execute("SELECT COUNT(*) FROM live_observations").fetchone()[0] == 15
                url, headers = target(0)
                stop = asyncio.create_task(client.post(url + "/stop", headers=headers))
                async def stopping():
                    while sessions[0]["session_id"] not in app.state.live.fusion_stopping:
                        await asyncio.sleep(0.001)
                await asyncio.wait_for(stopping(), 2)
                release.set()
                assert (await asyncio.wait_for(stop, 2)).status_code == 200
                assert (await client.get(url + "/events", headers=headers)).json()["events"] == []
                await asyncio.wait_for(entered.get(), 2)
                assert peak <= 2

    asyncio.run(run())


def test_failure_and_timeout_release_scheduling_capacity(tmp_path, monkeypatch):
    import screen_assistant_server.live as live
    monkeypatch.setattr(live, "FUSION_SETTLE_MS", 0)

    async def run():
        cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="fake",
                       tianyi_base_url="http://model.invalid", tianyi_model="fake",
                       dedup_ttl_seconds=60, max_sentence_chars=2000,
                       model_timeout_seconds=0.05, model_connect_timeout_seconds=1,
                       live_db_path=str(tmp_path / "failure.sqlite3"), live_fusion_concurrency=1)
        app = create_app(cfg)
        entered = asyncio.Queue()

        class Model:
            async def suggest_live(self, text, *args, **kwargs):
                await entered.put(text)
                if text == "主播你是哪里人？":
                    raise RuntimeError("controlled failure")
                if text == "主播几点开播？":
                    await asyncio.Event().wait()
                return "可以顺着问题聊两句。"
            async def aclose(self):
                pass

        async with app.router.lifespan_context(app):
            app.state.model = Model()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
                for text in ("主播你是哪里人？", "主播几点开播？", "主播喜欢什么？"):
                    s = (await client.post("/v1/live/sessions", json={})).json()
                    url = f"/v1/live/sessions/{s['session_id']}"
                    headers = {"X-Live-Session-Token": s["session_token"]}
                    response = await client.post(url + "/comments", headers=headers, json={
                        "event_id": text, "input_mode": "live_observation", "source": "ios_replaykit_ocr",
                        "coverage": "ocr_visible_sample", "reliability": "known", "text": text})
                    assert response.json()["reason"] == "scheduled"
                    assert await asyncio.wait_for(entered.get(), 2) == text
                    async def idle():
                        while app.state.live.fusion_active:
                            await asyncio.sleep(0.001)
                    await asyncio.wait_for(idle(), 2)
                events = (await client.get(url + "/events", headers=headers)).json()["events"]
                assert len(events) == 1 and "可以顺着问题聊两句" in events[0]["display_text"]

    asyncio.run(run())


def test_shutdown_cancels_and_discards_late_result(tmp_path, monkeypatch):
    import screen_assistant_server.live as live
    monkeypatch.setattr(live, "FUSION_SETTLE_MS", 0)

    async def run():
        path = tmp_path / "shutdown.sqlite3"
        cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="fake",
                       tianyi_base_url="http://model.invalid", tianyi_model="fake",
                       dedup_ttl_seconds=60, max_sentence_chars=2000,
                       model_timeout_seconds=5, model_connect_timeout_seconds=1, live_db_path=str(path))
        app = create_app(cfg)
        entered, release = asyncio.Event(), asyncio.Event()
        class Model:
            async def suggest_live(self, text, *args, **kwargs):
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return "可以顺着问题聊两句。"
            async def aclose(self):
                pass

        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        app.state.model = Model()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            session = (await client.post("/v1/live/sessions", json={})).json()
            url = f"/v1/live/sessions/{session['session_id']}/comments"
            response = await client.post(url, headers={"X-Live-Session-Token": session["session_token"]},
                                         json={"event_id": "one", "input_mode": "live_observation",
                                               "source": "ios_replaykit_ocr", "coverage": "ocr_visible_sample",
                                               "reliability": "known", "text": "主播你是哪里人？"})
            assert response.json()["reason"] == "scheduled"
            await asyncio.wait_for(entered.wait(), 2)
            closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
            async def closed():
                while not app.state.live.fusion_closed:
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(closed(), 2)
            release.set()
            await asyncio.wait_for(closing, 2)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM live_events").fetchone()[0] == 0

    asyncio.run(run())
