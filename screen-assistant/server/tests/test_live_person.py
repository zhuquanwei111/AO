from __future__ import annotations

import pytest
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
        redis_url="redis://127.0.0.1:1/0", tianyi_api_key="",
        tianyi_base_url="http://model.invalid", tianyi_model="unused",
        dedup_ttl_seconds=86_400, max_sentence_chars=2_000,
        model_timeout_seconds=1, model_connect_timeout_seconds=1,
        live_db_path=str(path),
    )


def create(client):
    response = client.post("/v1/live/sessions", json={})
    assert response.status_code == 201, response.text
    data = response.json()
    return data, {"X-Live-Session-Token": data["session_token"]}


def sample(seq, observed_at, uptime, *, segment="segment-a", people=1,
           box=(0.3, 0.1, 0.4, 0.8), quality="valid", body_vector=None,
           face_vector=None, source="ios_replaykit_vision"):
    return {
        "source": source, "coverage": "sampled_person_roi",
        "segment_id": segment, "sequence": seq,
        "observed_at_ms": observed_at, "uptime_ms": uptime,
        "quality": quality, "person_count": people,
        "confidence": 0.8 if people == 1 else None,
        "person_box": box if people == 1 else None,
        "body_vector": body_vector or [0.0, 0.8, -0.5, 0.0, 0.5, 0.0],
        "face_vector": face_vector or [0.2, 0.2, 0.1],
    }


def test_person_candidate_flows_through_m4_prompt_events_and_summary(tmp_path):
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "person.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}"

        first = client.post(url + "/person-observations", headers=headers,
                            json=sample(0, clock.now, 10_000))
        assert first.status_code == 200, first.text
        assert first.json()["presence"] == "visible"
        clock.now += 3_000
        edge = client.post(url + "/person-observations", headers=headers,
                           json=sample(1, clock.now, 13_000, box=(0.7, 0.1, 0.3, 0.8)))
        assert edge.status_code == 200, edge.text
        for seq in range(2, 8):
            clock.now += 3_000
            response = client.post(url + "/person-observations", headers=headers,
                                   json=sample(seq, clock.now, 10_000 + seq * 3_000, people=0))
            assert response.status_code == 200, response.text

        result = response.json()
        assert result["reason"] == "generated"
        page = client.get(url + "/prompts", headers=headers).json()
        assert len(page["prompts"]) == 1
        event = page["prompts"][0]
        assert event["phenomenon"] == "采样画面里持续暂未看到人物（候选）"
        assert event["suggestion"] == "画面里暂未看到你，确认一下镜头位置。"
        assert event["sources"][0]["source_verification"] == "unverified_client_source"
        assert event["sources"][0].get("confidence") is None
        assert "head_down" not in event["phenomenon"]
        events = client.get(url + "/events", headers=headers).json()["events"]
        assert len(events) == 1 and events[0]["prompt_id"] == event["prompt_id"]
        summary = client.get(url + "/summary", headers=headers).json()
        assert summary["session_id"] == session["session_id"]
        assert summary["online_count"]["status"] == "unknown"


@pytest.mark.parametrize("person_source", [
    "android_vision_person",
    "android_mediaprojection_mlkit_pose",
])
def test_android_person_adapter_reuses_m7_policy_and_preserves_source(tmp_path, person_source):
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "android-person.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}"
        started = client.post(url + "/start", headers=headers,
                              json={"source": "android_app"})
        assert started.status_code == 200, started.text
        assert client.post(url + "/person-observations", headers=headers,
                           json=sample(0, clock.now, 10_000, source=person_source)).status_code == 200
        clock.now += 3_000
        edge = sample(1, clock.now, 13_000, box=(0.7, 0.1, 0.3, 0.8),
                      source=person_source)
        assert client.post(url + "/person-observations", headers=headers, json=edge).status_code == 200
        result = None
        for seq in range(2, 8):
            clock.now += 3_000
            result = client.post(url + "/person-observations", headers=headers,
                                 json=sample(seq, clock.now, 10_000 + seq * 3_000,
                                             people=0, source=person_source))
        assert result.status_code == 200
        assert result.json()["reason"] == "generated"
        events = client.get(url + "/events", headers=headers).json()["events"]
        assert len(events) == 1
        assert events[0]["sources"][0]["source"] == person_source


def test_person_observation_auth_validation_replay_segment_and_stop_isolation(tmp_path):
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "person-isolation.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}"
        body = sample(0, clock.now, 10_000)
        assert client.post(url + "/person-observations", json=body).status_code == 403
        assert client.post(url + "/person-observations", headers=headers,
                           json={**body, "source": "untrusted"}).status_code == 422
        assert client.post(url + "/person-observations", headers=headers,
                           json={**body, "body_vector": [0.0, 1.0]}).status_code == 422

        first = client.post(url + "/person-observations", headers=headers, json=body)
        assert first.status_code == 200
        duplicate = client.post(url + "/person-observations", headers=headers, json=body)
        assert duplicate.status_code == 200 and duplicate.json()["reason"] == "duplicate_event"

        older_segment = sample(0, clock.now, 9_999, segment="old-segment")
        old = client.post(url + "/person-observations", headers=headers, json=older_segment)
        assert old.status_code == 200 and old.json()["reason"] == "old_segment"
        clock.now += 3_000
        new_segment = sample(0, clock.now, 13_000, segment="new-segment")
        assert client.post(url + "/person-observations", headers=headers,
                           json=new_segment).status_code == 200
        late_old = sample(1, clock.now + 1, 14_000, segment="segment-a")
        assert client.post(url + "/person-observations", headers=headers,
                           json=late_old).json()["reason"] == "old_segment"

        assert client.post(url + "/stop", headers=headers).status_code == 200
        stopped = client.post(url + "/person-observations", headers=headers,
                              json=sample(1, clock.now + 3_000, 16_000, segment="new-segment"))
        assert stopped.status_code == 409


def test_motion_and_facial_change_are_observations_without_coaching_prompt(tmp_path):
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "person-motion.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}"
        base = sample(0, clock.now, 10_000, body_vector=[0, 0, 0, 0, 0, 0],
                      face_vector=[0.2, 0.2, 0.1])
        first = client.post(url + "/person-observations", headers=headers, json=base)
        assert first.status_code == 200
        clock.now += 3_000
        changed = sample(1, clock.now, 13_000, body_vector=[0.2, 0, 0, 0, 0, 0],
                         face_vector=[0.2, 0.2, 0.4])
        second = client.post(url + "/person-observations", headers=headers, json=changed)
        assert second.status_code == 200
        assert second.json()["motion"] == "changed"
        assert second.json()["facial_change"] == "changed"
        assert client.get(url + "/prompts", headers=headers).json()["prompts"] == []
        assert client.get(url + "/events", headers=headers).json()["events"] == []


def test_legacy_head_down_and_new_person_candidate_share_cooldown(tmp_path):
    clock = Clock()
    with TestClient(create_app(settings(tmp_path / "person-cooldown.sqlite3"), live_clock=clock)) as client:
        session, headers = create(client)
        url = f"/v1/live/sessions/{session['session_id']}"
        clock.now += 20_000
        legacy = {
            "event_id": "legacy-head-down", "source": "ios_replaykit_vision",
            "signal": "head_down", "confidence": 0.8, "sample_count": 6,
            "started_at_ms": session["started_at_ms"], "observed_at_ms": clock.now,
        }
        assert client.post(url + "/signals", headers=headers, json=legacy).json()["reason"] == "generated"
        # 70秒后新候选仍需服从旧head_down出口的90秒冷却。
        clock.now += 49_000
        sequence = 0
        payload = sample(sequence, clock.now, 100_000)
        assert client.post(url + "/person-observations", headers=headers, json=payload).status_code == 200
        sequence += 1
        clock.now += 3_000
        assert client.post(url + "/person-observations", headers=headers,
                           json=sample(sequence, clock.now, 103_000,
                                       box=(0.7, 0.1, 0.3, 0.8))).status_code == 200
        for sequence in range(2, 8):
            clock.now += 3_000
            candidate = client.post(url + "/person-observations", headers=headers,
                                    json=sample(sequence, clock.now, 100_000 + sequence * 3_000,
                                                people=0))
        assert candidate.status_code == 200
        assert candidate.json()["reason"] == "cooldown"
        events = client.get(url + "/events", headers=headers).json()["events"]
        assert len(events) == 1 and events[0]["phenomenon"] == "画面中可见人物持续低头"
