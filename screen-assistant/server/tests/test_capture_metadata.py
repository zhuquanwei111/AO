import sqlite3

import pytest
from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings


@pytest.fixture
def live_client(tmp_path):
    path = tmp_path / "live.sqlite3"
    settings = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="",
                        tianyi_base_url="http://model.invalid", tianyi_model="unused",
                        dedup_ttl_seconds=60, max_sentence_chars=2000,
                        model_timeout_seconds=1, model_connect_timeout_seconds=1,
                        live_db_path=str(path))
    with TestClient(create_app(settings)) as client:
        session = client.post("/v1/live/sessions", json={}).json()
        url = f"/v1/live/sessions/{session['session_id']}/observations"
        headers = {"X-Live-Session-Token": session["session_token"]}
        yield client, url, headers, path


def comment(event_id="legacy-1"):
    return {"kind": "comment", "event_id": event_id, "source": "fixture:ocr_comment",
            "availability": "available", "coverage": "structured_sample",
            "reliability": "known", "content": {"text": "hello"}}


@pytest.mark.parametrize("metadata", [
    {"capture_batch_id": "batch"}, {"line_index": 0}, {"capture_comment_count": 1},
    {"capture_batch_id": None, "line_index": None, "capture_comment_count": None},
])
def test_legacy_comments_reject_batch_metadata(live_client, metadata):
    client, url, headers, path = live_client
    legacy_url = url.removesuffix("/observations") + "/comments"
    body = {"event_id": "legacy-comment", "input_mode": "live_observation",
            "source": "ios_replaykit_ocr", "coverage": "ocr_visible_sample",
            "reliability": "known", "text": "hello"}
    assert client.post(legacy_url, headers=headers, json={**body, **metadata}).status_code == 422
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM live_observations").fetchone()[0] == 0
    assert client.post(legacy_url, headers=headers, json=body).status_code == 200


def test_legacy_fingerprint_survives_upgrade_and_explicit_null(live_client):
    client, url, headers, path = live_client
    body = comment()
    assert client.post(url, headers=headers, json=body).status_code == 200
    # e423 的 Pydantic model_dump 对该 DTO 所算出的实际 SHA-256。
    legacy_hash = "a61e91df842e498d33ad9e86bab7e05aa01578b36e5872e44f0941f0e208e6d7"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT fingerprint FROM live_observations").fetchone()[0] == legacy_hash
        db.execute("UPDATE live_observations SET fingerprint=?", (legacy_hash,))
    for payload in (body, {**body, "capture_batch_id": None, "line_index": None}):
        response = client.post(url, headers=headers, json=payload)
        assert response.status_code == 200
        assert response.json()["reason"] == "duplicate_event"


def test_metadata_retry_and_conflict(live_client):
    client, url, headers, _ = live_client
    body = {**comment("metadata"), "capture_batch_id": "batch-1", "line_index": 0}
    first = client.post(url, headers=headers, json=body)
    assert first.status_code == 200 and first.json()["reason"] == "scheduled"
    again = client.post(url, headers=headers, json=body)
    assert again.status_code == 200 and again.json()["reason"] == "duplicate_event"
    for changed in ({**body, "line_index": 1}, {**body, "capture_batch_id": "batch-2"}, comment("metadata")):
        assert client.post(url, headers=headers, json=changed).status_code == 409


@pytest.mark.parametrize("metadata", [
    {"capture_batch_id": ""}, {"capture_batch_id": "   "}, {"capture_batch_id": "x" * 129},
    {"line_index": 0}, {"capture_batch_id": "x", "line_index": -1},
    {"capture_batch_id": "x", "line_index": 65536},
    {"capture_batch_id": "x", "line_index": True},
    {"capture_batch_id": "x", "line_index": 1.0},
    {"capture_batch_id": "x", "line_index": "1"},
])
def test_invalid_comment_metadata(live_client, metadata):
    client, url, headers, _ = live_client
    assert client.post(url, headers=headers, json={**comment(), **metadata}).status_code == 422


def test_online_count_and_upper_bounds(live_client):
    client, url, headers, _ = live_client
    body = {**comment("upper"), "capture_batch_id": "x" * 128, "line_index": 65535}
    assert client.post(url, headers=headers, json=body).status_code == 200
    count = {"kind": "online_count", "event_id": "count", "source": "fixture:confirmed-count",
             "availability": "available", "coverage": "dedicated_region", "reliability": "known",
             "content": {"raw_text": "20", "field_semantics_confirmed": True},
             "capture_batch_id": "batch-1"}
    assert client.post(url, headers=headers, json=count).status_code == 200
    assert client.post(url, headers=headers, json=count).json()["reason"] == "duplicate_event"
    assert client.post(url, headers=headers, json={**count, "capture_batch_id": "batch-2"}).status_code == 409
    assert client.post(url, headers=headers, json={**count, "line_index": 0}).status_code == 422
