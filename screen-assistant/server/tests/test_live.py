"""M2 契约：显式测试观察、临时 SQLite；不调用模型或 Redis。"""
from __future__ import annotations

import asyncio
import sqlite3

import httpx
import pytest

from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from screen_assistant_server.config import Settings
from screen_assistant_server.live_store import LiveStore


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


def observation(event_id, **changes):
    value = {"event_id": event_id, "input_mode": "test_fixture",
             "source": "fixture:chat", "coverage": "structured_sample",
             "reliability": "known", "text": "主播你是哪里人？",
             "platform_message_id": event_id}
    value.update(changes)
    return value


def post_comment(client, sid, headers, event_id, **changes):
    response = client.post(f"/v1/live/sessions/{sid}/comments", headers=headers,
                           json=observation(event_id, **changes))
    assert response.status_code == 200, response.text
    return response.json()


class ControlledOperationsModel:
    def __init__(self):
        self.observations = []

    async def suggest_live(self, observation, description="", source="unknown", observed_at_ms=None, context=""):
        self.observations.append((observation, description, source, observed_at_ms, context))
        return "顺着这个问题聊两句，再邀请大家分享。"

    async def ask(self, sentence):
        from screen_assistant_server.model_client import ModelAnswer
        return ModelAnswer("旧问答仍可用", "stop", 3, 0, False, False)

    async def aclose(self):
        return None


def test_real_observation_model_prompt_poll_record_stop_and_isolation(tmp_path):
    clock = Clock()
    config = settings(tmp_path / "live.sqlite3")
    # 仅启用受控模型调用分支，不读取任何外部凭据或端点。
    config = config.__class__(**{**config.__dict__, "tianyi_api_key": "controlled-test"})
    app = create_app(config, live_clock=clock)
    model = ControlledOperationsModel()
    with TestClient(app) as client:
        app.state.model = model
        session, headers = create(client)
        sid = session["session_id"]
        url = f"/v1/live/sessions/{sid}"
        body = {
            "event_id": "ocr-observation-1", "input_mode": "live_observation",
            "source": "ios_replaykit_ocr", "coverage": "ocr_visible_sample",
            "reliability": "known", "text": "主播你是哪里人？",
            "text_position": {"x": 0.1, "y": 0.3, "width": 0.2, "height": 0.04},
            "observed_at_ms": clock.now,
        }
        generated = client.post(url + "/comments", headers=headers, json=body)
        assert generated.status_code == 200, generated.text
        result = generated.json()
        assert result["reason"] == "generated" and result["prompt_id"]
        assert model.observations == [(body["text"], "", "ios_replaykit_ocr", clock.now, "")]
        # bbox 每帧会轻微漂移，但同题同来源同时间桶仍会抑制重放。
        replay = dict(body, event_id="ocr-observation-replay",
                      text_position={"x": 0.105, "y": 0.3, "width": 0.2, "height": 0.04})
        assert client.post(url + "/comments", headers=headers, json=replay).json()["reason"] == "duplicate_message"

        page = client.get(url + "/prompts", headers=headers, params={"wait_ms": 0}).json()
        assert len(page["prompts"]) == 1
        prompt = page["prompts"][0]
        assert prompt["input_mode"] == "live_observation"
        assert prompt["display_text"] == "顺着这个问题聊两句，再邀请大家分享。"
        assert prompt["sources"][0]["source"] == "ios_replaykit_ocr"
        assert prompt["sources"][0]["text_position"] == body["text_position"]
        assert prompt["start_at_ms"] == clock.now

        other, other_headers = create(client)
        assert client.get(f"/v1/live/sessions/{other['session_id']}/events",
                          headers=headers).status_code == 403
        clock.now += 15_001
        assert client.get(url + "/prompts", headers=headers).json()["prompts"] == []
        assert len(client.get(url + "/events", headers=headers).json()["events"]) == 1
        stopped = client.post(url + "/stop", headers=headers)
        assert stopped.status_code == 200
        assert client.get(url + "/prompts", headers=headers).json()["stopped"] is True
        assert client.post(url + "/comments", headers=headers, json=body).status_code == 409
        assert client.post("/v1/answer", json={"sentence": " "}).status_code == 422


def test_mixed_screen_lines_are_not_sent_as_one_comment_and_identity_stays_unknown(tmp_path):
    clock = Clock()
    config = settings(tmp_path / "live.sqlite3")
    config = config.__class__(**{**config.__dict__, "tianyi_api_key": "controlled-test"})
    app = create_app(config, live_clock=clock)
    model = ControlledOperationsModel()
    with TestClient(app) as client:
        app.state.model = model
        session, headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}"

        # 模拟同一ROI里的普通数字/提示行；它们没有变成评论或模型输入。
        for event_id, text in (("count-line", "在线 3.2万人"),
                               ("notice-line", "平台提醒：请检查直播内容")):
            response = client.post(url + "/comments", headers=headers, json={
                "event_id": event_id, "input_mode": "live_observation",
                "source": "ios_replaykit_ocr", "coverage": "ocr_visible_sample",
                "reliability": "known", "text": text,
                "text_position": {"x": 0.7, "y": 0.1, "width": 0.2, "height": 0.03},
                "observed_at_ms": clock.now,
            })
            assert response.status_code == 200
            assert response.json()["reason"] == "not_a_question"
        assert model.observations == []

        # 单独候选的问题行带bbox和来源/时刻，不臆造昵称或平台消息ID。
        question_body = {
            "event_id": "question-line", "input_mode": "live_observation",
            "source": "ios_replaykit_ocr", "coverage": "ocr_visible_sample",
            "reliability": "known", "text": "主播你是哪里人？",
            "text_position": {"x": 0.2, "y": 0.6, "width": 0.3, "height": 0.04},
            "observed_at_ms": clock.now,
        }
        response = client.post(url + "/comments", headers=headers, json=question_body)
        assert response.status_code == 200 and response.json()["reason"] == "generated"
        assert model.observations == [(
            question_body["text"], "", "ios_replaykit_ocr", clock.now, ""
        )]
        event = client.get(url + "/events", headers=headers).json()["events"][0]
        source = event["sources"][0]
        assert source["text_position"] == question_body["text_position"]
        assert "author_id" not in source and "platform_message_id" not in source


def test_person_signal_candidate_is_recorded_with_confidence_and_cooldown(tmp_path):
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        sid = session["session_id"]
        url = f"/v1/live/sessions/{sid}"
        signal = {
            "event_id": "vision-head-down-1", "source": "ios_replaykit_vision",
            "signal": "head_down", "confidence": 0.82, "sample_count": 6,
            "started_at_ms": clock.now, "observed_at_ms": clock.now + 15_000,
        }
        clock.now += 16_000
        first = client.post(url + "/signals", headers=headers, json=signal)
        assert first.status_code == 200, first.text
        assert first.json()["reason"] == "generated"
        repeat = dict(signal, event_id="vision-head-down-2", started_at_ms=clock.now-15_000,
                      observed_at_ms=clock.now)
        assert client.post(url + "/signals", headers=headers, json=repeat).json()["reason"] == "cooldown"
        events = client.get(url + "/events", headers=headers).json()["events"]
        assert len(events) == 1
        assert events[0]["phenomenon"] == "画面中可见人物持续低头"
        assert events[0]["sources"][0]["confidence"] == 0.82
        assert events[0]["observation_count"] == 6


def test_typed_observations_fuse_by_time_and_platform_notice_preempts(tmp_path):
    async def run():
        clock = Clock()
        app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                async def session():
                    value = (await client.post("/v1/live/sessions", json={})).json()
                    return value, {"X-Live-Session-Token": value["session_token"]}

                async def post(url, headers, body):
                    response = await client.post(url + "/observations", headers=headers, json=body)
                    assert response.status_code == 200, response.text
                    return response.json()

                # 先留足启动时间，使观察区间落在本场内；读数故意以 1,3,2 的到达顺序送达。
                clock.now += 10_000
                value, headers = await session()
                clock.now += 30_000
                url = f"/v1/live/sessions/{value['session_id']}"
                now = clock.now
                count_payloads = []
                for index, (offset, amount) in enumerate(((1_000, 1_800), (7_000, 2_500), (4_000, 2_100))):
                    count_payloads.append({
                        "kind": "online_count", "event_id": f"count-{index}",
                        "source": "fixture:confirmed_online_region", "observed_at_ms": now-offset,
                        "availability": "available", "coverage": "confirmed_online_field",
                        "reliability": "known", "confidence": 0.96,
                        "content": {"raw_text": f"在线人数 {amount}人", "field_semantics_confirmed": True},
                    })
                interaction = {
                    "kind": "interaction", "event_id": "window-interaction",
                    "source": "fixture:interaction_counter", "observed_at_ms": now,
                    "started_at_ms": now-8_000, "ended_at_ms": now,
                    "availability": "available", "coverage": "complete_window",
                    "reliability": "known", "confidence": None,
                    "content": {"count": 0, "window_started_at_ms": now-8_000,
                                "window_ended_at_ms": now, "coverage_complete": True},
                }
                transcript = {
                    "kind": "transcript", "event_id": "host-transcript",
                    "source": "fixture:host_asr", "observed_at_ms": now,
                    "started_at_ms": now-2_000, "ended_at_ms": now,
                    "availability": "available", "coverage": "continuous",
                    "reliability": "known", "confidence": None,
                    "content": {"text": "刚才我们聊到这个话题", "segment_started_at_ms": now-2_000,
                                "segment_ended_at_ms": now, "is_final": True,
                                "speaker_is_host": True},
                }
                quiet = {
                    "kind": "quality", "event_id": "unreliable-audio",
                    "source": "audio:mic", "observed_at_ms": now,
                    "availability": "unreliable", "coverage": "energy_sample",
                    "reliability": "unknown", "confidence": None, "content": None,
                }
                assert (await post(url, headers, quiet))["reason"] == "source_unreliable"
                results = await asyncio.gather(*[
                    post(url, headers, payload)
                    for payload in [count_payloads[0], count_payloads[1], count_payloads[2], interaction, transcript]
                ])
                events = (await client.get(url + "/events", headers=headers)).json()["events"]
                assert len(events) == 1
                event = events[0]
                assert "互动不多" in event["display_text"] and "在线人数连续回落" in event["display_text"]
                assert {item["kind"] for item in event["sources"]} == {
                    "online_count", "interaction", "transcript"
                }
                assert event["observation_count"] == 5
                assert all(item["source_verification"] == "declared_fixture_input"
                           for item in event["sources"])
                count_sources = [item for item in event["sources"] if item["kind"] == "online_count"]
                assert all("raw_count_text" in item and item["parsed_count"] is not None
                           for item in count_sources)
                assert all(result["received_at_ms"] <= clock.now for result in results)

                # 重复 event_id 幂等；过期读数与未配置字段不会进入数字趋势。
                duplicate = await post(url, headers, count_payloads[0])
                assert duplicate["reason"] == "duplicate_event"
                missing = dict(count_payloads[0], event_id="missing-count", availability="missing", content=None)
                assert (await post(url, headers, missing))["reason"] == "source_missing"
                stale = dict(count_payloads[0], event_id="stale-count", observed_at_ms=now-20_000)
                assert (await post(url, headers, stale))["reason"] == "late"
                unconfirmed = dict(count_payloads[0], event_id="unknown-count",
                                   content={"raw_text": "在线人数 9999人", "field_semantics_confirmed": False})
                assert (await post(url, headers, unconfirmed))["reason"] == "field_semantics_unconfirmed"

                # 新场次中，候选人物状态与可见问题组成一句；随后明确平台提示越过融合等待并抢占。
                clock.now += 5_000
                second, h2 = await session()
                clock.now += 5_000
                u2 = f"/v1/live/sessions/{second['session_id']}"
                now2 = clock.now
                person = {
                    "kind": "person_state", "event_id": "head-down",
                    "source": "fixture:vision_person", "observed_at_ms": now2,
                    "started_at_ms": now2-5_000, "ended_at_ms": now2,
                    "availability": "available", "coverage": "vision_pose_sample",
                    "reliability": "known", "confidence": 0.83,
                    "content": {"signal": "head_down", "sample_count": 6},
                }
                comment = {
                    "kind": "comment", "event_id": "visible-question", "source": "fixture:ocr_comment",
                    "observed_at_ms": now2, "availability": "available",
                    "coverage": "ocr_visible_sample", "reliability": "known", "confidence": 0.88,
                    "content": {"text": "主播你是哪里人？", "text_position": None,
                                "author_id": None, "platform_message_id": None},
                }
                await asyncio.gather(post(u2, h2, person), post(u2, h2, comment))
                fused = (await client.get(u2 + "/events", headers=h2)).json()["events"]
                assert len(fused) == 1 and "持续低头" in fused[0]["phenomenon"]
                assert len(fused[0]["sources"]) == 2

                clock.now += 5_000
                repeated_session, hr = await session()
                ur = f"/v1/live/sessions/{repeated_session['session_id']}"
                repeat_rows = [dict(comment, event_id=f"repeat-question-{index}",
                                    source="fixture:ocr_comment", observed_at_ms=clock.now,
                                    content={"text": "主播你是哪里人？",
                                             "text_position": {"x": 0.2 + index * 0.2, "y": 0.6,
                                                                "width": 0.3, "height": 0.04},
                                             "author_id": None, "platform_message_id": None})
                               for index in range(2)]
                await asyncio.gather(*(post(ur, hr, row) for row in repeat_rows))
                repeated = (await client.get(ur + "/events", headers=hr)).json()["events"]
                assert len(repeated) == 1 and "重复出现同一问题主题" in repeated[0]["phenomenon"]
                assert len(repeated[0]["sources"]) == 2

                # 警告明确来自已核对提醒区，原文证据保留，且优先于普通评论模型任务。
                third, h3 = await session()
                u3 = f"/v1/live/sessions/{third['session_id']}"
                normal_comment = dict(comment, event_id="ordinary-question", observed_at_ms=clock.now)
                ordinary_task = asyncio.create_task(post(u3, h3, normal_comment))
                await asyncio.sleep(0.03)
                notice = {
                    "kind": "platform_notice", "event_id": "confirmed-warning",
                    "source": "fixture:platform_notice_region", "observed_at_ms": clock.now,
                    "availability": "available", "coverage": "confirmed_notice_region",
                    "reliability": "known", "confidence": 0.96,
                        "content": {"text": "请查看直播提示", "region_confirmed": True,
                                    "explicit_warning_confirmed": False,
                                    "text_position": {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.12},
                                    "ocr_lines": [{"text": "请查看直播提示",
                                                   "bbox": {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.12},
                                                   "confidence": 0.91}]},
                }
                warning_result = await post(u3, h3, notice)
                await ordinary_task
                assert warning_result["reason"] == "generated"
                warning_events = (await client.get(u3 + "/events", headers=h3)).json()["events"]
                assert len(warning_events) == 1
                assert warning_events[0]["sources"][0]["evidence_text"] == "请查看直播提示"
                assert warning_events[0]["sources"][0]["text_position"]["x"] == 0.1
                assert warning_events[0]["sources"][0]["ocr_lines"][0]["confidence"] == 0.91
                assert "请查看直播提示" in warning_events[0]["display_text"]
                stopped = await client.post(u3 + "/stop", headers=h3)
                assert stopped.status_code == 200
                after_stop = await client.post(u3 + "/observations", headers=h3, json=normal_comment)
                assert after_stop.status_code == 409

    asyncio.run(run())


def test_stopped_session_online_count_peak_weighted_average_and_gaps(tmp_path):
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        sid = session["session_id"]
        url = f"/v1/live/sessions/{sid}"

        def submit(event_id, raw_text=None, *, availability="available", field_confirmed=True,
                   evidence=False):
            body = {
                "kind": "online_count", "event_id": event_id,
                "source": "fixture:calibrated_online_field", "observed_at_ms": clock.now,
                "availability": availability, "coverage": "calibrated_online_field",
                "reliability": "known", "confidence": 0.94,
                "content": ({
                    "raw_text": raw_text, "field_semantics_confirmed": field_confirmed,
                    "text_position": {"x": 0.2, "y": 0.1, "width": 0.3, "height": 0.04},
                    "ocr_lines": [{"text": raw_text, "bbox": {"x": 0.2, "y": 0.1, "width": 0.3, "height": 0.04},
                                   "confidence": 0.88}],
                } if availability == "available" and evidence else
                    ({"raw_text": raw_text, "field_semantics_confirmed": field_confirmed}
                     if availability == "available" else None)),
            }
            response = client.post(url + "/observations", headers=headers, json=body)
            assert response.status_code == 200, response.text
            return response.json()

        assert submit("count-100", "在线人数 100人")["reason"] == "windowed"
        clock.now += 2_000
        assert submit("count-200", "在线人数 200人")["reason"] == "windowed"
        clock.now += 6_000
        assert submit("count-400", "在线人数约0.4千")["reason"] == "windowed"
        clock.now += 2_000
        assert submit("count-missing", availability="missing")["reason"] == "source_missing"
        clock.now += 16_000
        assert submit("count-500", "0.5千", evidence=True)["reason"] == "windowed"
        assert client.post(url + "/stop", headers=headers).status_code == 200

        summary = client.get(url + "/summary", headers=headers)
        assert summary.status_code == 200, summary.text
        data = summary.json()["online_count"]
        assert data["status"] == "available"
        assert data["peak"] == {
            "nominal_value": 500, "raw_text": "0.5千", "approximate": True,
            "display_unit": "千",
            "text_position": {"x": 0.2, "y": 0.1, "width": 0.3, "height": 0.04},
            "ocr_lines": [{"text": "0.5千", "bbox": {"x": 0.2, "y": 0.1, "width": 0.3, "height": 0.04},
                           "confidence": 0.88}],
            "observed_at_ms": clock.now, "confidence": 0.94,
        }
        assert data["time_weighted_average"] == {
            "nominal_value": 175.0, "approximate": True,
            "method": "previous_sample_hold_between_valid_observations",
        }
        assert data["coverage"]["covered_duration_ms"] == 8_000
        assert data["coverage"]["uncovered_between_samples_ms"] == 18_000
        assert data["coverage"]["sample_issues"] == {"source_missing": 1}
        assert summary.json()["online_count_by_source"][0]["source_verification"] == "declared_fixture_input"

        empty, empty_headers = create(client)
        empty_url = f"/v1/live/sessions/{empty['session_id']}"
        unknown_time = {
            "kind": "online_count", "event_id": "count-without-time",
            "source": "fixture:calibrated_online_field",
            "availability": "available", "coverage": "calibrated_online_field",
            "reliability": "known", "confidence": None,
            "content": {"raw_text": "在线人数 900人", "field_semantics_confirmed": True},
        }
        unknown_result = client.post(empty_url + "/observations", headers=empty_headers, json=unknown_time)
        assert unknown_result.status_code == 200
        assert unknown_result.json()["reason"] == "observation_time_unknown"
        client.post(empty_url + "/stop", headers=empty_headers)
        empty_summary = client.get(empty_url + "/summary", headers=empty_headers).json()["online_count"]
        assert empty_summary["status"] == "unknown"
        assert empty_summary["peak"] is None and empty_summary["time_weighted_average"] is None
        assert empty_summary["coverage"]["sample_issues"] == {"observation_time_unknown": 1}


def test_m5_count_evidence_columns_migrate_existing_m4_database(tmp_path):
    path = tmp_path / "m4.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("""
        CREATE TABLE live_count_samples (
            session_id TEXT NOT NULL, event_hash TEXT NOT NULL, source TEXT NOT NULL,
            observed_at_ms INTEGER, effective_at_ms INTEGER NOT NULL, received_at_ms INTEGER NOT NULL,
            availability TEXT NOT NULL, reliability TEXT NOT NULL, coverage TEXT NOT NULL,
            confidence REAL, field_semantics_confirmed INTEGER NOT NULL,
            raw_text TEXT, parsed_count INTEGER, approximate INTEGER NOT NULL,
            reason TEXT NOT NULL, PRIMARY KEY (session_id, event_hash)
        )
    """)
    connection.commit()
    connection.close()

    store = LiveStore(str(path), lambda: 1_800_000_000_000)
    try:
        columns = {row["name"] for row in store.db.execute("PRAGMA table_info(live_count_samples)")}
        assert {"display_unit", "text_position_json", "ocr_lines_json"} <= columns
    finally:
        store.close()


def test_fixture_loop_dedup_independent_questions_and_reopen(tmp_path):
    path, clock = tmp_path / "live.sqlite3", Clock()
    with TestClient(create_app(settings(path), live_clock=clock)) as client:
        session, headers = create(client)
        sid, started = session["session_id"], session["started_at_ms"]
        url = f"/v1/live/sessions/{sid}"
        first = post_comment(client, sid, headers, "m1", author_id="alice")
        assert first["counted"] and first["prompt_id"] is None
        assert post_comment(client, sid, headers, "m1", author_id="alice")["status"] == "duplicate"
        assert post_comment(client, sid, headers, "retry-new-id", platform_message_id="m1")["reason"] == "duplicate_message"
        assert client.get(url + "/prompts", headers=headers).json()["prompts"] == []
        clock.now += 1000
        second = post_comment(client, sid, headers, "m2", text="你来自哪里？", author_id="bob")
        assert second["prompt_id"]
        prompts = client.get(url + "/prompts", headers=headers).json()
        assert len(prompts["prompts"]) == 1
        event = prompts["prompts"][0]
        assert event["observation_count"] == 2
        assert event["start_offset_ms"] == 0 and event["end_offset_ms"] == 1000
        assert event["time_basis"] == "received"
        assert event["display_text"] == "公屏反复问“来自哪里”，可以集中答一下。"
        assert event["delivery_state"] == "returned_to_client"
        assert event["start_at_ms"] == started
        assert client.get(url + "/prompts", headers=headers,
                          params={"after": prompts["next_cursor"]}).json()["prompts"] == []
        other, other_headers = create(client)
        post_comment(client, other["session_id"], other_headers, "m1")
        assert client.get(f"/v1/live/sessions/{other['session_id']}/events", headers=other_headers).json()["events"] == []
        assert client.get(f"/v1/live/sessions/{other['session_id']}/prompts", headers=other_headers,
                          params={"after": prompts["next_cursor"]}).status_code == 422
        client.post(url + "/stop", headers=headers)
        assert client.post(url + "/comments", headers=headers,
                           json=observation("after-stop")).status_code == 409
        assert client.get(url + "/prompts", headers=headers).json()["prompts"] == []
    with TestClient(create_app(settings(path), live_clock=clock)) as client:
        saved = client.get(url + "/events", headers=headers).json()["events"]
        assert saved == [event]
    with sqlite3.connect(path) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(live_observations)")]
        assert "text" not in columns and "token" not in columns
        assert db.execute("SELECT COUNT(*) FROM live_observations WHERE session_id=?", (sid,)).fetchone()[0] == 3


def test_unknown_ocr_no_evidence_and_opposite_questions_are_quiet(tmp_path):
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())) as client:
        session, headers = create(client)
        sid = session["session_id"]
        assert post_comment(client, sid, headers, "u", reliability="unknown")["reason"] == "unreliable"
        assert post_comment(client, sid, headers, "o", coverage="ocr_visible_sample")["reason"] == "ocr_tracking_unavailable"
        assert post_comment(client, sid, headers, "n", platform_message_id=None)["reason"] == "no_independent_evidence"
        post_comment(client, sid, headers, "positive", text="你是北京人吗？")
        post_comment(client, sid, headers, "negative", text="你不是北京人吗？")
        post_comment(client, sid, headers, "chat", text="这首歌真好听")
        for i in range(2):
            post_comment(client, sid, headers, f"statement-{i}", text="我知道你来自哪里")
        assert client.get(f"/v1/live/sessions/{sid}/prompts", headers=headers).json()["prompts"] == []


def test_cooldown_expiry_late_window_and_source_time(tmp_path):
    from screen_assistant_server.live_rules import COOLDOWN_MS, PROMPT_TTL_MS, WINDOW_MS
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        sid, start = session["session_id"], clock.now
        url = f"/v1/live/sessions/{sid}"
        post_comment(client, sid, headers, "a", observed_at_ms=start)
        clock.now += 400
        post_comment(client, sid, headers, "b", observed_at_ms=clock.now)
        generated = client.get(url + "/events", headers=headers).json()["events"][0]
        assert generated["time_basis"] == "observed"
        assert generated["delivery_state"] == "generated" and generated["returned_at_ms"] is None
        clock.now += PROMPT_TTL_MS
        assert client.get(url + "/prompts", headers=headers).json()["prompts"] == []
        assert post_comment(client, sid, headers, "late", observed_at_ms=start)["reason"] == "late"
        assert post_comment(client, sid, headers, "cooldown")["reason"] == "cooldown"
        clock.now = start + COOLDOWN_MS + 1000
        assert post_comment(client, sid, headers, "new1")["prompt_id"] is None
        assert post_comment(client, sid, headers, "new2")["prompt_id"]
        assert len(client.get(url + "/events", headers=headers).json()["events"]) == 2
        other, oh = create(client)
        osid = other["session_id"]
        post_comment(client, osid, oh, "window1")
        clock.now += WINDOW_MS + 1
        assert post_comment(client, osid, oh, "window2")["prompt_id"] is None
        for timestamp in (-1, start-1, clock.now+1, True, 1.5):
            assert client.post(url + "/comments", headers=headers,
                               json=observation("bad-time", observed_at_ms=timestamp)).status_code == 422


def test_point_event_anonymous_authors_and_idempotency_conflict(tmp_path):
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        sid, url = session["session_id"], f"/v1/live/sessions/{session['session_id']}"
        post_comment(client, sid, headers, "one")
        post_comment(client, sid, headers, "two")
        event = client.get(url + "/events", headers=headers).json()["events"][0]
        assert event["start_offset_ms"] == event["end_offset_ms"] == 0
        assert "多位" not in event["display_text"] and "人" not in event["display_text"]
        assert client.post(url + "/comments", headers=headers,
                           json=observation("one", text=" changed ")).status_code == 409
        assert client.get(url + "/events", headers={"X-Live-Session-Token": "wrong"}).status_code == 403
        assert client.get(url + "/prompts", headers=headers, params={"after": "bad!"}).status_code == 422
        assert client.get(url + "/prompts", headers=headers, params={"wait_ms": 15001}).status_code == 422


def test_only_author_evidence_counts_each_author_once(tmp_path):
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())) as client:
        session, headers = create(client)
        sid = session["session_id"]
        post_comment(client, sid, headers, "a1", platform_message_id=None, author_id="a")
        assert post_comment(client, sid, headers, "a2", platform_message_id=None,
                            author_id="a")["reason"] == "same_author_without_message_id"
        assert post_comment(client, sid, headers, "b1", platform_message_id=None, author_id="b")["prompt_id"]


def test_restart_keeps_idempotency_but_does_not_replay_raw_window(tmp_path):
    path, clock = tmp_path / "live.sqlite3", Clock()
    for index in range(2):
        with TestClient(create_app(settings(path), live_clock=clock)) as client:
            if index == 0:
                session, headers = create(client)
                sid = session["session_id"]
                post_comment(client, sid, headers, "one")
            else:
                assert post_comment(client, sid, headers, "one")["status"] == "duplicate"
                assert post_comment(client, sid, headers, "two")["prompt_id"] is None


def test_comment_and_event_are_one_transaction(tmp_path):
    app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())
    with TestClient(app, raise_server_exceptions=False) as client:
        session, headers = create(client)
        sid = session["session_id"]
        post_comment(client, sid, headers, "first")
        db = app.state.live.store.db
        db.execute("CREATE TRIGGER reject_observation BEFORE INSERT ON live_observations BEGIN SELECT RAISE(FAIL, 'fixture'); END")
        response = client.post(f"/v1/live/sessions/{sid}/comments", headers=headers,
                               json=observation("second"))
        assert response.status_code == 503
        assert client.get(f"/v1/live/sessions/{sid}/events", headers=headers).json()["events"] == []
        db.execute("DROP TRIGGER reject_observation")
        assert post_comment(client, sid, headers, "second")["prompt_id"]


def test_create_without_description_body(tmp_path):
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"))) as client:
        assert client.post("/v1/live/sessions").status_code == 201


async def wait_for_waiters(app, count):
    async def ready():
        while sum(len(v) for v in app.state.live.waiters.values()) != count:
            await asyncio.sleep(0.001)
    await asyncio.wait_for(ready(), 2)


def test_long_poll_wakeup_stop_timeout_waiter_limit_and_cancel(tmp_path):
    from screen_assistant_server.live_rules import MAX_SESSION_WAITERS

    async def run():
        app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                sid = session["session_id"]
                headers = {"X-Live-Session-Token": session["session_token"]}
                url = f"/v1/live/sessions/{sid}"
                poll = asyncio.create_task(client.get(url + "/prompts", headers=headers, params={"wait_ms": 1000}))
                await wait_for_waiters(app, 1)
                for mid in ("first", "second"):
                    r = await asyncio.wait_for(client.post(url + "/comments", headers=headers, json=observation(mid)), 0.5)
                    assert r.status_code == 200
                result = (await asyncio.wait_for(poll, 0.5)).json()
                assert len(result["prompts"]) == 1
                after = result["next_cursor"]
                start = asyncio.get_running_loop().time()
                empty = await client.get(url + "/prompts", headers=headers, params={"after": after, "wait_ms": 40})
                assert empty.json()["prompts"] == []
                assert 0.025 <= asyncio.get_running_loop().time()-start < 1
                polls = [asyncio.create_task(client.get(url + "/prompts", headers=headers,
                         params={"after": after, "wait_ms": 1000})) for _ in range(MAX_SESSION_WAITERS)]
                await wait_for_waiters(app, MAX_SESSION_WAITERS)
                assert (await client.get(url + "/prompts", headers=headers, params={"after": after, "wait_ms": 1000})).status_code == 429
                polls[0].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await polls[0]
                await wait_for_waiters(app, MAX_SESSION_WAITERS-1)
                assert (await client.post(url + "/stop", headers=headers)).status_code == 200
                for pending in polls[1:]:
                    stopped = (await asyncio.wait_for(pending, 0.5)).json()
                    assert stopped["stopped"] and stopped["prompts"] == []
                assert not app.state.live.waiters
    asyncio.run(run())


@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "/events", None), ("GET", "/prompts", None),
    ("POST", "/stop", None), ("POST", "/comments", observation("x")),
])
def test_all_session_routes_require_matching_token(tmp_path, method, suffix, body):
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"))) as client:
        session, _ = create(client)
        url = f"/v1/live/sessions/{session['session_id']}" + suffix
        assert client.request(method, url, json=body).status_code == 403
        assert client.request(method, url, headers={"X-Live-Session-Token": "bad"}, json=body).status_code == 403


def test_concurrent_duplicate_submissions_create_one_event(tmp_path):
    async def run():
        app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                url = f"/v1/live/sessions/{session['session_id']}"
                headers = {"X-Live-Session-Token": session["session_token"]}
                requests = [client.post(url + "/comments", headers=headers, json=observation(f"m{i % 2}")) for i in range(12)]
                responses = await asyncio.gather(*requests)
                assert all(r.status_code == 200 for r in responses)
                assert sum(r.json()["counted"] for r in responses) == 2
                events = (await client.get(url + "/events", headers=headers)).json()["events"]
                assert len(events) == 1 and events[0]["observation_count"] == 2
    asyncio.run(run())


def test_pending_window_and_idempotency_metadata_limits(tmp_path, monkeypatch):
    import screen_assistant_server.live_store as store_module
    from screen_assistant_server.live_rules import PROMPT_TTL_MS
    monkeypatch.setattr(store_module, "MAX_PENDING", 2)
    monkeypatch.setattr(store_module, "MAX_WINDOW_OBSERVATIONS", 4)
    monkeypatch.setattr(store_module, "MAX_OBSERVATIONS", 8)
    clock = Clock()
    app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)
    with TestClient(app) as client:
        session, headers = create(client)
        sid, url = session["session_id"], f"/v1/live/sessions/{session['session_id']}"
        for i in range(3):
            for j in range(2):
                result = post_comment(client, sid, headers, f"{i}-{j}", text=f"问题{i}怎么做？")
            assert result["reason"] == ("generated" if i < 2 else "pending_limit")
        assert len(app.state.live.store.windows[sid]) <= 4
        clock.now += PROMPT_TTL_MS
        # 过期候选不占待返回名额；此处换一个新问题以避开主题冷却。
        for j in range(2):
            result = post_comment(client, sid, headers, f"new-{j}", text="问题3怎么做？")
        assert result["prompt_id"]
        assert client.post(url + "/comments", headers=headers, json=observation("overflow")).status_code == 429
        # 达到上限仍允许完全相同的幂等重试。
        assert post_comment(client, sid, headers, "new-1", text="问题3怎么做？")["status"] == "duplicate"


def test_no_original_comment_stream_or_token_in_storage_or_logs(tmp_path, caplog):
    path = tmp_path / "live.sqlite3"
    with TestClient(create_app(settings(path))) as client:
        session, headers = create(client)
        post_comment(client, session["session_id"], headers, "private", text="fixture-private-comment-123")
    with sqlite3.connect(path) as db:
        dump = "\n".join(db.iterdump())
    assert session["session_token"] not in dump + caplog.text
    assert "fixture-private-comment-123" not in dump + caplog.text


def test_commit_failure_does_not_add_ghost_window_observation(tmp_path):
    app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=Clock())
    with TestClient(app) as client:
        session, headers = create(client)
        sid = session["session_id"]
        db = app.state.live.store.db

        def deny_commit(action, first, *_):
            if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(deny_commit)
        response = client.post(f"/v1/live/sessions/{sid}/comments", headers=headers,
                               json=observation("one"))
        assert response.status_code == 503
        db.set_authorizer(None)
        assert post_comment(client, sid, headers, "one")["prompt_id"] is None
        assert post_comment(client, sid, headers, "two")["prompt_id"]


def test_sqlite_io_runs_off_the_event_loop(tmp_path):
    import time

    async def run():
        app = create_app(settings(tmp_path / "live.sqlite3"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                session = (await client.post("/v1/live/sessions", json={})).json()
                original = app.state.live.store.events
                started = asyncio.Event()
                loop = asyncio.get_running_loop()

                def slow(*args):
                    loop.call_soon_threadsafe(started.set)
                    time.sleep(0.15)
                    return original(*args)

                app.state.live.store.events = slow
                task = asyncio.create_task(client.get(f"/v1/live/sessions/{session['session_id']}/events",
                    headers={"X-Live-Session-Token": session["session_token"]}))
                await asyncio.wait_for(started.wait(), 1)
                await asyncio.sleep(0.01)
                assert not task.done()
                assert (await task).status_code == 200
    asyncio.run(run())


def test_live_storage_failure_does_not_break_legacy_contract(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("fixture", encoding="utf-8")
    with TestClient(create_app(settings(blocked / "live.sqlite3"))) as client:
        assert client.post("/v1/live/sessions", json={}).status_code == 503
        # 旧接口在调用模型/Redis 前的输入校验仍可独立执行。
        assert client.post("/v1/answer", json={"sentence": " "}).status_code == 422


def test_event_pagination_and_fresh_prompts_survive_reopen(tmp_path):
    path, clock = tmp_path / "live.sqlite3", Clock()
    with TestClient(create_app(settings(path), live_clock=clock)) as client:
        session, headers = create(client)
        sid, url = session["session_id"], f"/v1/live/sessions/{session['session_id']}"
        for i in range(3):
            for j in range(2):
                post_comment(client, sid, headers, f"{i}-{j}", text=f"问题{i}怎么做？")
        after, ids = "", []
        for i in range(3):
            page = client.get(url + "/events", headers=headers,
                              params={"after": after, "limit": 1}).json()
            assert len(page["events"]) == 1
            assert page["has_more"] is (i < 2)
            ids.append(page["events"][0]["prompt_id"])
            assert page["next_cursor"] != after
            after = page["next_cursor"]
        assert len(set(ids)) == 3
    with TestClient(create_app(settings(path), live_clock=clock)) as client:
        result = client.get(url + "/prompts", headers=headers).json()
        assert [p["prompt_id"] for p in result["prompts"]] == ids
        assert all(p["delivery_state"] == "returned_to_client" for p in result["prompts"])
        assert client.get(url + "/prompts", headers=headers,
                          params={"after": result["next_cursor"]}).json()["prompts"] == []


def test_poll_global_waiter_bound(tmp_path):
    from screen_assistant_server.live_rules import MAX_WAITERS, MAX_SESSION_WAITERS

    async def run():
        app = create_app(settings(tmp_path / "live.sqlite3"))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                polls = []
                try:
                    for _ in range(MAX_WAITERS // MAX_SESSION_WAITERS):
                        session = (await client.post("/v1/live/sessions", json={})).json()
                        for _ in range(MAX_SESSION_WAITERS):
                            polls.append(asyncio.create_task(client.get(
                                f"/v1/live/sessions/{session['session_id']}/prompts",
                                headers={"X-Live-Session-Token": session["session_token"]}, params={"wait_ms": 5000})))
                    await wait_for_waiters(app, MAX_WAITERS)
                    other = (await client.post("/v1/live/sessions", json={})).json()
                    response = await client.get(f"/v1/live/sessions/{other['session_id']}/prompts",
                        headers={"X-Live-Session-Token": other["session_token"]}, params={"wait_ms": 100})
                    assert response.status_code == 429
                finally:
                    for task in polls:
                        task.cancel()
                    await asyncio.gather(*polls, return_exceptions=True)
                assert not app.state.live.waiters
    asyncio.run(run())


@pytest.mark.parametrize("changes", [
    {"input_mode": "real_capture"}, {"input_mode": None}, {"reliability": "guessed"},
    {"coverage": "full_platform_stream"}, {"text": " "}, {"text": "长" * 501},
    {"event_id": ""}, {"extra": "unexpected"},
])
def test_fixture_input_validation(tmp_path, changes):
    with TestClient(create_app(settings(tmp_path / "live.sqlite3"))) as client:
        session, headers = create(client)
        response = client.post(f"/v1/live/sessions/{session['session_id']}/comments",
                               headers=headers, json={**observation("bad"), **changes})
        assert response.status_code == 422


def test_received_time_is_captured_before_store_queue_delay(tmp_path):
    clock = Clock()
    app = create_app(settings(tmp_path / "live.sqlite3"), live_clock=clock)
    with TestClient(app) as client:
        session, headers = create(client)
        started, sid = clock.now, session["session_id"]
        original = app.state.live.run

        async def delayed(method, *args):
            if method == "add_comment":
                clock.now += 500
            return await original(method, *args)

        app.state.live.run = delayed
        first = post_comment(client, sid, headers, "first")
        assert first["received_at_ms"] == first["effective_at_ms"] == started
        assert first["time_basis"] == "received"
        second = post_comment(client, sid, headers, "second")
        assert second["received_at_ms"] == started + 500
        duplicate = post_comment(client, sid, headers, "first")
        assert duplicate["received_at_ms"] == started
        event = client.get(f"/v1/live/sessions/{sid}/events", headers=headers).json()["events"][0]
        assert event["first_received_at_ms"] == started
        assert event["last_received_at_ms"] == started + 500
        assert event["start_offset_ms"] == 0 and event["end_offset_ms"] == 500
