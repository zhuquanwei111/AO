"""Live-session observation contracts and conservative deterministic rules."""
from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .live_rules import COUNT_TREND_SAMPLES, INTERACTION_SPARSE_MAX, question


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)


class CommentContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=500)
    text_position: Position | None = None
    author_id: str | None = Field(default=None, min_length=1, max_length=128)
    platform_message_id: str | None = Field(default=None, min_length=1, max_length=128)


class OnlineCountContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    raw_text: str = Field(min_length=1, max_length=120)
    field_semantics_confirmed: bool = False


class InteractionContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    count: int = Field(ge=0, le=100_000)
    window_started_at_ms: int = Field(ge=0, le=2**63 - 1)
    window_ended_at_ms: int = Field(ge=0, le=2**63 - 1)
    coverage_complete: bool = False

    @model_validator(mode="after")
    def ordered_window(self):
        if self.window_ended_at_ms < self.window_started_at_ms:
            raise ValueError("interaction window end precedes start")
        return self


class TranscriptContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=500)
    segment_started_at_ms: int = Field(ge=0, le=2**63 - 1, strict=True)
    segment_ended_at_ms: int = Field(ge=0, le=2**63 - 1, strict=True)
    is_final: bool = False
    speaker_is_host: bool = False
    revision: int = Field(default=0, ge=0, le=100)
    supersedes_event_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def ordered_segment(self):
        if self.segment_ended_at_ms <= self.segment_started_at_ms:
            raise ValueError("transcript segment must have positive duration")
        return self


class PersonContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: Literal["head_down"]
    sample_count: int = Field(ge=5, le=100)


class PlatformNoticeContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=500)
    region_confirmed: bool = False
    explicit_warning_confirmed: bool = False


class QualityContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: Literal["audio_input_unclear", "video_input_unclear", "screen_capture_stale"]


class ObservationBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    observed_at_ms: int | None = Field(default=None, ge=0, le=2**63 - 1, strict=True)
    started_at_ms: int | None = Field(default=None, ge=0, le=2**63 - 1, strict=True)
    ended_at_ms: int | None = Field(default=None, ge=0, le=2**63 - 1, strict=True)
    availability: Literal["available", "missing", "unreliable"]
    coverage: str = Field(min_length=1, max_length=64)
    reliability: Literal["known", "unknown"]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.started_at_ms is not None and self.ended_at_ms is not None:
            if self.ended_at_ms < self.started_at_ms:
                raise ValueError("observation interval end precedes start")
        return self


class CommentObservation(ObservationBase):
    kind: Literal["comment"]
    content: CommentContent | None = None


class OnlineCountObservation(ObservationBase):
    kind: Literal["online_count"]
    content: OnlineCountContent | None = None


class InteractionObservation(ObservationBase):
    kind: Literal["interaction"]
    content: InteractionContent | None = None


class TranscriptObservation(ObservationBase):
    kind: Literal["transcript"]
    content: TranscriptContent | None = None


class PersonObservation(ObservationBase):
    kind: Literal["person_state"]
    content: PersonContent | None = None

    @model_validator(mode="after")
    def sustained_observation(self):
        if self.content is not None:
            end = self.ended_at_ms or self.observed_at_ms
            if self.started_at_ms is None or end is None or end - self.started_at_ms < 1_500:
                raise ValueError("person state requires a sustained observation interval")
        return self


class PlatformNoticeObservation(ObservationBase):
    kind: Literal["platform_notice"]
    content: PlatformNoticeContent | None = None


class QualityObservation(ObservationBase):
    kind: Literal["quality"]
    content: QualityContent | None = None


Observation = Annotated[Union[
    CommentObservation, OnlineCountObservation, InteractionObservation,
    TranscriptObservation, PersonObservation, PlatformNoticeObservation,
    QualityObservation,
], Field(discriminator="kind")]


def validate_observation_content(body: dict) -> tuple[bool, str | None]:
    """Check body state without converting missing/unreliable into numeric zero."""
    availability = body["availability"]
    content = body.get("content")
    if availability == "available" and content is None:
        return False, "content_missing"
    if availability != "available":
        return False, "source_" + availability
    if body["reliability"] != "known":
        return False, "unreliable"
    return True, None


_FIELD = re.compile(r"(?:当前|目前)?在线(?:人数)?\s*[:：]?\s*(?:约|≈|~)?\s*(\d+(?:\.\d+)?)\s*(万人|万|千|人|位)?")
_NOT_ONLINE_FIELDS = ("人气", "热度", "累计", "观看", "浏览", "点赞", "排名")


def parse_online_count(raw_text: str, *, field_semantics_confirmed: bool) -> int | None:
    """Parse only an explicitly confirmed live-online field; never guess from proxies."""
    if not field_semantics_confirmed:
        return None
    text = unicodedata.normalize("NFKC", raw_text).replace(",", "").replace("，", "")
    if any(marker in text for marker in _NOT_ONLINE_FIELDS):
        return None
    match = _FIELD.search(text)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    unit = match.group(2) or ""
    multiplier = 10_000 if unit in {"万", "万人"} else 1_000 if unit == "千" else 1
    result = round(value * multiplier)
    if not 0 <= result <= 100_000_000:
        return None
    return result


def _candidate(topic: str, priority: int, phenomenon: str, suggestion: str | None,
               selected: list[dict], *, model_text: str | None = None,
               model_context: str = "") -> dict:
    return {
        "topic": topic,
        "priority": priority,
        "phenomenon": phenomenon,
        "suggestion": suggestion,
        "model_text": model_text,
        "model_context": model_context,
        "observations": selected,
    }


def build_candidate(observations: list[dict]) -> dict | None:
    """Choose one explainable operation candidate from a sorted short window."""
    rows = sorted(observations, key=lambda item: item["effective_at_ms"])
    if not rows:
        return None

    for row in reversed(rows):
        if row["kind"] != "platform_notice":
            continue
        content = row["content"]
        if not content["region_confirmed"] or not content["explicit_warning_confirmed"]:
            continue
        wording = f"屏幕出现疑似提醒：‘{content['text'][:24]}’，请核对来源和原文后再处理。"
        phenomenon = "提醒区域出现待核验的屏幕文字候选"
        return _candidate("platform_notice", 100, phenomenon, wording, [row])

    for row in reversed(rows):
        if row["kind"] != "quality":
            continue
        signal = row["content"]["signal"]
        suggestions = {
            "audio_input_unclear": "当前音频采集样本不清晰，请核对采集输入；这不能说明观众端收音异常。",
            "video_input_unclear": "当前画面采集样本不清晰，请核对采集输入；这不能说明观众端画面异常。",
            "screen_capture_stale": "屏幕采集暂不连续，先检查采集状态，恢复后再判断直播内容。",
        }
        return _candidate("quality:" + signal, 80, "采集输入出现可检查的质量信号",
                          suggestions[signal], [row])

    comments = [row for row in rows if row["kind"] == "comment"]
    questions: dict[str, list[dict]] = {}
    for row in comments:
        parsed = question(row["content"]["text"])
        if parsed:
            questions.setdefault(parsed[0], []).append(row)
    person = next((row for row in reversed(rows)
                   if row["kind"] == "person_state" and row["content"]["signal"] == "head_down"), None)

    # 体验担忧是比一般趋势/节奏提示更急的候选，但只依据评论原文。
    for row in reversed(comments):
        text = row["content"]["text"]
        if any(mark in text for mark in ('“', '”', '‘', '’', '"', "'", "「", "」", "『", "』")):
            continue
        cue = next((phrase for phrase in ("听不清", "没声音", "卡顿", "画面黑") if phrase in text), None)
        if not cue:
            continue
        before = text[max(0, text.find(cue) - 4):text.find(cue)]
        if any(negation in before for negation in ("不是", "沒有", "没有", "并非", "别说")):
            continue
        return _candidate("comment_concern", 60, "一条可见评论表达了明确的体验担忧",
                          f"有条评论提到“{cue}”，可以先平和确认一下实际情况。", [row])

    counts_by_source: dict[str, list[tuple[int, dict]]] = {}
    for row in rows:
        if row["kind"] != "online_count":
            continue
        content = row["content"]
        value = row.get("normalized_count")
        if value is not None and content["field_semantics_confirmed"]:
            counts_by_source.setdefault(row["source"], []).append((value, row))
    falling: list[dict] | None = None
    for source_rows in counts_by_source.values():
        latest = sorted(source_rows, key=lambda pair: pair[1]["effective_at_ms"])[-COUNT_TREND_SAMPLES:]
        times = [pair[1]["effective_at_ms"] for pair in latest]
        if (len(latest) == 3 and latest[0][0] > latest[1][0] > latest[2][0]
                and times[0] < times[1] < times[2] and times[2] - times[0] >= 2_000):
            falling = [pair[1] for pair in latest]
            break

    interaction = next((row for row in reversed(rows)
                        if row["kind"] == "interaction"
                        and row["content"]["coverage_complete"]
                        and row["content"]["count"] <= INTERACTION_SPARSE_MAX), None)
    transcript_revisions: dict[tuple, dict] = {}
    for row in rows:
        if (row["kind"] != "transcript" or not row["content"]["is_final"]
                or not row["content"]["speaker_is_host"] or row["coverage"] != "continuous"):
            continue
        content = row["content"]
        segment_key = (row["source"], content["segment_started_at_ms"], content["segment_ended_at_ms"])
        current = transcript_revisions.get(segment_key)
        if current is None or content["revision"] > current["content"]["revision"]:
            transcript_revisions[segment_key] = row
    transcripts = sorted(transcript_revisions.values(), key=lambda row: row["effective_at_ms"])

    if falling and interaction and transcripts:
        selected = [*falling, interaction, transcripts[-1]]
        return _candidate("measured_engagement_decline", 55,
                          "客户端标注的在线字段连续回落，完整互动窗口较少且有近期主播转写",
                          "近期在线人数连续回落、已覆盖时段互动不多，先放慢节奏，留空接住当前话题。",
                          selected)
    if falling:
        return _candidate("measured_count_decline", 45,
                          "客户端标注的在线字段读数连续三次回落",
                          "在线人数连续回落，先放慢一点，看看下一轮互动再调整。", falling)

    if transcripts:
        selected = transcripts[-3:]
        if len(selected) == 3:
            start = min(row["content"]["segment_started_at_ms"] for row in selected)
            end = max(row["content"]["segment_ended_at_ms"] for row in selected)
            chars = sum(len("".join(row["content"]["text"].split())) for row in selected)
            if end - start >= 5_000 and chars / ((end - start) / 1000) > 7.0:
                return _candidate("speech_pace", 35, "近期连续主播转写的文字率偏快",
                                  "这段表达稍快，可以放慢一点，留出接话的空隙。", selected)
        ordered = sorted(selected, key=lambda row: row["content"]["segment_started_at_ms"])
        for left, right in zip(ordered, ordered[1:]):
            gap = right["content"]["segment_started_at_ms"] - left["content"]["segment_ended_at_ms"]
            if gap >= 5_000:
                return _candidate("speech_pause", 30, "连续转写窗口中出现较长停顿",
                                  "刚才停了一会儿，可以顺着当前话题补一句或接住公屏。",
                                  [left, right])

    for topic, topic_rows in questions.items():
        if person:
            return _candidate("person_question", 40,
                              "持续低头候选与近期可见问题同时出现",
                              "你刚才持续低头了一会儿，抬头看一下镜头，再接住这个问题。",
                              [person, topic_rows[-1]])
        if len(topic_rows) >= 2:
            label = question(topic_rows[-1]["content"]["text"])[1]
            return _candidate("repeated_question:" + topic, 38,
                              "近期可见公屏重复出现同一问题主题",
                              f"近期重复出现“{label}”的问题，可以集中回应一次，再邀请大家接着聊。",
                              topic_rows[-2:])

    for topic_rows in questions.values():
        row = topic_rows[-1]
        parsed = question(row["content"]["text"])
        if parsed:
            return _candidate("question:" + parsed[0], 25,
                              f"公屏出现一条可见问题：{parsed[1]}", None, [row],
                              model_text=row["content"]["text"])

    if person:
        return _candidate("person:head_down", 20, "画面中可见人物持续低头",
                          "抬头看一下镜头，接住观众的互动。", [person])
    return None

