import pytest
from pydantic import TypeAdapter, ValidationError

from screen_assistant_server.live_fusion import (
    Observation,
    build_candidate,
    parse_online_count,
    validate_observation_content,
)


def test_confirmed_online_count_units_and_proxy_rejection():
    assert parse_online_count("在线人数 1.2万人", field_semantics_confirmed=True) == 12_000
    assert parse_online_count("当前在线：3.4万", field_semantics_confirmed=True) == 34_000
    assert parse_online_count("在线 1,234人", field_semantics_confirmed=True) == 1234
    assert parse_online_count("在线人数约1.2万人左右", field_semantics_confirmed=True) == 12000
    assert parse_online_count("人气 3.4万", field_semantics_confirmed=True) is None
    assert parse_online_count("累计观看 8.1万", field_semantics_confirmed=True) is None
    assert parse_online_count("在线人数 999", field_semantics_confirmed=False) is None


def test_typed_observation_keeps_missing_unreliable_and_confidence_unknown_distinct():
    adapter = TypeAdapter(Observation)
    missing = adapter.validate_python({
        "event_id": "missing-1", "kind": "online_count", "source": "ios_screen_roi",
        "observed_at_ms": 1000, "availability": "missing", "coverage": "dedicated_region",
        "reliability": "unknown", "confidence": None, "content": None,
    })
    assert missing.kind == "online_count"
    assert validate_observation_content(missing.model_dump()) == (False, "source_missing")

    available = adapter.validate_python({
        "event_id": "count-1", "kind": "online_count", "source": "fixture:confirmed-count",
        "observed_at_ms": 1000, "availability": "available", "coverage": "dedicated_region",
        "reliability": "known", "content": {"raw_text": "在线 20人", "field_semantics_confirmed": True},
    })
    assert available.confidence is None
    assert parse_online_count(available.content.raw_text,
                              field_semantics_confirmed=available.content.field_semantics_confirmed) == 20


def test_typed_observation_rejects_mismatched_content_and_client_received_time():
    adapter = TypeAdapter(Observation)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "event_id": "bad-kind", "kind": "online_count", "source": "fixture",
            "availability": "available", "coverage": "dedicated_region", "reliability": "known",
            "content": {"text": "在线 2万"},
        })
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "event_id": "bad-received-time", "kind": "comment", "source": "fixture",
            "availability": "available", "coverage": "partial", "reliability": "known",
            "content": {"text": "你哪里人？"}, "received_at_ms": 123,
        })


def test_transcript_revision_is_one_segment_and_speech_pace_is_template_based():
    def transcript(event_id, start, end, revision, text):
        return {
            "kind": "transcript", "event_id": event_id, "source": "fixture:host_asr",
            "effective_at_ms": end, "received_at_ms": end,
            "observed_at_ms": end, "started_at_ms": start, "ended_at_ms": end,
            "availability": "available", "coverage": "continuous", "reliability": "known",
            "confidence": None, "normalized_count": None,
            "content": {"text": text, "segment_started_at_ms": start,
                        "segment_ended_at_ms": end, "is_final": True,
                        "speaker_is_host": True, "revision": revision,
                        "supersedes_event_id": None},
        }

    rows = [
        transcript("segment-1", 0, 2_000, 0, "刚才我们聊聊这个话题欢迎大家参与"),
        transcript("segment-1-revision", 0, 2_000, 1, "刚才我们聊聊这个话题欢迎大家参与"),
        transcript("segment-2", 2_000, 4_000, 0, "今天我们接着聊聊生活里的小故事"),
        transcript("segment-3", 4_000, 6_000, 0, "你们也可以说说自己的想法和感受"),
    ]
    candidate = build_candidate(rows)
    assert candidate is not None and candidate["topic"] == "speech_pace"
    assert len(candidate["observations"]) == 3


def test_explicit_comment_concern_is_cautious_and_quotes_are_not_live_claims():
    def comment(event_id, text):
        return {
            "kind": "comment", "event_id": event_id, "source": "fixture:ocr_comment",
            "effective_at_ms": 1000, "received_at_ms": 1000,
            "observed_at_ms": 1000, "availability": "available",
            "coverage": "ocr_visible_sample", "reliability": "known",
            "confidence": None, "normalized_count": None,
            "content": {"text": text, "text_position": None,
                        "author_id": None, "platform_message_id": None},
        }

    candidate = build_candidate([comment("concern", "我这边听不清")])
    assert candidate is not None and candidate["topic"] == "comment_concern"
    assert "确认一下实际情况" in candidate["suggestion"]
    assert build_candidate([comment("quote", "主播提到‘听不清’这个词")]) is None
    assert build_candidate([comment("negated", "不是没声音")]) is None
