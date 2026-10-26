"""M7 独立人物观察策略；不注册HTTP、不存媒体、不调用模型。

每个活动场次/采集段由集成者持有一个实例，start/stop 与源生命周期绑定。
仅消费 LivePersonObservation 的数值摘要。接公共路由/融合前不能称PiP已接通。
"""
from collections import deque
from dataclasses import dataclass, replace
from math import isfinite


@dataclass(frozen=True)
class PersonObservation:
    segment_id: str
    sequence: int
    observed_at_ms: int
    uptime_ms: int
    quality: str
    source: str = "ios_replaykit_vision"
    person_count: int | None = None
    confidence: float | None = None
    person_box: tuple[float, ...] | None = None  # 可靠人体框，左上归一化；脸框不代替。
    body_vector: tuple[float, ...] | None = None  # 鼻/肩6值；双腕可见时追加4值。
    face_vector: tuple[float, ...] | None = None  # 两眼、嘴的几何开合比例；不是情绪。

    @classmethod
    def from_payload(cls, payload: dict) -> "PersonObservation":
        """薄接线入口。只接受明确字段；不将客户端摘要认定为认证来源。"""
        keys = cls.__dataclass_fields__
        return cls(**{key: payload[key] for key in keys if key in payload})

    def valid(self) -> bool:
        def number(value, low, high):
            return (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and isfinite(value) and low <= value <= high)

        def vector(value, size, low, high):
            return value is None or (isinstance(value, (tuple, list)) and len(value) == size
                                     and all(number(x, low, high) for x in value))

        return (isinstance(self.segment_id, str) and 0 < len(self.segment_id) <= 128
                and all(type(x) is int and 0 <= x <= 2**53 for x in
                        (self.sequence, self.observed_at_ms, self.uptime_ms))
                and isinstance(self.quality, str) and len(self.quality) <= 64
                and self.source in {"ios_replaykit_vision", "android_vision_person"}
                and (self.person_count is None or type(self.person_count) is int
                     and 0 <= self.person_count <= 10)
                and (self.confidence is None or number(self.confidence, 0, 1))
                and vector(self.person_box, 4, 0, 1)
                and (self.person_box is None or self.person_box[2] >= 0.03 and self.person_box[3] >= 0.03
                     and self.person_box[0] + self.person_box[2] <= 1.001
                     and self.person_box[1] + self.person_box[3] <= 1.001)
                and (vector(self.body_vector, 6, -5, 5) or vector(self.body_vector, 10, -5, 5))
                and vector(self.face_vector, 3, 0, 2))


@dataclass(frozen=True)
class PersonCandidate:
    kind: str
    started_at_ms: int
    observed_at_ms: int
    sample_count: int
    confidence: float | None
    phenomenon: str
    suggestion: str
    source: str = "ios_replaykit_vision"
    coverage: str = "sampled_person_roi"


@dataclass(frozen=True)
class PersonDecision:
    presence: str
    motion: str = "unknown"
    facial_change: str = "unknown"
    candidate: PersonCandidate | None = None


class PersonPolicy:
    """3秒采样的保守策略：最多21个数值观测，零媒体/跨场历史。"""
    def __init__(self):
        self.segment_id = None
        self.history = deque(maxlen=21)
        self._last = None
        self._last_prompt_uptime = None
        self._clear_evidence()

    def _clear_evidence(self):
        self.history.clear()
        self._visible_box = None
        self._exit_at = None
        self._absence_start = None
        self._absence_count = 0

    def start(self, segment_id: str):
        if not isinstance(segment_id, str) or not 0 < len(segment_id) <= 128:
            raise ValueError("invalid_segment")
        self.segment_id = segment_id
        self._last = None
        self._last_prompt_uptime = None
        self._clear_evidence()

    def stop(self):
        self.segment_id = None
        self._last = None
        self._last_prompt_uptime = None
        self._clear_evidence()

    @staticmethod
    def _delta(a, b):
        return max(abs(x - y) for x, y in zip(a, b))

    def _change(self, field, threshold):
        values = [getattr(item, field) for item in self.history]
        # 缺关键点不是零动作；不能跨缺口比较。
        if (len(values) < 2 or values[-1] is None or values[-2] is None
                or len(values[-1]) != len(values[-2])):
            return "unknown"
        if self._delta(values[-1], values[-2]) >= threshold:
            return "changed"
        if (len(values) >= 8 and all(value is not None and len(value) == len(values[-1]) for value in values[-8:])
                and self.history[-1].uptime_ms - self.history[-8].uptime_ms >= 21_000
                and all(self._delta(values[-1], value) < threshold for value in values[-8:])):
            return "little_change"
        return "insufficient"

    def observe(self, item: PersonObservation) -> PersonDecision:
        if self.segment_id is None:
            return PersonDecision("inactive")
        if item.segment_id != self.segment_id:
            return PersonDecision("ignored")
        if not item.valid():
            self._clear_evidence()
            return PersonDecision("ignored")
        # JSON列表归一成不可变数值元组，不让调用方事后改写历史观测。
        item = replace(item, **{field: tuple(getattr(item, field)) if getattr(item, field) is not None else None
                                for field in ("person_box", "body_vector", "face_vector")})
        if self._last is not None:
            if item.sequence <= self._last.sequence or item.uptime_ms <= self._last.uptime_ms:
                return PersonDecision("ignored")
            if (item.uptime_ms - self._last.uptime_ms > 6_500
                    or item.observed_at_ms <= self._last.observed_at_ms
                    or abs((item.observed_at_ms - self._last.observed_at_ms)
                           - (item.uptime_ms - self._last.uptime_ms)) > 2_000):
                self._clear_evidence()
        self._last = item
        if (item.quality != "valid" or item.person_count not in (0, 1)
                or item.person_count == 1 and (item.confidence is None or item.confidence < 0.65)):
            self._clear_evidence()
            return PersonDecision("unknown")

        if item.person_count == 1:
            self._absence_start = None
            self._absence_count = 0
            # 必须见到可靠人体框向横向边缘移出；脸框或中央突然未检出不算。
            previous = self._visible_box
            box = item.person_box
            self._exit_at = None
            if previous is not None and box is not None:
                old_center = previous[0] + previous[2] / 2
                center = box[0] + box[2] / 2
                if ((box[0] + box[2] >= 0.96 and center - old_center >= 0.10)
                        or (box[0] <= 0.04 and old_center - center >= 0.10)):
                    self._exit_at = item.uptime_ms
            self._visible_box = box
            self.history.append(item)
            return PersonDecision("visible", self._change("body_vector", 0.12),
                                  self._change("face_vector", 0.10))

        self.history.clear()
        if self._exit_at is None or item.uptime_ms - self._exit_at > 22_000:
            self._absence_start = None
            self._absence_count = 0
            return PersonDecision("unknown")
        if self._absence_start is None:
            self._absence_start = item
        self._absence_count = min(self._absence_count + 1, 100)
        if (self._absence_count < 6 or item.uptime_ms - self._absence_start.uptime_ms < 15_000
                or self._last_prompt_uptime is not None
                and item.uptime_ms - self._last_prompt_uptime < 90_000):
            return PersonDecision("not_visible_candidate_pending")
        candidate = PersonCandidate(
            "person_not_visible_candidate", self._absence_start.observed_at_ms,
            item.observed_at_ms, self._absence_count, None,
            "采样画面里持续暂未看到人物（候选）", "画面里暂未看到你，确认一下镜头位置。",
            source=item.source)
        self._last_prompt_uptime = item.uptime_ms
        self._exit_at = None  # 新的退出证据前不重复提醒。
        return PersonDecision("not_visible_candidate", candidate=candidate)
