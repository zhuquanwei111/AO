import asyncio
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
                                           "typed_prompts": True}
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
                     "first_observed_at_ms": now,
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


def test_reconstructed_reply_does_not_refresh_its_first_observed_time(tmp_path):
    now = 1_790_000_000_000
    clock = [now]
    store = LiveStore(str(tmp_path / "first-time.sqlite3"), lambda: clock[0])
    try:
        session = store.create("")
        sid, token = session["session_id"], session["session_token"]
        row = {"event_id": "e2", "kind": "comment", "source": "ios_replaykit_ocr",
               "coverage": "ocr_visible_sample", "reliability": "known", "confidence": 0.9,
               "observed_at_ms": now + 3000, "effective_at_ms": now + 3000,
               "received_at_ms": now + 3000,
               "content": {"text": "后续片段", "text_position": None}}
        candidate = {"topic": "comment:c1", "priority": 25, "phenomenon": "公屏评论",
                     "observations": [row], "prompt_type": "comment_reply",
                     "first_observed_at_ms": now,
                     "reply_to": {"comment_id": "c1", "nickname": None, "text": "你好"}}
        clock[0] = now + 10_001
        assert store.save_fusion_candidate(sid, token, candidate, "你好呀。") == {
            "reason": "candidate_expired", "prompt_id": None}
        assert store.events(sid, token)["events"] == []
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
        assert store.comment_batches[sid][("ios_replaykit_ocr", "batch")]["quality_gaps"] == [
            {"event_id": "e2", "text_position": None}]
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


def test_complete_batch_runs_real_reconstructor_without_promoting_candidate(tmp_path):
    now = 1_790_000_000_000
    store = LiveStore(str(tmp_path / "reconstruct.sqlite3"), lambda: now)
    try:
        session = store.create("")
        sid, token = session["session_id"], session["session_token"]
        first = {"kind": "comment", "event_id": "e1", "source": "ios_replaykit_ocr",
                 "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
                 "reliability": "known", "confidence": 0.95,
                 "content": {"text": "紫菜蛋花汤：bless you", "text_position":
                             {"x": 0.1, "y": 0.5, "width": 0.4, "height": 0.04}},
                 "capture_batch_id": "batch", "line_index": 0, "capture_comment_count": 2}
        assert store.add_observation(sid, token, first, now)["reason"] == "batch_incomplete"
        assert store.comment_batches[sid][("ios_replaykit_ocr", "batch")].get("records") is None
        last = {**first, "event_id": "e2", "line_index": 3, "availability": "unreliable"}
        store.add_observation(sid, token, last, now + 1000)
        records = store.comment_batches[sid][("ios_replaykit_ocr", "batch")]["records"]
        assert len(records) == 1
        assert records[0]["text"] == "bless you"
        assert records[0]["source_event_ids"] == ["e1"]
        assert records[0]["field_status"]["text"] == "candidate"
        assert records[0]["boundary_status"] == "partial"
        assert store.fusion_candidate(sid, token) is None
    finally:
        store.close()


def test_expired_batch_discards_transient_raw_payloads_on_poll(tmp_path):
    now = 1_790_000_000_000
    clock = [now]
    store = LiveStore(str(tmp_path / "batch-cleanup.sqlite3"), lambda: clock[0])
    try:
        session = store.create("")
        sid, token = session["session_id"], session["session_token"]
        row = {"kind": "comment", "event_id": "e1", "source": "ios_replaykit_ocr",
               "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
               "reliability": "known", "content": {"text": "小明：你好", "text_position":
                   {"x": 0.1, "y": 0.5, "width": 0.4, "height": 0.04}},
               "capture_batch_id": "batch", "line_index": 0, "capture_comment_count": 1}
        store.add_observation(sid, token, row, now)
        batch = store.comment_batches[sid][("ios_replaykit_ocr", "batch")]
        assert batch["records"][0]["fragments"]
        clock[0] += 10_001
        store.prompts(sid, token, "")
        assert "records" not in batch and batch["rows"] == []
    finally:
        store.close()


def test_unreliable_batch_item_keeps_only_boundary_geometry(tmp_path):
    now = 1_790_000_000_000
    store = LiveStore(str(tmp_path / "batch-boundary.sqlite3"), lambda: now)
    try:
        session = store.create("")
        sid, token = session["session_id"], session["session_token"]
        rect = {"x": 0.1, "y": 0.55, "width": 0.3, "height": 0.04}
        bad = {"kind": "comment", "event_id": "quality-gap", "source": "ios_replaykit_ocr",
               "observed_at_ms": now, "availability": "unreliable", "coverage": "ocr_visible_sample",
               "reliability": "unknown", "content": {"text": "不可靠原文", "text_position": rect},
               "capture_batch_id": "batch", "line_index": 1, "capture_comment_count": 2}
        assert store.add_observation(sid, token, bad, now)["reason"] == "source_unreliable"
        context = store.comment_batches[sid][("ios_replaykit_ocr", "batch")]["quality_gaps"]
        assert context == [{"event_id": "quality-gap", "text_position": rect}]
        assert "text" not in context[0]
        assert context[0]["text_position"] is not rect
    finally:
        store.close()


def test_legacy_phone_observation_samples_keep_http_time_and_source_classification(tmp_path):
    # 取自脱敏录制的 Flow #7、16、27、37、153、269、3271、3306；无帧和批次真值。
    clock = [1_793_361_760_000]
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="",
                   tianyi_base_url="http://model.invalid", tianyi_model="unused",
                   dedup_ttl_seconds=60, max_sentence_chars=2000,
                   model_timeout_seconds=1, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "phone-samples.sqlite3"))
    source = "android_media_projection_ocr"
    # 会话组、原 event ID、采集与接收毫秒、原文本、位置、原处理结果。
    samples = [
        (1, "cbd8b328-55ff-4b23-9d91-383b51a5e4c9", 1793361765436, 1793361769294, None, None, "source_missing"),
        (1, "7e3022ac-8384-4595-84b9-740bd381934e", 1793361772303, 1793361776458, "胡言乱语：还在绷", (0.14047619047619048, 0.8114285714285714, 0.2746031746031746, 0.017142857142857144), "scheduled"),
        (1, "395bdea6-ad54-49e7-822f-33f29b7a24d0", 1793361772303, 1793361776899, "0万+", (0.8, 0.055, 0.1, 0.045), "field_semantics_unconfirmed"),
        (1, "80563833-b1f8-4f8b-b7a9-8c482791f85c", 1793361779222, 1793361780992, "八点领快递：坏孩子", (0.2, 0.7782142857142857, 0.3055555555555556, 0.016428571428571428), "scheduled"),
        (1, "112a9c4e-d31e-4db2-bbb0-a649f91faabd", 1793361799323, 1793361801009, "10yalo:喊麦", (0.0880952380952381, 0.8946428571428572, 0.22698412698412698, 0.017857142857142856), "scheduled"),
        (1, "0c18762c-0a4d-4d5b-a7fb-43a8666f14d7", 1793361822507, 1793361824210, "11：喊麦", (0.19523809523809524, 0.8482142857142857, 0.14365079365079364, 0.018571428571428572), "scheduled"),
        (3, "e3331ab0-35c3-4173-a263-321b688fca16", 1793363769050, 1793363772822, "点点关注 一台苹果耳机", (0.2992063492063492, 0.7225, 0.3531746031746032, 0.016428571428571428), "scheduled"),
        (3, "30f68222-1444-4f57-a6b5-d621fd8622da", 1793363775724, 1793363778040, "点点关注 一台苹果耳机", (0.4373015873015873, 0.8789285714285714, 0.3523809523809524, 0.016428571428571428), "scheduled"),
    ]
    with TestClient(create_app(cfg, live_clock=lambda: clock[0])) as client:
        sessions = {}
        for group, event_id, observed, received, raw, rect, expected in samples:
            if group not in sessions:
                clock[0] = observed - 1000
                session = client.post("/v1/live/sessions", json={}).json()
                headers = {"X-Live-Session-Token": session["session_token"]}
                url = f"/v1/live/sessions/{session['session_id']}"
                assert client.post(url + "/start", headers=headers, json={"source": "android_app"}).status_code == 200
                sessions[group] = (url, headers)
            url, headers = sessions[group]
            clock[0] = received
            is_count = expected in {"source_missing", "field_semantics_unconfirmed"}
            position = dict(zip(("x", "y", "width", "height"), rect)) if rect else None
            content = None if raw is None else ({"raw_text": raw, "field_semantics_confirmed": False,
                                                 "text_position": position} if is_count else
                                                {"text": raw, "text_position": position})
            body = {"kind": "online_count" if is_count else "comment", "event_id": event_id,
                    "source": source, "observed_at_ms": observed,
                    "availability": "missing" if raw is None else "available",
                    "coverage": "ocr_visible_sample", "reliability": "unknown" if raw is None else "known",
                    "content": content}
            response = client.post(url + "/observations", headers=headers, json=body)
            assert response.status_code == 200
            result = response.json()
            assert (result["status"], result["reason"], result["effective_at_ms"], result["time_basis"]) == (
                "accepted", expected, observed, "observed")
            assert result["received_at_ms"] == received


def test_complete_batch_real_reconstruction_produces_typed_direct_reply(tmp_path):
    now = 1_790_000_000_000
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="controlled",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake",
                   dedup_ttl_seconds=60, max_sentence_chars=2000,
                   model_timeout_seconds=1, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "real-reconstruction.sqlite3"))
    calls = []
    class ControlledModel:
        async def reply_to_comment(self, text, *, nickname, priority_eligible):
            calls.append((text, nickname, priority_eligible))
            return "我们可以聊聊最近喜欢的事情。"
        async def aclose(self):
            pass
    app = create_app(cfg, live_clock=lambda: now)
    with TestClient(app) as client:
        app.state.model = ControlledModel()
        session = client.post("/v1/live/sessions", json={}).json()
        sid = session["session_id"]
        assert session["capabilities"]["typed_prompts"] is True
        headers = {"X-Live-Session-Token": session["session_token"]}
        row = {"kind": "comment", "event_id": "e1", "source": "ios_replaykit_ocr",
               "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
               "reliability": "known", "confidence": 0.98,
               "content": {"text": "阿青：你想聊什么？", "text_position":
                           {"x": 0.1, "y": 0.5, "width": 0.4, "height": 0.04}},
               "capture_batch_id": "batch", "line_index": 0, "capture_comment_count": 1}
        result = client.post(f"/v1/live/sessions/{sid}/observations", headers=headers, json=row)
        assert result.status_code == 200 and result.json()["reason"] == "scheduled"
        async def wait_idle():
            async with asyncio.timeout(3):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(0.005)
        client.portal.call(wait_idle)
        prompts = client.get(f"/v1/live/sessions/{sid}/prompts", headers=headers).json()["prompts"]
        assert calls == [("你想聊什么？", "阿青", False)]
        assert len(prompts) == 1
        assert prompts[0]["prompt_type"] == "comment_reply"
        assert prompts[0]["reply_to"]["text"] == "你想聊什么？"
        assert prompts[0]["reply_to"]["nickname"] == "阿青"
        assert prompts[0]["display_text"] == "我们可以聊聊最近喜欢的事情。"
        retry = client.post(f"/v1/live/sessions/{sid}/observations", headers=headers, json=row)
        assert retry.json()["reason"] == "duplicate_event"
        client.portal.call(wait_idle)
        assert calls == [("你想聊什么？", "阿青", False)]


def test_complete_batch_handles_two_comments_and_group_without_extra_model_calls(tmp_path):
    now = 1_790_000_000_000
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="controlled",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake",
                   dedup_ttl_seconds=60, max_sentence_chars=2000,
                   model_timeout_seconds=1, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "group.sqlite3"))
    calls = []
    class Model:
        async def reply_to_comment(self, text, *, nickname, priority_eligible):
            calls.append((text, nickname))
            return "我们聊聊最近喜欢的事情。"
        async def aclose(self):
            pass
    app = create_app(cfg, live_clock=lambda: now)
    with TestClient(app) as client:
        app.state.model = Model()
        session = client.post("/v1/live/sessions", json={}).json()
        sid, token = session["session_id"], session["session_token"]
        headers = {"X-Live-Session-Token": token}
        base = {"kind": "comment", "source": "ios_replaykit_ocr", "observed_at_ms": now,
                "availability": "available", "coverage": "ocr_visible_sample", "reliability": "known",
                "capture_batch_id": "batch", "capture_comment_count": 2}
        for index, name in enumerate(("小明", "小红")):
            row = {**base, "event_id": f"e{index}", "line_index": index,
                   "content": {"text": f"{name}：今晚聊什么？", "text_position":
                               {"x": 0.1, "y": 0.3 + index * 0.3, "width": 0.4, "height": 0.04}}}
            response = client.post(f"/v1/live/sessions/{sid}/observations", headers=headers, json=row)
            assert response.status_code == 200
        async def wait_idle():
            async with asyncio.timeout(4):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(0.005)
        client.portal.call(wait_idle)
        events = client.get(f"/v1/live/sessions/{sid}/events", headers=headers).json()["events"]
        assert calls == [("今晚聊什么？", "小明"), ("今晚聊什么？", "小红")]
        assert [event["prompt_type"] for event in events] == ["comment_reply", "comment_reply", "operation_notice"]
        assert "集中" in events[2]["display_text"]
        assert len(events[2]["sources"]) == 2
        for index, name in enumerate(("小明", "小红")):
            row = {**base, "capture_batch_id": "next-batch", "event_id": f"next-{index}",
                   "line_index": index, "content": {"text": f"{name}：今晚聊什么？",
                   "text_position": {"x": 0.1, "y": 0.3 + index * 0.3, "width": 0.4, "height": 0.04}}}
            assert client.post(f"/v1/live/sessions/{sid}/observations", headers=headers, json=row).status_code == 200
        client.portal.call(wait_idle)
        events = client.get(f"/v1/live/sessions/{sid}/events", headers=headers).json()["events"]
        # 不同批次同昵称同正文只限制重复输出，不删除观察；群体提醒也保持独立冷却。
        assert len(calls) == 2
        assert len([event for event in events if event["prompt_type"] == "operation_notice"]) == 1
        assert app.state.live.store.db.execute(
            "SELECT COUNT(*) FROM live_observations WHERE session_id=?", (sid,)
        ).fetchone()[0] == 4
        for index, (name, text) in enumerate((("小明", "今晚聊什么？"), ("小花", "你有什么建议？"))):
            row = {**base, "capture_batch_id": "mixed-batch", "event_id": f"mixed-{index}",
                   "line_index": index, "content": {"text": f"{name}：{text}",
                   "text_position": {"x": 0.1, "y": 0.3 + index * 0.3, "width": 0.4, "height": 0.04}}}
            assert client.post(f"/v1/live/sessions/{sid}/observations", headers=headers, json=row).status_code == 200
        client.portal.call(wait_idle)
        assert calls[-1] == ("你有什么建议？", "小花") and len(calls) == 3


def test_unknown_quality_gap_prevents_real_reconstructor_from_calling_model(tmp_path):
    now = 1_790_000_000_000
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="controlled",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake",
                   dedup_ttl_seconds=60, max_sentence_chars=2000,
                   model_timeout_seconds=1, model_connect_timeout_seconds=1,
                   live_db_path=str(tmp_path / "gap.sqlite3"))
    calls = []
    class Model:
        async def reply_to_comment(self, *args, **kwargs):
            calls.append(args)
            return "你好。"
        async def aclose(self):
            pass
    app = create_app(cfg, live_clock=lambda: now)
    with TestClient(app) as client:
        app.state.model = Model()
        session = client.post("/v1/live/sessions", json={}).json()
        sid = session["session_id"]
        headers = {"X-Live-Session-Token": session["session_token"]}
        row = {"kind": "comment", "event_id": "e1", "source": "ios_replaykit_ocr",
               "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
               "reliability": "known", "content": {"text": "阿青：你想聊什么？", "text_position":
                   {"x": 0.1, "y": 0.5, "width": 0.4, "height": 0.04}},
               "capture_batch_id": "batch", "line_index": 0, "capture_comment_count": 2}
        assert client.post(f"/v1/live/sessions/{sid}/observations", headers=headers, json=row).json()["reason"] == "batch_incomplete"
        gap = {**row, "event_id": "e2", "line_index": 1, "availability": "missing", "content": None}
        assert client.post(f"/v1/live/sessions/{sid}/observations", headers=headers, json=gap).json()["reason"] == "source_missing"
        async def wait_idle():
            async with asyncio.timeout(3):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(0.005)
        client.portal.call(wait_idle)
        batch = app.state.live.store.comment_batches[sid][("ios_replaykit_ocr", "batch")]
        assert "quality_gap" in batch["records"][0]["usability_reasons"]
        assert calls == []
        assert client.get(f"/v1/live/sessions/{sid}/prompts", headers=headers).json()["prompts"] == []


@pytest.mark.parametrize("raw,rect,expected_text,expected_nickname,controlled_reply", [
    ("邪恶四眼：牙今晚还玩鹅鸭杀吗", (0.14126984126984127, 0.8114285714285714, 0.4785714285714286, 0.016428571428571428),
     "牙今晚还玩鹅鸭杀吗", "邪恶四眼", "今晚的安排到时候再和大家说。"),
    ("大梦i：现在华多少分了？", (0.14047619047619048, 0.7589285714285714, 0.37142857142857144, 0.018214285714285714),
     "现在华多少分了？", "大梦i", "这个分数我得先确认一下。"),
])
def test_phone_ocr_sample_real_reconstruction_through_controlled_reply(
        tmp_path, raw, rect, expected_text, expected_nickname, controlled_reply):
    # 原流没有批次字段；此处仅用本地合成的单项批次验证产品链，不称原流真实批次。
    now = 1_790_000_000_000
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="controlled",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake",
                   dedup_ttl_seconds=60, max_sentence_chars=2000, model_timeout_seconds=1,
                   model_connect_timeout_seconds=1, live_db_path=str(tmp_path / "phone-reply.sqlite3"))
    calls = []
    class Model:
        async def reply_to_comment(self, text, *, nickname, priority_eligible):
            calls.append((text, nickname, priority_eligible))
            return controlled_reply
        async def aclose(self):
            pass
    app = create_app(cfg, live_clock=lambda: now)
    with TestClient(app) as client:
        app.state.model = Model()
        session = client.post("/v1/live/sessions", json={}).json()
        sid = session["session_id"]
        url = f"/v1/live/sessions/{sid}"
        headers = {"X-Live-Session-Token": session["session_token"]}
        assert client.post(url + "/start", headers=headers, json={"source": "android_app"}).status_code == 200
        row = {"kind": "comment", "event_id": "sample", "source": "android_media_projection_ocr",
               "observed_at_ms": now, "availability": "available", "coverage": "ocr_visible_sample",
               "reliability": "known", "capture_batch_id": "local-controlled", "line_index": 0,
               "capture_comment_count": 1, "content": {"text": raw,
                   "text_position": dict(zip(("x", "y", "width", "height"), rect))}}
        assert client.post(url + "/observations", headers=headers, json=row).json()["reason"] == "scheduled"
        async def wait_idle():
            async with asyncio.timeout(3):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(0.005)
        client.portal.call(wait_idle)
        prompts = client.get(url + "/prompts", headers=headers).json()["prompts"]
        assert calls == [(expected_text, expected_nickname, False)]
        assert len(prompts) == 1 and prompts[0]["prompt_type"] == "comment_reply"
        assert prompts[0]["reply_to"]["text"] == expected_text
        assert prompts[0]["display_text"] == controlled_reply


def test_reply_cooldown_is_scoped_and_failure_does_not_create_cooldown(tmp_path):
    from screen_assistant_server.model_client import ModelUnavailableError
    clock = [1_790_000_000_000]
    cfg = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="controlled",
                   tianyi_base_url="http://model.invalid", tianyi_model="fake",
                   dedup_ttl_seconds=60, max_sentence_chars=2000, model_timeout_seconds=1,
                   model_connect_timeout_seconds=1, live_db_path=str(tmp_path / "cooldown.sqlite3"))
    calls = []
    class Model:
        async def reply_to_comment(self, text, *, nickname, priority_eligible):
            calls.append((text, nickname))
            if text == "失败后还可以吗？" and sum(t == text for t, _ in calls) == 1:
                raise ModelUnavailableError("controlled")
            return "我们可以聊聊这个话题。"
        async def aclose(self):
            pass
    app = create_app(cfg, live_clock=lambda: clock[0])
    with TestClient(app) as client:
        app.state.model = Model()
        session = client.post("/v1/live/sessions", json={}).json()
        sid = session["session_id"]
        headers = {"X-Live-Session-Token": session["session_token"]}
        url = f"/v1/live/sessions/{sid}"
        async def wait_idle():
            async with asyncio.timeout(3):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(0.005)
        def submit(event, raw):
            row = {"kind": "comment", "event_id": event, "source": "ios_replaykit_ocr",
                   "observed_at_ms": clock[0], "availability": "available", "coverage": "ocr_visible_sample",
                   "reliability": "known", "capture_batch_id": "batch-" + event, "line_index": 0,
                   "capture_comment_count": 1, "content": {"text": raw, "text_position":
                       {"x": 0.1, "y": 0.5, "width": 0.4, "height": 0.04}}}
            assert client.post(url + "/observations", headers=headers, json=row).json()["reason"] == "scheduled"
            client.portal.call(wait_idle)
        submit("one", "阿青：你想聊什么？")
        submit("duplicate", "阿青：你想聊什么？")
        submit("different-text", "阿青：今天聊什么？")
        submit("different-name", "小林：你想聊什么？")
        submit("unknown-one", "123：你想聊什么？")
        submit("unknown-repeat", "456：你想聊什么？")
        assert calls == [("你想聊什么？", "阿青"), ("今天聊什么？", "阿青"),
                         ("你想聊什么？", "小林"), ("你想聊什么？", None)]
        submit("fail-one", "阿青：失败后还可以吗？")
        submit("fail-two", "阿青：失败后还可以吗？")
        assert calls[-2:] == [("失败后还可以吗？", "阿青")] * 2
        quality = {"kind": "quality", "event_id": "quality", "source": "ios_replaykit_ocr",
                   "observed_at_ms": clock[0], "availability": "available", "coverage": "ocr_visible_sample",
                   "reliability": "known", "content": {"signal": "video_input_unclear"}}
        assert client.post(url + "/observations", headers=headers, json=quality).json()["reason"] == "scheduled"
        client.portal.call(wait_idle)
        events = client.get(url + "/events", headers=headers).json()["events"]
        assert any(event.get("prompt_type") == "operation_notice" for event in events)
        assert app.state.live.store.db.execute(
            "SELECT COUNT(*) FROM live_observations WHERE session_id=?", (sid,)
        ).fetchone()[0] == 9
        clock[0] += 60_001
        submit("after-cooldown", "阿青：你想聊什么？")
        assert calls[-1] == ("你想聊什么？", "阿青")
        assert sum(text == "你想聊什么？" and name == "阿青" for text, name in calls) == 2
