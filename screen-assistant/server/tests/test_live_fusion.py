import pytest
from pydantic import TypeAdapter, ValidationError

from screen_assistant_server.live_fusion import (
    Observation,
    build_candidate,
    parse_online_count,
    validate_observation_content,
)


def test_comment_eligibility_level_boundaries_and_unknown():
    from screen_assistant_server.live_fusion import comment_is_candidate
    assert comment_is_candidate("👍", consumption_level=30, fan_level=None)
    assert comment_is_candidate("哈哈", consumption_level=None, fan_level=8)
    assert not comment_is_candidate("👍", consumption_level=29, fan_level=7)
    assert not comment_is_candidate("哈哈", consumption_level=None, fan_level=None)
    assert not comment_is_candidate("1", consumption_level=None, fan_level=None)
    assert comment_is_candidate("这个颜色很漂亮", consumption_level=None, fan_level=None)
    assert comment_is_candidate("这款还有吗？", consumption_level=0, fan_level=0)
    assert not comment_is_candidate("", consumption_level=30, fan_level=8)
    assert not comment_is_candidate("👍", consumption_level=True, fan_level=False)


def test_group_feedback_requires_distinct_named_messages_within_window():
    from screen_assistant_server.live_fusion import group_feedback
    now = 100_000
    def row(comment_id, nickname, text, seen):
        return {"comment_id": comment_id, "nickname": nickname, "text": text,
                "last_seen_at_ms": seen}
    one = row("c1", "小明", "有点卡", now)
    assert group_feedback([one, one], now) is None
    assert group_feedback([one, row("c2", None, "有点卡", now)], now) is None
    assert group_feedback([one, row("c2", "小明", "有点卡", now)], now) is None
    assert group_feedback([one, row("c2", "小红", "有点卡", now - 10_001)], now) is None
    result = group_feedback([one, row("c2", "小红", "有点卡", now - 10_000)], now)
    assert result is not None
    assert {item["comment_id"] for item in result} == {"c1", "c2"}
    assert group_feedback([one, row("c2", "小红", "很流畅", now)], now) is None


def test_legacy_repeated_ocr_without_distinct_names_is_not_group_feedback():
    def row(event_id):
        return {"kind": "comment", "event_id": event_id, "source": "ios_replaykit_ocr",
                "effective_at_ms": 1000, "received_at_ms": 1000, "observed_at_ms": 1000,
                "availability": "available", "coverage": "ocr_visible_sample", "reliability": "known",
                "confidence": None, "content": {"text": "主播你是哪里人？", "text_position": None}}
    candidate = build_candidate([row("e1"), row("e2")])
    assert candidate is not None
    assert candidate["topic"].startswith("question:")
    assert candidate["model_text"] == "主播你是哪里人？"
    assert len(candidate["observations"]) == 1


def test_confirmed_online_count_units_and_proxy_rejection():
    assert parse_online_count("在线人数 1.2万人", field_semantics_confirmed=True) == 12_000
    assert parse_online_count("当前在线：3.4万", field_semantics_confirmed=True) == 34_000
    assert parse_online_count("在线 1,234人", field_semantics_confirmed=True) == 1234
    assert parse_online_count("在线人数约1.2万人左右", field_semantics_confirmed=True) == 12000
    assert parse_online_count("人气 3.4万", field_semantics_confirmed=True) is None
    assert parse_online_count("累计观看 8.1万", field_semantics_confirmed=True) is None
    assert parse_online_count("在线人数 999", field_semantics_confirmed=False) is None


def test_user_selected_count_region_accepts_bare_digits_and_marks_approximate_units():
    assert parse_online_count("1,260", field_semantics_confirmed=True) == 1260
    assert parse_online_count("1.2万", field_semantics_confirmed=True) == 12000
    assert parse_online_count("约 0.8千", field_semantics_confirmed=True) == 800
    assert parse_online_count("热度 1.2万", field_semantics_confirmed=True) is None
    assert parse_online_count("累计观看 2600", field_semantics_confirmed=True) is None


def test_typed_observation_keeps_missing_unreliable_and_confidence_unknown_distinct():
    adapter = TypeAdapter(Observation)
    missing = adapter.validate_python({
        "event_id": "missing-1", "kind": "online_count", "source": "ios_replaykit_online_count_roi",
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


def audio_transcript(index, start, end, *, source='ios_replaykit_audioMic_asr', continuity='one'):
    return dict(kind='transcript', event_id=f'event-{index}', source=source,
        effective_at_ms=end, received_at_ms=end, observed_at_ms=end,
        started_at_ms=start, ended_at_ms=end, availability='available',
        reliability='known', coverage='continuous', content=dict(
            text='欢迎一起聊聊今天的生活故事和想法'*3,
            segment_started_at_ms=start, segment_ended_at_ms=end, is_final=True,
            speaker_is_host=True, revision=0, supersedes_event_id=None,
            audio_evidence=dict(segment_id=f'segment-{index}', continuity_id=continuity,
                speech_validated=True, time_basis='pts_arrival_approx',
                coverage_started_at_ms=0, coverage_ended_at_ms=end)))


def test_audio_pace_never_combines_sources_or_gaps_or_unverified_voice():
    rows = [audio_transcript(i, i*2000, (i+1)*2000) for i in range(3)]
    assert build_candidate(rows)['topic'] == 'speech_pace'
    rows[0]['source'] = 'ios_replaykit_audioApp_asr'
    assert build_candidate(rows) is None
    rows[0]['source'] = rows[1]['source']
    rows[0]['content']['audio_evidence']['continuity_id'] = 'before-gap'
    assert build_candidate(rows) is None
    for row in rows:
        row['content'].pop('audio_evidence')
    assert build_candidate(rows) is None


def test_revision_with_changed_end_replaces_one_segment_and_pause_requires_covered_gap():
    rows = [audio_transcript('left', 0, 1000), audio_transcript('right', 7000, 8000)]
    assert build_candidate(rows)['topic'] == 'speech_pause'
    revised = audio_transcript('revision', 7000, 8200)
    revised['content'].update(revision=1, supersedes_event_id=rows[1]['event_id'])
    revised['content']['audio_evidence']['segment_id'] = rows[1]['content']['audio_evidence']['segment_id']
    result = build_candidate([*rows, revised])
    assert len(result['observations']) == 2
    assert result['observations'][-1]['event_id'] == 'event-revision'
    revised['content']['audio_evidence']['coverage_started_at_ms'] = 7000
    assert build_candidate([rows[0], revised]) is None
    revised['content']['audio_evidence']['coverage_started_at_ms'] = 0
    revised['content']['audio_evidence']['continuity_id'] = 'after-gap'
    assert build_candidate([rows[0], revised]) is None
