"""M2 契约：显式测试观察、临时 SQLite；不调用模型或 Redis。"""
from __future__ import annotations

import sqlite3

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
