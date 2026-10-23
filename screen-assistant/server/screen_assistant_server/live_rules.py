"""M2 可解释默认值与小范围问句归一化；不作模型/语义相似度推断。"""
from __future__ import annotations

import re
import unicodedata

WINDOW_MS = 30_000
MAX_OBSERVATION_AGE_MS = 10_000
COOLDOWN_MS = 60_000
PROMPT_TTL_MS = 15_000
MAX_WINDOW_OBSERVATIONS = 200
MAX_OBSERVATIONS = 10_000
MAX_EVENTS = 1_000
MAX_PENDING = 16
MAX_WAIT_MS = 15_000
MAX_WAITERS = 16
MAX_SESSION_WAITERS = 4

_PREFIX = r"(?:请问)?(?:主播)?(?:你|您)?"
_SUFFIX = r"(?:呀|啊|呢|吗)?"
_HOMETOWN = re.compile(_PREFIX + r"(?:是)?(?:哪里人|哪儿人)" + _SUFFIX)
_FROM = re.compile(_PREFIX + r"(?:来自|老家在)(?:哪里|哪儿)" + _SUFFIX)
_SCHEDULE = re.compile(_PREFIX + r"(?:几点开播|什么时候开播)" + _SUFFIX)


def question(text: str) -> tuple[str, str] | None:
    """只允许已列出的同义问句；不删除否定、地名或数字来追求召回。"""
    normalized = unicodedata.normalize("NFKC", text)
    compact = "".join(char for char in normalized if not char.isspace())
    compact = compact.rstrip("?!。!~～")
    if _HOMETOWN.fullmatch(compact) or _FROM.fullmatch(compact):
        return "hometown", "来自哪里"
    if _SCHEDULE.fullmatch(compact):
        return "schedule", "几点开播"
    if not normalized.rstrip().endswith(("?", "？")) and not compact.endswith(("吗", "呢", "么")):
        return None
    if not any(word in compact for word in ("什么", "怎么", "哪里", "哪儿", "几点", "多少", "为何", "吗")):
        return None
    # 未覆盖的问句保留完整文本作精确键，展示代表句仅截取少量字符。
    display = "".join(c for c in compact if unicodedata.category(c)[0] != "C")
    display = display.replace("“", "").replace("”", "")[:40]
    return ("exact:" + compact, display) if display else None
