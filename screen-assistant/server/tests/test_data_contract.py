from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings
from screen_assistant_server.live_store import LiveStore


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
