"""验证可靠评论可进入既有运营建议链，并保留原回复与去重。"""
import asyncio
import sqlite3

from fastapi.testclient import TestClient
import pytest

from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings
from screen_assistant_server.live_store import COACHING_TOPICS, LiveStore, comment_record_priority


def test_priority_uses_only_usable_level_with_matching_evidence():
    record = {"source_event_ids": ["e"], "consumption_level": 30, "fan_level": None,
              "field_status": {"consumption_level": "usable"},
              "field_sources": {"consumption_level": ["e"]}}
    assert comment_record_priority(record)
    assert not comment_record_priority({**record, "field_status": {"consumption_level": "candidate"}})
    assert not comment_record_priority({**record, "field_sources": {"consumption_level": ["other"]}})
    assert not comment_record_priority({})


def test_usable_comment_can_produce_both_types_without_replaying_same_frame(tmp_path):
    now = 1_790_000_000_000
    clock = [now]
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="synthetic",
                   tianyi_base_url="http://model.invalid", tianyi_model="stub",
                   dedup_ttl_seconds=60, max_sentence_chars=2000,
                   model_timeout_seconds=2, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "coaching.sqlite3"))
    calls = []

    class ControlledModel:
        async def reply_to_comment(self, text, *, nickname, priority_eligible):
            calls.append(("reply", text))
            return "谢谢喜欢。"

        async def suggest_live(self, text, description, source, observed_at_ms, *, context):
            raise AssertionError("条件式运营模板不应再次调用模型")

        async def aclose(self):
            pass

    app = create_app(cfg, live_clock=lambda: clock[0])
    with TestClient(app) as client:
        client.portal.call(app.state.model.aclose)
        app.state.model = ControlledModel()
        session = client.post("/v1/live/sessions", json={}).json()
        url = f"/v1/live/sessions/{session['session_id']}"
        headers = {"X-Live-Session-Token": session["session_token"]}

        def submit(event, batch, text="阿青：这首歌很好听", reason="scheduled"):
            body = {"kind": "comment", "event_id": event, "source": "ios_replaykit_ocr",
                    "observed_at_ms": clock[0], "availability": "available",
                    "coverage": "ocr_visible_sample", "reliability": "known", "confidence": .98,
                    "capture_batch_id": batch, "line_index": 0, "capture_comment_count": 1,
                    "content": {"text": text, "text_position":
                                {"x": .1, "y": .5, "width": .4, "height": .04}}}
            result = client.post(url + "/observations", headers=headers, json=body)
            assert result.status_code == 200 and result.json()["reason"] == reason

        async def wait_idle():
            async with asyncio.timeout(5):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(.005)

        submit("first", "batch-1")
        client.portal.call(wait_idle)
        events = client.get(url + "/prompts", headers=headers).json()["prompts"]
        assert [event["prompt_type"] for event in events] == ["comment_reply"]
        assert calls == [("reply", "这首歌很好听")]
        submit("first", "batch-1", reason="duplicate_event")
        client.portal.call(wait_idle)
        assert [event["prompt_type"] for event in client.get(url + "/events", headers=headers).json()["events"]] == ["comment_reply"]
        clock[0] += 1000
        submit("second", "batch-2")
        client.portal.call(wait_idle)
        events = client.get(url + "/events", headers=headers).json()["events"]
        assert [event["prompt_type"] for event in events] == ["comment_reply", "operation_notice"]
        assert events[1]["expires_at_ms"] == now + 30_000
        assert events[1]["phenomenon"] == "基于可用评论的条件式话术训练"
        assert events[1]["display_text"] and events[1]["display_text"] == events[1]["suggestion"]
        assert len(calls) == 1
        clock[0] += 1000
        submit("third", "batch-3", "小林：晚上好")
        client.portal.call(wait_idle)
        assert len(client.get(url + "/events", headers=headers).json()["events"]) == 3
        assert calls[-1] == ("reply", "晚上好")
        clock[0] = now + 32_000
        submit("fourth", "batch-4", "阿青：这首歌的旋律很好")
        client.portal.call(wait_idle)
        submit("fourth-trigger", "batch-4-trigger", "阿青：这首歌的旋律很好")
        client.portal.call(wait_idle)
        events = client.get(url + "/events", headers=headers).json()["events"]
        assert [event["prompt_type"] for event in events] == [
            "comment_reply", "operation_notice", "comment_reply", "comment_reply", "operation_notice"]
        assert calls[-1] == ("reply", "这首歌的旋律很好") and len(calls) == 3
        for index, offset in enumerate((62_000, 92_000), start=5):
            clock[0] = now + offset
            submit(f"later-{index}", f"batch-{index}", f"阿青：这首歌的旋律不错{index}")
            client.portal.call(wait_idle)
            submit(f"later-trigger-{index}", f"batch-trigger-{index}", f"阿青：这首歌的旋律不错{index}")
            client.portal.call(wait_idle)
        events = client.get(url + "/events", headers=headers).json()["events"]
        notices = [event for event in events if event["prompt_type"] == "operation_notice"]
        assert len(notices) == 4
        assert len({event["display_text"] for event in notices[:3]}) == 3
        assert notices[0]["display_text"] == notices[3]["display_text"]
        assert [event["expires_at_ms"] for event in notices] == [now + n for n in (30_000, 62_000, 92_000, 122_000)]


def test_incomplete_or_quality_gap_does_not_create_coaching(tmp_path):
    now = 1_790_000_000_000
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="",
                   tianyi_base_url="http://model.invalid", tianyi_model="unused",
                   dedup_ttl_seconds=60, max_sentence_chars=2000,
                   model_timeout_seconds=1, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "gap.sqlite3"))
    with TestClient(create_app(cfg, live_clock=lambda: now)) as client:
        session = client.post("/v1/live/sessions", json={}).json()
        url = f"/v1/live/sessions/{session['session_id']}"
        headers = {"X-Live-Session-Token": session["session_token"]}
        row = {"kind": "comment", "event_id": "first", "source": "ios_replaykit_ocr",
               "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
               "reliability": "known", "confidence": .98, "capture_batch_id": "batch",
               "line_index": 0, "capture_comment_count": 2,
               "content": {"text": "阿青：这首歌很好听", "text_position":
                           {"x": .1, "y": .5, "width": .4, "height": .04}}}
        first = client.post(url + "/observations", headers=headers, json=row)
        assert first.status_code == 200 and first.json()["reason"] == "batch_incomplete"
        assert client.get(url + "/events", headers=headers).json()["events"] == []
        gap = {**row, "event_id": "gap", "line_index": 1, "reliability": "unknown",
               "content": {"text": "不可靠片段", "text_position": None}}
        last = client.post(url + "/observations", headers=headers, json=gap)
        assert last.status_code == 200 and last.json()["reason"] == "unreliable"

        async def wait_idle():
            async with asyncio.timeout(3):
                while client.app.state.live.fusion_active or client.app.state.live.fusion_queue:
                    await asyncio.sleep(.005)

        client.portal.call(wait_idle)
        assert client.get(url + "/events", headers=headers).json()["events"] == []


def test_observation_during_model_call_does_not_unlock_training(tmp_path):
    now = 1_790_000_000_000
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="controlled",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake",
                   dedup_ttl_seconds=60, max_sentence_chars=2000,
                   model_timeout_seconds=5, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "inflight.sqlite3"))

    class Model:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def reply_to_comment(self, text, *, nickname, priority_eligible):
            self.started.set()
            await self.release.wait()
            return "我们聊聊这个话题。"

        async def aclose(self):
            pass

    app = create_app(cfg, live_clock=lambda: now)
    with TestClient(app) as client:
        client.portal.call(app.state.model.aclose)
        model = Model()
        app.state.model = model
        data = client.post("/v1/live/sessions", json={}).json()
        url = "/v1/live/sessions/" + data["session_id"]
        headers = {"X-Live-Session-Token": data["session_token"]}

        def send(event):
            row = {"kind": "comment", "event_id": event, "source": "ios_replaykit_ocr",
                   "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
                   "reliability": "known", "confidence": .98, "capture_batch_id": event,
                   "line_index": 0, "capture_comment_count": 1, "content": {
                       "text": "阿青：这首歌很好听", "text_position":
                       {"x": .1, "y": .5, "width": .4, "height": .04}}}
            assert client.post(url + "/observations", headers=headers, json=row).status_code == 200

        async def wait_idle():
            async with asyncio.timeout(5):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(.005)

        send("first")
        client.portal.call(model.started.wait)
        send("during")
        client.portal.call(model.release.set)
        client.portal.call(wait_idle)
        assert [e["prompt_type"] for e in client.get(url + "/events", headers=headers).json()["events"]] == ["comment_reply"]
        send("after")
        client.portal.call(wait_idle)
        assert [e["prompt_type"] for e in client.get(url + "/events", headers=headers).json()["events"]] == ["comment_reply", "operation_notice"]


@pytest.mark.parametrize("sources", [
    ("ios_replaykit_ocr", "fixture:ocr_comment"),
    ("fixture:ocr_comment", "ios_replaykit_ocr"),
])
def test_training_considers_replied_comment_from_each_source(tmp_path, sources):
    now = 1_790_000_000_000
    store = LiveStore(str(tmp_path / "sources.sqlite3"), lambda: now)
    try:
        session = store.create("")
        sid, token = session["session_id"], session["session_token"]

        def add(source, event, text):
            row = {"kind": "comment", "event_id": event, "source": source,
                   "observed_at_ms": now, "availability": "available",
                   "coverage": "ocr_visible_sample", "reliability": "known", "confidence": .98,
                   "capture_batch_id": event, "line_index": 0, "capture_comment_count": 1,
                   "content": {"text": text, "text_position":
                               {"x": .1, "y": .5, "width": .4, "height": .04}}}
            assert store.add_observation(sid, token, row, now)["_batch_ready"]

        add(sources[0], "first", "阿青：这首歌很好听")
        first = store.fusion_candidate(sid, token)
        assert first["prompt_type"] == "comment_reply"
        assert store.save_fusion_candidate(sid, token, first, "谢谢喜欢。 ")["reason"] == "generated"
        store.finish_comment_attempt(sid, first["reply_to"]["comment_id"], 0, True, True)

        add(sources[1], "second", "小林：晚上好")
        second = store.fusion_candidate(sid, token)
        assert second["prompt_type"] == "comment_reply"
        store.finish_comment_attempt(sid, second["reply_to"]["comment_id"], 0, True, True)

        training = store.fusion_candidate(sid, token, trigger_epoch=1)
        assert training is not None and training["prompt_type"] == "operation_notice"
        assert training["observations"][0]["source"] == sources[0]
    finally:
        store.close()


def test_failed_training_save_does_not_consume_cooldown_or_extend_deadline(tmp_path):
    now = 1_790_000_000_000
    clock = [now]
    store = LiveStore(str(tmp_path / "save.sqlite3"), lambda: clock[0])
    try:
        session = store.create("")
        sid, token = session["session_id"], session["session_token"]
        row = {"kind": "comment", "event_id": "first", "source": "ios_replaykit_ocr",
               "availability": "available", "coverage": "ocr_visible_sample",
               "reliability": "known", "confidence": .9, "observed_at_ms": now,
               "capture_batch_id": "batch", "line_index": 0, "capture_comment_count": 1,
               "content": {"text": "阿青：这首歌很好听", "text_position":
                           {"x": .1, "y": .5, "width": .4, "height": .04}}}
        assert store.add_observation(sid, token, row, now)["_batch_ready"]
        reply = store.fusion_candidate(sid, token, 0)
        assert store.save_fusion_candidate(sid, token, reply, "谢谢喜欢。", 0)["reason"] == "generated"
        store.finish_comment_attempt(sid, reply["reply_to"]["comment_id"], 0, True, True)
        candidate = store.fusion_candidate(sid, token, 1)
        assert candidate and candidate["prompt_type"] == "operation_notice"
        store.db.executescript("CREATE TRIGGER reject_training BEFORE INSERT ON live_events "
                               "BEGIN SELECT RAISE(FAIL, 'controlled failure'); END;")
        with pytest.raises(sqlite3.IntegrityError):
            store.save_fusion_candidate(sid, token, candidate, candidate["suggestion"], 1)
        assert len(store.events(sid, token)["events"]) == 1
        assert store.fusion_candidate(sid, token, 1) is None
        store.db.execute("DROP TRIGGER reject_training")
        clock[0] = now + 5000
        candidate = store.fusion_candidate(sid, token, 2)
        result = store.save_fusion_candidate(sid, token, candidate, candidate["suggestion"], 2)
        assert result["reason"] == "generated"
        event = store.events(sid, token)["events"][-1]
        assert event["expires_at_ms"] == now + 30_000
        clock[0] = now + 30_000
        assert store.save_fusion_candidate(sid, token, candidate, candidate["suggestion"], 2)["reason"] == "candidate_expired"
        assert len(store.events(sid, token)["events"]) == 2
        store.stop(sid, token)
        assert store.fusion_candidate(sid, token) is None
    finally:
        store.close()
