"""训练机会按成功回复的固定时限保存，并在密集回复中获得有界选择。"""
import asyncio
import socket

import pytest
from fastapi.testclient import TestClient
from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings
from screen_assistant_server.live_store import LiveStore


def add_comment(store, sid, token, now, event, text):
    row = {"kind": "comment", "event_id": event, "source": "ios_replaykit_ocr",
           "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
           "reliability": "known", "confidence": .98, "capture_batch_id": event,
           "line_index": 0, "capture_comment_count": 1,
           "content": {"text": text, "text_position": {"x": .1, "y": .5, "width": .4, "height": .04}}}
    assert store.add_observation(sid, token, row, now)["_batch_ready"]


def reply(store, sid, token, epoch):
    candidate = store.fusion_candidate(sid, token, epoch)
    assert candidate and candidate["prompt_type"] == "comment_reply"
    assert store.save_fusion_candidate(sid, token, candidate, "谢谢分享。", epoch)["reason"] == "generated"
    store.finish_comment_attempt(sid, candidate["reply_to"]["comment_id"], epoch, True, True)


def test_expired_comment_can_support_fixed_lifetime_coaching(tmp_path):
    start = 1_790_000_000_000
    clock = [start]
    store = LiveStore(str(tmp_path / "expiry.sqlite3"), lambda: clock[0])
    try:
        s = store.create("")
        sid, token = s["session_id"], s["session_token"]
        add_comment(store, sid, token, start, "first", "阿青：这首歌很好听")
        reply(store, sid, token, 1)
        assert store.fusion_candidate(sid, token, 1) is None
        clock[0] += 20_001
        add_comment(store, sid, token, clock[0], "second", "小林：晚上好")
        old_batch = store.comment_batches[sid][("ios_replaykit_ocr", "first")]
        assert "records" not in old_batch and "reply_rows" not in old_batch
        reply(store, sid, token, 2)
        candidate = store.fusion_candidate(sid, token, 2)
        assert candidate and candidate["prompt_type"] == "operation_notice"
        assert store.save_fusion_candidate(sid, token, candidate, candidate["suggestion"], 2)["reason"] == "generated"
        event = store.events(sid, token)["events"][-1]
        assert event["expires_at_ms"] == start + 30_000
        assert event["sources"][0]["observed_at_ms"] == start
        assert "这首歌很好听" not in str(event) and "阿青" not in str(event)
    finally:
        store.close()


def test_dense_comment_stream_yields_training_after_two_replies(tmp_path):
    now = 1_790_000_000_000
    store = LiveStore(str(tmp_path / "fair.sqlite3"), lambda: now)
    try:
        s = store.create("")
        sid, token = s["session_id"], s["session_token"]
        add_comment(store, sid, token, now, "first", "阿青：这首歌很好听")
        reply(store, sid, token, 1)
        for epoch, text in [(2, "小林：晚上好"), (3, "小明：今天心情不错")]:
            add_comment(store, sid, token, now, f"e{epoch}", text)
            reply(store, sid, token, epoch)
        add_comment(store, sid, token, now, "e4", "小红：我也喜欢音乐")
        candidate = store.fusion_candidate(sid, token, 4)
        assert candidate and candidate["prompt_type"] == "operation_notice"
        assert store.save_fusion_candidate(sid, token, candidate, candidate["suggestion"], 4)["reason"] == "generated"
        following = store.fusion_candidate(sid, token, 4)
        assert following and following["prompt_type"] == "comment_reply"
        assert following["model_text"] == "我也喜欢音乐"
    finally:
        store.close()


def test_expiry_stop_and_session_isolation(tmp_path):
    start = 1_790_000_000_000
    clock = [start]
    store = LiveStore(str(tmp_path / "sessions.sqlite3"), lambda: clock[0])
    try:
        a, b = store.create(""), store.create("")
        for session, event in [(a, "a"), (b, "b")]:
            sid, token = session["session_id"], session["session_token"]
            add_comment(store, sid, token, start, event, "阿青：这首歌很好听")
            reply(store, sid, token, 1)
        sid_a, token_a = a["session_id"], a["session_token"]
        sid_b, token_b = b["session_id"], b["session_token"]
        store.stop(sid_a, token_a)
        assert sid_a not in store.coaching_opportunities
        assert sid_b in store.coaching_opportunities
        clock[0] += 30_000
        assert store.fusion_candidate(sid_b, token_b, 2) is None
        assert sid_b not in store.coaching_opportunities
    finally:
        store.close()


@pytest.mark.parametrize("age,eligible", [(20_001, True), (27_000, True), (27_001, False), (29_000, False)])
def test_training_minimum_remaining_at_selection(tmp_path, age, eligible):
    start = 1_790_000_000_000
    clock = [start]
    store = LiveStore(str(tmp_path / f"minimum-{age}.sqlite3"), lambda: clock[0])
    try:
        data = store.create("")
        sid, token = data["session_id"], data["session_token"]
        add_comment(store, sid, token, start, "first", "阿青：这首歌很好听")
        reply(store, sid, token, 1)
        clock[0] += age
        candidate = store.fusion_candidate(sid, token, 2)
        assert bool(candidate) is eligible
        if candidate:
            assert candidate["prompt_type"] == "operation_notice"
            assert store.save_fusion_candidate(sid, token, candidate, candidate["suggestion"], 2)["reason"] == "generated"
            assert store.events(sid, token)["events"][-1]["expires_at_ms"] == start + 30_000
        else:
            assert sid not in store.coaching_opportunities
            assert len(store.events(sid, token)["events"]) == 1
    finally:
        store.close()


def test_training_below_minimum_after_selection_is_not_saved_or_retried(tmp_path):
    start = 1_790_000_000_000
    clock = [start]
    store = LiveStore(str(tmp_path / "late-save.sqlite3"), lambda: clock[0])
    try:
        data = store.create("")
        sid, token = data["session_id"], data["session_token"]
        add_comment(store, sid, token, start, "first", "阿青：这首歌很好听")
        reply(store, sid, token, 1)
        clock[0] += 27_000
        candidate = store.fusion_candidate(sid, token, 2)
        assert candidate and candidate["prompt_type"] == "operation_notice"
        clock[0] += 1
        assert store.save_fusion_candidate(sid, token, candidate, candidate["suggestion"], 2)["reason"] == "insufficient_reading_time"
        assert len(store.events(sid, token)["events"]) == 1
        assert store.fusion_candidate(sid, token, 2) is None
        assert sid not in store.coaching_opportunities
        add_comment(store, sid, token, clock[0], "new", "小林：晚上好")
        reply(store, sid, token, 3)
        later = store.fusion_candidate(sid, token, 4)
        assert later and later["prompt_type"] == "operation_notice"
        assert later["training_trigger_at_ms"] == clock[0]
    finally:
        store.close()


def test_confirmed_quality_candidate_precedes_due_training(tmp_path):
    now = 1_790_000_000_000
    store = LiveStore(str(tmp_path / "priority.sqlite3"), lambda: now)
    try:
        s = store.create("")
        sid, token = s["session_id"], s["session_token"]
        for epoch, text in [(1, "阿青：这首歌很好听"), (2, "小林：晚上好"), (3, "小明：今天心情不错")]:
            add_comment(store, sid, token, now, f"e{epoch}", text)
            reply(store, sid, token, epoch)
        signal = {"kind": "quality", "event_id": "quality", "source": "ios_replaykit_ocr",
                  "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
                  "reliability": "known", "content": {"signal": "video_input_unclear"}}
        assert store.add_observation(sid, token, signal, now)["reason"] == "windowed"
        candidate = store.fusion_candidate(sid, token, 4)
        assert candidate and candidate["priority"] == 80 and candidate["topic"].startswith("quality:")
    finally:
        store.close()


def test_higher_priority_history_does_not_block_ordinary_reply(tmp_path):
    start = 1_790_000_000_000
    clock = [start]
    store = LiveStore(str(tmp_path / "priority-history.sqlite3"), lambda: clock[0])
    try:
        s = store.create("")
        sid, token = s["session_id"], s["session_token"]
        for epoch, text in [(1, "阿青：这首歌很好听"), (2, "小林：晚上好"), (3, "小明：今天心情不错")]:
            add_comment(store, sid, token, start, f"e{epoch}", text)
            reply(store, sid, token, epoch)
        signal = {"kind": "quality", "event_id": "quality", "source": "ios_replaykit_ocr",
                  "observed_at_ms": start, "availability": "available", "coverage": "ocr_visible_sample",
                  "reliability": "known", "content": {"signal": "video_input_unclear"}}
        assert store.add_observation(sid, token, signal, start)["reason"] == "windowed"
        quality = store.fusion_candidate(sid, token, 4)
        assert quality["priority"] == 80
        assert store.save_fusion_candidate(sid, token, quality, quality["suggestion"], 4)["reason"] == "generated"
        clock[0] += 10_001
        add_comment(store, sid, token, clock[0], "fresh", "小红：我也喜欢音乐")
        candidate = store.fusion_candidate(sid, token, 5)
        assert candidate and candidate["prompt_type"] == "comment_reply"
        assert candidate["model_text"] == "我也喜欢音乐"
    finally:
        store.close()


def test_http_training_after_original_comment_payload_expiry(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("隔离回归不允许网络访问")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    start = 1_790_000_000_000
    clock = [start]
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="controlled",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake", dedup_ttl_seconds=60,
                   max_sentence_chars=2000, model_timeout_seconds=1, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "http.sqlite3"))
    class Model:
        async def reply_to_comment(self, text, *, nickname, priority_eligible):
            return "谢谢分享。"
        async def aclose(self):
            pass
    app = create_app(cfg, live_clock=lambda: clock[0])
    with TestClient(app) as client:
        client.portal.call(app.state.model.aclose)
        app.state.model = Model()
        data = client.post("/v1/live/sessions", json={}).json()
        sid, token = data["session_id"], data["session_token"]
        url, headers = "/v1/live/sessions/" + sid, {"X-Live-Session-Token": token}
        async def idle():
            async with asyncio.timeout(5):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(.005)
        def send(event, text):
            row = {"kind": "comment", "event_id": event, "source": "ios_replaykit_ocr",
                   "observed_at_ms": clock[0], "availability": "available", "coverage": "ocr_visible_sample",
                   "reliability": "known", "confidence": .98, "capture_batch_id": event,
                   "line_index": 0, "capture_comment_count": 1, "content": {"text": text,
                       "text_position": {"x": .1, "y": .5, "width": .4, "height": .04}}}
            assert client.post(url + "/observations", headers=headers, json=row).status_code == 200
            client.portal.call(idle)
        send("first", "阿青：这首歌很好听")
        assert len(client.get(url + "/prompts", headers=headers).json()["prompts"]) == 1
        clock[0] += 20_001
        send("second", "小林：晚上好")
        events = client.get(url + "/events", headers=headers).json()["events"]
        notices = [event for event in events if event.get("prompt_type") == "operation_notice"]
        assert len(notices) == 1 and notices[0]["expires_at_ms"] == start + 30_000
        assert notices[0]["sources"][0]["observed_at_ms"] == start
        assert "这首歌很好听" not in str(notices[0]) and "阿青" not in str(notices[0])
        prompts = client.get(url + "/prompts", headers=headers).json()["prompts"]
        assert any(event["prompt_id"] == notices[0]["prompt_id"] for event in prompts)


def test_http_dense_batch_resumes_comments_after_training(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("隔离回归不允许网络访问")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    now = 1_790_000_000_000
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="controlled",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake", dedup_ttl_seconds=60,
                   max_sentence_chars=2000, model_timeout_seconds=1, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "dense-http.sqlite3"))
    calls = []
    class Model:
        async def reply_to_comment(self, text, *, nickname, priority_eligible):
            calls.append(text)
            return "谢谢分享。"
        async def aclose(self):
            pass
    app = create_app(cfg, live_clock=lambda: now)
    with TestClient(app) as client:
        client.portal.call(app.state.model.aclose)
        app.state.model = Model()
        data = client.post("/v1/live/sessions", json={}).json()
        url = "/v1/live/sessions/" + data["session_id"]
        headers = {"X-Live-Session-Token": data["session_token"]}
        async def idle():
            async with asyncio.timeout(5):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(.005)
        def send(event, batch, line, count, text, y):
            row = {"kind": "comment", "event_id": event, "source": "ios_replaykit_ocr",
                   "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
                   "reliability": "known", "confidence": .98, "capture_batch_id": batch,
                   "line_index": line, "capture_comment_count": count, "content": {"text": text,
                       "text_position": {"x": .1, "y": y, "width": .4, "height": .04}}}
            assert client.post(url + "/observations", headers=headers, json=row).status_code == 200
        send("first", "first", 0, 1, "阿青：这首歌很好听", .1)
        client.portal.call(idle)
        for index, text in enumerate(("小林：晚上好", "小明：今天心情不错", "小红：我也喜欢音乐")):
            send(f"e{index}", "dense", index, 3, text, .3 + index * .15)
        client.portal.call(idle)
        events = client.get(url + "/events", headers=headers).json()["events"]
        assert [event["prompt_type"] for event in events] == [
            "comment_reply", "comment_reply", "comment_reply", "operation_notice", "comment_reply"]
        assert calls == ["这首歌很好听", "晚上好", "今天心情不错", "我也喜欢音乐"]
