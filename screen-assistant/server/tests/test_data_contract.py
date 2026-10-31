from fastapi.testclient import TestClient
import pytest

from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings
from screen_assistant_server.live_store import LiveStore
from fastapi import HTTPException


def test_batch_capability_and_ios_distinct_rows_preserve_legacy_dedup(tmp_path):
    now = 1_790_000_000_000
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="",
                   tianyi_base_url="http://model.invalid", tianyi_model="unused",
                   dedup_ttl_seconds=60, max_sentence_chars=2000,
                   model_timeout_seconds=1, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "live.sqlite3"))
    with TestClient(create_app(cfg, live_clock=lambda: now)) as client:
        session = client.post("/v1/live/sessions", json={}).json()
        assert session["capabilities"] == {"observation_batch_metadata": True,
                                           "typed_prompts": False}
        url = f"/v1/live/sessions/{session['session_id']}/observations"
        headers = {"X-Live-Session-Token": session["session_token"]}
        body = {"kind": "comment", "source": "ios_replaykit_ocr",
                "observed_at_ms": now, "availability": "available",
                "coverage": "ocr_visible_sample", "reliability": "known",
                "content": {"text": "hello", "text_position": {
                    "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1}}}
        old1 = {**body, "event_id": "old-1"}
        old2 = {**body, "event_id": "old-2"}
        assert client.post(url, headers=headers, json=old1).json()["reason"] == "scheduled"
        assert client.post(url, headers=headers, json={**old1, "capture_comment_count": None}).json()["reason"] == "duplicate_event"
        assert client.post(url, headers=headers, json={**old1, "capture_batch_id": "new-batch",
                                                       "line_index": 0, "capture_comment_count": 1}).status_code == 409
        assert client.post(url, headers=headers, json=old2).json()["reason"] == "duplicate_message"
        row1 = {**body, "event_id": "new-1", "capture_batch_id": "batch-1", "line_index": 0}
        row2 = {**body, "event_id": "new-2", "capture_batch_id": "batch-1", "line_index": 1}
        assert client.post(url, headers=headers, json=row1).json()["reason"] == "scheduled"
        assert client.post(url, headers=headers, json=row2).json()["reason"] == "scheduled"
        assert client.post(url, headers=headers, json=row1).json()["reason"] == "duplicate_event"
        assert client.post(url, headers=headers, json={**row1, "line_index": 2}).status_code == 409
        identified = {**row1, "event_id": "identified-1", "content": {
            **body["content"], "platform_message_id": "platform-1"}}
        assert client.post(url, headers=headers, json=identified).json()["reason"] == "scheduled"
        duplicate = {**identified, "event_id": "identified-2", "line_index": 2}
        assert client.post(url, headers=headers, json=duplicate).json()["reason"] == "duplicate_message"


def test_typed_prompt_keeps_existing_event_fields_and_source(tmp_path):
    now = 1_790_000_000_000
    store = LiveStore(str(tmp_path / "typed.sqlite3"), lambda: now)
    try:
        session = store.create("")
        sid, token = session["session_id"], session["session_token"]
        row = {"event_id": "event-1", "kind": "comment", "source": "ios_replaykit_ocr",
               "coverage": "ocr_visible_sample", "reliability": "known", "confidence": 0.9,
               "observed_at_ms": now, "effective_at_ms": now, "received_at_ms": now,
               "content": {"text": "小明：bless you", "text_position": None}}
        candidate = {"topic": "comment:1", "priority": 25, "phenomenon": "公屏评论",
                     "observations": [row], "prompt_type": "comment_reply",
                     "reply_to": {"comment_id": "c1", "nickname": "小明", "text": "bless you"}}
        result = store.save_fusion_candidate(sid, token, candidate, "谢谢你的祝福。")
        assert result["reason"] == "generated"
        event = store.prompts(sid, token, "")["prompts"][0]
        assert event["prompt_id"] == result["prompt_id"]
        assert event["prompt_type"] == "comment_reply"
        assert event["reply_to"] == candidate["reply_to"]
        assert event["display_text"] == event["suggestion"] == "谢谢你的祝福。"
        assert event["sources"][0]["source"] == "ios_replaykit_ocr"
        assert event["expires_at_ms"] == now + 15_000
        notice = {**candidate, "topic": "notice:1", "prompt_type": "operation_notice"}
        store.save_fusion_candidate(sid, token, notice, "可以留意评论区。")
        events = store.prompts(sid, token, "")["prompts"]
        assert events[1]["prompt_type"] == "operation_notice"
        assert "reply_to" not in events[1]
    finally:
        store.close()


@pytest.mark.parametrize("bad", [0, 129, True, 1.0, -1])
def test_capture_count_rejects_invalid_values(bad):
    from pydantic import TypeAdapter, ValidationError
    from screen_assistant_server.live_fusion import Observation
    body = {"kind": "comment", "event_id": "e", "source": "ios_replaykit_ocr",
            "observed_at_ms": 1000, "availability": "available", "coverage": "ocr_visible_sample",
            "reliability": "known", "content": {"text": "你好"}, "capture_batch_id": "b",
            "line_index": 3, "capture_comment_count": bad}
    with pytest.raises(ValidationError):
        TypeAdapter(Observation).validate_python(body)


def test_capture_count_requires_batch_and_index_and_online_rejects_count():
    from pydantic import TypeAdapter, ValidationError
    from screen_assistant_server.live_fusion import Observation
    base = {"kind": "comment", "event_id": "e", "source": "ios_replaykit_ocr",
            "observed_at_ms": 1000, "availability": "available", "coverage": "ocr_visible_sample",
            "reliability": "known", "content": {"text": "你好"}, "capture_comment_count": 1}
    adapter = TypeAdapter(Observation)
    for body in (base, {**base, "capture_batch_id": "b"},
                 {**base, "line_index": 0},
                 {**base, "kind": "online_count", "source": "ios_replaykit_online_count_roi",
                  "content": {"raw_text": "1"}, "capture_batch_id": "b"}):
        with pytest.raises(ValidationError):
            adapter.validate_python(body)
    valid = adapter.validate_python({**base, "capture_batch_id": "b", "line_index": 7})
    assert valid.capture_comment_count == 1


def test_batch_waits_for_distinct_indices_and_counts_unreliable_arrivals(tmp_path):
    now = 1_790_000_000_000
    store = LiveStore(str(tmp_path / "batch.sqlite3"), lambda: now)
    try:
        session = store.create("")
        sid, token = session["session_id"], session["session_token"]
        first = {"kind": "comment", "event_id": "e1", "source": "ios_replaykit_ocr",
                 "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
                 "reliability": "known", "content": {"text": "主播你是哪里人？"},
                 "capture_batch_id": "batch", "line_index": 1, "capture_comment_count": 2}
        result = store.add_observation(sid, token, first, now)
        assert result["reason"] == "batch_incomplete"
        assert not store.fusion_windows.get(sid)
        assert store.add_observation(sid, token, first, now)["reason"] == "duplicate_event"
        assert not store.fusion_windows.get(sid)
        for bad in ({**first, "event_id": "other"},
                    {**first, "event_id": "e2", "line_index": 5, "capture_comment_count": 3}):
            with pytest.raises(HTTPException) as error:
                store.add_observation(sid, token, bad, now + 1000)
            assert error.value.status_code == 409
        last = {**first, "event_id": "e2", "line_index": 5, "availability": "unreliable"}
        result = store.add_observation(sid, token, last, now + 1000)
        assert result["reason"] == "source_unreliable"
        assert result["_batch_ready"] is True
        assert [row["event_id"] for row in store.fusion_windows[sid]] == ["e1"]
    finally:
        store.close()


def test_batch_expiry_and_session_isolation(tmp_path):
    now = 1_790_000_000_000
    clock = [now]
    store = LiveStore(str(tmp_path / "batch-expiry.sqlite3"), lambda: clock[0])
    try:
        a, b, c = store.create(""), store.create(""), store.create("")
        first = {"kind": "comment", "event_id": "e1", "source": "ios_replaykit_ocr",
                 "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
                 "reliability": "known", "content": {"text": "你好"},
                 "capture_batch_id": "shared", "line_index": 1, "capture_comment_count": 2}
        for session in (a, b):
            result = store.add_observation(session["session_id"], session["session_token"], first, now)
            assert result["reason"] == "batch_incomplete"
        fixture = {**first, "event_id": "fixture-e1", "source": "fixture:ocr_comment"}
        assert store.add_observation(a["session_id"], a["session_token"], fixture, now)["reason"] == "batch_incomplete"
        separate = {**first, "event_id": "other-e1", "capture_batch_id": "other"}
        assert store.add_observation(a["session_id"], a["session_token"], separate, now)["reason"] == "batch_incomplete"
        assert store.add_observation(c["session_id"], c["session_token"], first, now)["reason"] == "batch_incomplete"
        store.stop(c["session_id"], c["session_token"])
        assert c["session_id"] not in store.comment_batches
        assert store.events(c["session_id"], c["session_token"])["events"] == []
        second = {**first, "event_id": "e2", "line_index": 9}
        ready = store.add_observation(b["session_id"], b["session_token"], second, now + 1000)
        assert ready["_batch_ready"] is True
        assert not store.fusion_windows.get(a["session_id"])
        assert len(store.comment_batches[a["session_id"]]) == 3
        clock[0] += 10_001
        late = store.add_observation(a["session_id"], a["session_token"], second, clock[0])
        assert late["reason"] == "batch_expired"
        assert not store.fusion_windows.get(a["session_id"])
        store.stop(a["session_id"], a["session_token"])
        assert a["session_id"] not in store.comment_batches
        with pytest.raises(HTTPException) as error:
            store.add_observation(a["session_id"], a["session_token"],
                                  {**first, "event_id": "e3"}, clock[0])
        assert error.value.status_code == 409
    finally:
        store.close()


def test_restart_does_not_complete_old_partial_batch_but_new_batch_works(tmp_path):
    now = 1_790_000_000_000
    path = str(tmp_path / "batch-restart.sqlite3")
    store = LiveStore(path, lambda: now)
    session = store.create("")
    sid, token = session["session_id"], session["session_token"]
    first = {"kind": "comment", "event_id": "e1", "source": "ios_replaykit_ocr",
             "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
             "reliability": "known", "content": {"text": "主播你是哪里人？"},
             "capture_batch_id": "old-uuid", "line_index": 0, "capture_comment_count": 2}
    assert store.add_observation(sid, token, first, now)["reason"] == "batch_incomplete"
    store.close()
    store = LiveStore(path, lambda: now + 1000)
    try:
        assert store.add_observation(sid, token, first, now + 1000)["reason"] == "duplicate_event"
        last = {**first, "event_id": "e2", "line_index": 4}
        assert store.add_observation(sid, token, last, now + 1000)["reason"] == "batch_incomplete"
        assert not store.fusion_windows.get(sid)
        assert store.events(sid, token)["events"] == []
        fresh = {**first, "event_id": "e3", "capture_batch_id": "new-uuid",
                 "capture_comment_count": 1, "observed_at_ms": now + 1000}
        assert store.add_observation(sid, token, fresh, now + 1000)["_batch_ready"] is True
        assert [row["event_id"] for row in store.fusion_windows[sid]] == ["e3"]
    finally:
        store.close()
