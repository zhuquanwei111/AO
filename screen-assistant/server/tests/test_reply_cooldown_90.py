"""按成功回复限制重复提示，保持采集与其他评论独立。"""
import asyncio
import socket

from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings


def test_ninety_second_reply_window_keeps_other_batch_rows_and_sessions(tmp_path, monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("受控测试禁止网络连接")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    clock = [1_790_000_000_000]
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="controlled",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake",
                   dedup_ttl_seconds=60, max_sentence_chars=2000, model_timeout_seconds=1,
                   model_connect_timeout_seconds=1, live_db_path=str(tmp_path / "reply.sqlite3"))
    calls = []

    class Model:
        async def reply_to_comment(self, text, *, nickname, priority_eligible):
            calls.append((text, nickname))
            return "我们聊聊这个话题。"

        async def aclose(self):
            pass

    app = create_app(cfg, live_clock=lambda: clock[0])
    with TestClient(app) as client:
        client.portal.call(app.state.model.aclose)
        app.state.model = Model()

        def session():
            data = client.post("/v1/live/sessions", json={}).json()
            path = "/v1/live/sessions/" + data["session_id"]
            headers = {"X-Live-Session-Token": data["session_token"]}
            return data["session_id"], path, headers

        async def idle():
            async with asyncio.timeout(5):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(.005)

        def row(event, batch, line, count, text, y):
            return {"kind": "comment", "event_id": event, "source": "ios_replaykit_ocr",
                    "observed_at_ms": clock[0], "availability": "available",
                    "coverage": "ocr_visible_sample", "reliability": "known", "confidence": .98,
                    "capture_batch_id": batch, "line_index": line, "capture_comment_count": count,
                    "content": {"text": text, "text_position":
                                {"x": .1, "y": y, "width": .4, "height": .04}}}

        def send(path, headers, item):
            result = client.post(path + "/observations", headers=headers, json=item)
            assert result.status_code == 200
            client.portal.call(idle)
            return result.json()["reason"]

        sid, path, headers = session()
        original = row("first", "first-batch", 0, 1, "阿青：今晚聊什么？", .3)
        assert send(path, headers, original) == "scheduled"
        assert send(path, headers, original) == "duplicate_event"
        assert calls == [("今晚聊什么？", "阿青")]

        clock[0] += 20_000
        duplicate = row("repeat", "mixed", 1, 2, "阿青：今晚聊什么？", .3)
        other = row("other", "mixed", 5, 2, "小林：今天心情不错", .7)
        assert send(path, headers, duplicate) == "batch_incomplete"
        assert send(path, headers, duplicate) == "duplicate_event"
        assert send(path, headers, other) == "scheduled"
        assert calls == [("今晚聊什么？", "阿青"), ("今天心情不错", "小林")]
        assert app.state.live.store.db.execute(
            "SELECT COUNT(*) FROM live_observations WHERE session_id=?", (sid,)
        ).fetchone()[0] == 3
        replies = [event for event in client.get(path + "/events", headers=headers).json()["events"]
                   if event.get("prompt_type") == "comment_reply"]
        assert len(replies) == 2

        _, path2, headers2 = session()
        assert send(path2, headers2, row("independent", "new-session", 0, 1, "阿青：今晚聊什么？", .3)) == "scheduled"
        assert calls.count(("今晚聊什么？", "阿青")) == 2

        clock[0] += 70_000
        assert send(path, headers, row("later", "later-batch", 0, 1, "阿青：今晚聊什么？", .3)) == "scheduled"
        assert calls.count(("今晚聊什么？", "阿青")) == 3
        assert client.post(path + "/stop", headers=headers, json={"source": "ios_app"}).status_code == 200
        assert client.post(path + "/observations", headers=headers, json=row(
            "stopped", "stopped-batch", 0, 1, "阿青：今晚聊什么？", .3)).status_code == 409
