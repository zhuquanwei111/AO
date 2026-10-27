"""复用原结果补缺函数；不包含旧Codex视觉调用链。"""
from core import parse_count


def fill_missing(result, raws):
    """保留已验证OCR值，只填原缺项；原文仍用同一业务解析器，缺失不补零。"""
    copy = {key: (value.copy() if isinstance(value, (dict, list)) else value)
            for key, value in result.items()}
    for label in list(copy["missing_fields"]):
        raw = raws.get(label)
        value, approximate = parse_count(raw) if isinstance(raw, str) else (None, False)
        if value is None:
            continue
        copy["metrics"][label] = value
        copy["raw_displays"][label] = raw
        copy["missing_fields"].remove(label)
        copy["missing_reasons"].pop(label, None)
        if approximate and label not in copy["approximate_metrics"]:
            copy["approximate_metrics"].append(label)
    return copy
