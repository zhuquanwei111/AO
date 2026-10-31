"""Reconstruct visible OCR observations without treating them as platform identities.

The caller supplies one session and one capture at a time. No batch identifier
means each observation is independent. Field values are OCR-derived candidates,
not verified user identities or proof of a complete capture.
"""

from __future__ import annotations

import re
import json
import uuid
from difflib import SequenceMatcher
from typing import Any


def _box(item: dict[str, Any]) -> dict[str, float] | None:
    box = item.get("content", {}).get("text_position")
    if not box or not all(key in box for key in ("x", "y", "width", "height")):
        return None
    return box


def _split(text: str) -> tuple[str | None, str | None] | None:
    """Extract a bounded header/body candidate, never interpret numeric badges."""
    text = text.strip()
    if "://" in text or sum(text.count(c) for c in (":", "：")) != 1:
        return None
    header, body = re.split("[:：]", text, maxsplit=1)
    header, body = header.strip(), body.strip()
    if not header or header.startswith("主播回复") or len(header) > 40 or any(c.isspace() for c in header):
        return None
    # A leading digit may be a badge or part of a nickname; neither is guessed.
    nickname = None if header[0].isdigit() else header
    return nickname, body or None


def _overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    overlap = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
    return overlap > 0 and overlap >= min(a["height"], b["height"]) * 0.45


def _gap(a: dict[str, float], b: dict[str, float]) -> float:
    return max(0.0, b["x"] - (a["x"] + a["width"]))


def _make(items: list[dict[str, Any]], nickname: str | None, text: str | None,
          boundary: str, header_evidence: str | None = None,
          nickname_sources: list[str] | None = None, text_sources: list[str] | None = None) -> dict[str, Any]:
    ids = [item["event_id"] for item in items]
    times = [item["observed_at_ms"] for item in items]
    boxes = [_box(item) for item in items if _box(item)]
    position = None
    if boxes:
        left = min(b["x"] for b in boxes)
        top = min(b["y"] for b in boxes)
        right = max(b["x"] + b["width"] for b in boxes)
        bottom = max(b["y"] + b["height"] for b in boxes)
        position = {"x": left, "y": top, "width": right-left, "height": bottom-top}
    fields = {"nickname": nickname, "text": text, "consumption_level": None,
              "fan_level": None, "fan_group": None}
    id_material = json.dumps([items[0].get("source"), ids[0], min(times)], ensure_ascii=True)
    return {"comment_id": uuid.uuid5(uuid.NAMESPACE_OID, id_material).hex, "source": items[0].get("source"), **fields,
            "field_status": {k: "candidate" if v is not None else "unknown" for k, v in fields.items()},
            "field_sources": {"nickname": nickname_sources or [], "text": text_sources or [],
                              "consumption_level": [], "fan_level": [], "fan_group": []},
            "source_event_ids": ids, "first_observed_at_ms": min(times),
            "last_seen_at_ms": max(times), "boundary_status": boundary,
            "tracking_status": "new", "position": position, "header_evidence": header_evidence,
            "fragments": [{"text": item["content"]["text"], "position": _box(item)} for item in items]}


def _shared_fragment_movement(old: dict[str, Any], current: dict[str, Any]) -> bool:
    left = [f for f in old.get("fragments", []) if f.get("position")]
    right = [f for f in current.get("fragments", []) if f.get("position")]
    matcher = SequenceMatcher(None, [f["text"] for f in left], [f["text"] for f in right], autojunk=False)
    shifts = []
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            a, b = left[block.a + offset]["position"], right[block.b + offset]["position"]
            if abs(a["x"] - b["x"]) <= 0.05 and b["y"] < a["y"]:
                shifts.append(a["y"] - b["y"])
    return len(shifts) >= 2 and max(shifts) - min(shifts) <= 0.03


def reconstruct_comments(observations: list[dict[str, Any]], previous: list[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Return OCR candidates; previous must be the immediately preceding capture.

    Callers isolate sessions, enforce capture completeness and decide expiry.
    No platform identifiers, credentials, or persistent state are inferred.
    """
    batches: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in observations:
        batch = item.get("capture_batch_id")
        # Missing batch does not license grouping observations into a frame.
        source = item.get("source", "")
        key = (source, "batch", batch) if batch is not None else (source, "single", item["event_id"])
        batches.setdefault(key, []).append(item)

    result: list[dict[str, Any]] = []
    for key, items in batches.items():
        ordered = sorted(items, key=lambda i: ((_box(i) or {}).get("y", 2),
                                                (_box(i) or {}).get("x", 2),
                                                i.get("line_index") or 0))
        used: set[str] = set()
        starts = [i for i in ordered if _split(i["content"]["text"]) is not None]
        for start in starts:
            if start["event_id"] in used:
                continue
            nickname, body = _split(start["content"]["text"])
            nickname_sources = [start["event_id"]] if nickname is not None else []
            text_sources = [start["event_id"]] if body is not None else []
            start_box = _box(start)
            if not start_box:
                result.append(_make([start], nickname, body, "ambiguous",
                                    nickname_sources=nickname_sources, text_sources=text_sources))
                used.add(start["event_id"])
                continue
            parts = [start]
            used.add(start["event_id"])
            if key[1] == "batch":
                # Same-row fragments require both overlap and a bounded gap.
                candidates = sorted((i for i in ordered if i is not start and _box(i)
                                     and _overlap(start_box, _box(i))), key=lambda i: _box(i)["x"])
                edge = start_box
                for item in candidates:
                    b = _box(item)
                    if item["event_id"] in used or _split(item["content"]["text"]) is not None:
                        continue
                    limit = min(edge["width"], b["width"]) * 0.5
                    if b["x"] >= start_box["x"] and _gap(edge, b) <= limit:
                        body = (body or "") + item["content"]["text"].strip()
                        text_sources.append(item["event_id"])
                        parts.append(item)
                        used.add(item["event_id"])
                        edge = b
                    elif b["x"] < start_box["x"] and _gap(b, start_box) <= min(b["width"], start_box["width"]) * 0.5:
                        parts.insert(0, item)
                        used.add(item["event_id"])
                # One nearby left-shifted continuation is only a candidate;
                # a new header terminates it and an intervening row is ambiguous.
                below = [i for i in ordered if i["event_id"] not in used and _box(i)
                         and _box(i)["y"] >= start_box["y"] + start_box["height"] * 0.7]
                if below:
                    item = below[0]
                    b = _box(item)
                    gap = b["y"] - (start_box["y"] + start_box["height"])
                    shares_next_start = any(other is not start and _box(other) and _overlap(b, _box(other))
                                            for other in starts)
                    continuation = item["content"]["text"].strip()
                    if (_split(continuation) is None and not shares_next_start
                            and len(continuation) <= 4 and not continuation[0].isdigit()
                            and 0 <= gap <= start_box["height"] * 0.5
                            and b["x"] <= start_box["x"] + start_box["width"] * 0.2):
                        body = (body or "") + item["content"]["text"].strip()
                        text_sources.append(item["event_id"])
                        parts.append(item)
                        used.add(item["event_id"])
            header = re.split("[:：]", start["content"]["text"], maxsplit=1)[0].strip()
            result.append(_make(parts, nickname, body, "partial" if body else "ambiguous", header,
                                nickname_sources, text_sources))
        for item in ordered:
            if item["event_id"] not in used:
                result.append(_make([item], None, None, "ambiguous"))

    # A unique text/geometry match remains ambiguous unless two fragments or
    # two neighbouring comments show a consistent upward movement.
    possible: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for current in result:
        if not current["header_evidence"] or not current["text"] or not current["position"]:
            continue
        matches = []
        for old in previous:
            p, q = old.get("position"), current["position"]
            if (not p or old.get("source") != current["source"]
                    or old.get("header_evidence") != current["header_evidence"] or not old.get("text")):
                continue
            delta = current["first_observed_at_ms"] - old["last_seen_at_ms"]
            if (0 < delta <= 10000 and q["y"] < p["y"] and abs(q["x"]-p["x"]) <= 0.05
                    and SequenceMatcher(None, old["text"], current["text"], autojunk=False).ratio() >= 0.95):
                matches.append(old)
        if len(matches) == 1:
            possible.append((current, matches[0]))
    for current, old in possible:
        unique = sum(candidate["comment_id"] == old["comment_id"] for _, candidate in possible) == 1
        shift = old["position"]["y"] - current["position"]["y"]
        neighbours = any(
            other is not current and candidate["comment_id"] != old["comment_id"]
            and sum(match["comment_id"] == candidate["comment_id"] for _, match in possible) == 1
            and abs((candidate["position"]["y"] - other["position"]["y"]) - shift) <= 0.03
            and (candidate["position"]["y"] - old["position"]["y"])
                * (other["position"]["y"] - current["position"]["y"]) > 0
            for other, candidate in possible
        )
        if unique and (_shared_fragment_movement(old, current) or neighbours):
            current["comment_id"] = old["comment_id"]
            current["first_observed_at_ms"] = old["first_observed_at_ms"]
            current["source_event_ids"] = list(dict.fromkeys(old["source_event_ids"] + current["source_event_ids"]))
            current["tracking_status"] = "continuous_candidate"
        elif unique:
            current["tracking_status"] = "ambiguous_single"
    return result
