"""真实策略状态机的受控时序测试，不代表 Vision 真人准确率。"""
from dataclasses import replace

import pytest

from screen_assistant_server.person_observation import PersonObservation, PersonPolicy


def sample(seq, *, segment="a", people=1, quality="valid", box=(0.3, 0.1, 0.4, 0.8),
           body=(0.0, 0.8, -0.5, 0.0, 0.5, 0.0), face=(0.2, 0.2, 0.1)):
    return PersonObservation(segment, seq, 1_000 + seq * 3_000, seq * 3_000,
                             quality, people, 0.8 if people else None,
                             box if people else None, body if people else None,
                             face if people else None)


def exited():
    policy = PersonPolicy()
    policy.start("a")
    policy.observe(sample(0))
    policy.observe(sample(1, box=(0.65, 0.1, 0.34, 0.8)))
    return policy


def test_no_person_without_prior_exit_is_unknown_and_never_prompts():
    policy = PersonPolicy()
    policy.start("a")
    for i in range(40):
        result = policy.observe(sample(i, people=0))
        assert result.candidate is None
        assert result.presence == "unknown"


def test_short_absence_is_not_a_candidate_and_continuous_absence_is_cautious():
    policy = exited()
    for i in range(2, 7):
        assert policy.observe(sample(i, people=0)).candidate is None
    result = policy.observe(sample(7, people=0))
    candidate = result.candidate
    assert candidate.kind == "person_not_visible_candidate"
    assert candidate.started_at_ms == 7_000
    assert candidate.observed_at_ms == 22_000
    assert candidate.sample_count == 6
    assert candidate.confidence is None  # 没有“无人”的模型概率，不能填0.8。
    assert "暂未看到" in candidate.phenomenon
    assert "确认" in candidate.suggestion


@pytest.mark.parametrize("reason", ["near_black", "frozen_or_still", "low_detail", "scene_change",
                                    "occluded", "vision_error", "unknown"])
def test_unusable_frame_breaks_absence_and_does_not_reuse_exit(reason):
    policy = exited()
    policy.observe(sample(2, people=0))
    assert policy.observe(sample(3, people=0, quality=reason)).candidate is None
    for i in range(4, 15):
        assert policy.observe(sample(i, people=0)).candidate is None


def test_center_disappearance_cannot_be_called_exit_or_occlusion():
    policy = PersonPolicy()
    policy.start("a")
    policy.observe(sample(0))
    for i in range(1, 15):
        assert policy.observe(sample(i, people=0)).candidate is None


def test_return_resets_absence_and_no_mechanical_prompt_for_quiet_chat():
    policy = exited()
    policy.observe(sample(2, people=0))
    for i in range(3, 60):
        result = policy.observe(sample(i))
        assert result.candidate is None
    assert result.motion == "little_change"
    assert result.facial_change == "little_change"
    assert len(policy.history) <= 21


def test_actual_landmark_change_is_observed_without_emotion_or_action_prompt():
    policy = PersonPolicy()
    policy.start("a")
    policy.observe(sample(0))
    result = policy.observe(sample(1, body=(0, 1.0, -0.5, 0, 0.5, 0), face=(0.2, 0.2, 0.5)))
    assert result.motion == "changed"
    assert result.facial_change == "changed"
    assert result.candidate is None


def test_unavailable_landmarks_remain_unknown_not_zero_motion():
    policy = PersonPolicy()
    policy.start("a")
    for i in range(10):
        result = policy.observe(sample(i, body=None, face=None))
    assert result.motion == "unknown"
    assert result.facial_change == "unknown"


def test_stop_old_segment_late_and_out_of_order_samples_are_ignored():
    policy = exited()
    policy.stop()
    assert policy.observe(sample(2, people=0)).presence == "inactive"
    policy.start("b")
    policy.observe(sample(0, segment="b"))
    assert policy.observe(sample(12, people=0)).presence == "ignored"
    assert policy.observe(sample(0, segment="b")).presence == "ignored"
    assert policy.observe(sample(1, segment="b")).presence == "visible"


def test_gap_or_clock_rollback_resets_without_stitching_absence():
    policy = exited()
    policy.observe(sample(2, people=0))
    for i in range(8, 20):
        assert policy.observe(sample(i, people=0)).candidate is None
    policy = exited()
    policy.observe(replace(sample(2), observed_at_ms=500))
    for i in range(3, 15):
        assert policy.observe(sample(i, people=0)).candidate is None


def test_low_confidence_or_multiple_people_are_unknown():
    policy = exited()
    assert policy.observe(replace(sample(2), confidence=0.1)).presence == "unknown"
    assert policy.observe(sample(3, people=2)).presence == "unknown"


def test_absence_cooldown_is_bounded_and_independent_of_normal_observations():
    policy = exited()
    events = [policy.observe(sample(i, people=0)).candidate for i in range(2, 60)]
    assert len([event for event in events if event]) == 1  # 无新退出证据不反复催。


@pytest.mark.parametrize("field,value", [("sequence", True), ("uptime_ms", float("nan")),
                                        ("confidence", 2), ("body_vector", (float("inf"),) * 6),
                                        ("person_box", (-1, 0, 1, 1))])
def test_malformed_numeric_evidence_is_rejected_without_advancing_state(field, value):
    policy = exited()
    invalid = replace(sample(2), **{field: value})
    assert policy.observe(invalid).presence == "ignored"
    assert policy.observe(sample(2)).presence == "visible"


def test_ios_numeric_payload_adapter_has_no_media_or_false_confidence_requirement():
    payload = {"segment_id": "a", "sequence": 1, "observed_at_ms": 4000,
               "uptime_ms": 3000, "quality": "valid", "person_count": 0,
               "confidence": None, "head_ratio": None, "pts_seconds": 3.0}
    value = PersonObservation.from_payload(payload)
    assert value.valid() and value.face_vector is None
    policy = PersonPolicy()
    policy.start("a")
    assert policy.observe(value).presence == "unknown"


def test_one_frame_change_and_new_exit_during_cooldown_do_not_prompt():
    policy = exited()
    for i in range(2, 8):
        result = policy.observe(sample(i, people=0))
    assert result.candidate is not None
    policy.observe(sample(8))
    policy.observe(sample(9, box=(0.65, 0.1, 0.34, 0.8)))
    for i in range(10, 16):
        assert policy.observe(sample(i, people=0)).candidate is None


def test_empty_or_tiny_person_box_does_not_manufacture_exit_evidence():
    policy = PersonPolicy()
    policy.start("a")
    policy.observe(sample(0))
    policy.observe(sample(1, box=(1, 0, 0, 0)))
    for i in range(2, 8):
        assert policy.observe(sample(i, people=0)).candidate is None


def test_wrist_visibility_changes_do_not_become_motion_and_visible_wrists_can_change():
    policy = PersonPolicy()
    policy.start("a")
    policy.observe(sample(0))
    body = (0.0, 0.8, -0.5, 0.0, 0.5, 0.0, -0.7, -0.5, 0.7, -0.5)
    result = policy.observe(sample(1, body=body))
    assert result.motion == "unknown"  # 可见性改变，不拿6值和10值硬比较。
    result = policy.observe(sample(2, body=body[:-1] + (0.5,)))
    assert result.motion == "changed" and result.candidate is None


def test_mutable_json_vectors_cannot_rewrite_accepted_history():
    policy = PersonPolicy()
    policy.start("a")
    body = [0.0, 0.8, -0.5, 0.0, 0.5, 0.0]
    policy.observe(sample(0, body=body))
    body[1] = 2.0
    assert policy.history[0].body_vector[1] == 0.8


def test_legacy_signal_contract_is_preserved_and_new_candidate_is_not_disguised():
    from pydantic import ValidationError
    from screen_assistant_server.live import PersonSignal

    payload = {"event_id": "person-test", "source": "ios_replaykit_vision", "signal": "head_down",
               "confidence": 0.6, "sample_count": 6, "started_at_ms": 10000, "observed_at_ms": 25000}
    assert PersonSignal.model_validate(payload).signal == "head_down"
    with pytest.raises(ValidationError):
        PersonSignal.model_validate({**payload, "signal": "person_not_visible_candidate"})
    with pytest.raises(ValidationError):
        PersonSignal.model_validate({**payload, "confidence": 0.49})


def test_new_exit_after_cooldown_can_produce_a_new_bounded_candidate():
    policy = exited()
    for i in range(2, 8):
        result = policy.observe(sample(i, people=0))
    assert result.candidate is not None
    policy.observe(sample(32))
    policy.observe(sample(33, box=(0.65, 0.1, 0.34, 0.8)))
    for i in range(34, 40):
        result = policy.observe(sample(i, people=0))
    assert result.candidate is not None
