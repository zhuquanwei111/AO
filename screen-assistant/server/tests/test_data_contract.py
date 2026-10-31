from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings


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
