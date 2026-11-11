"""Response shaping: pagination and output caps so no tool floods an LLM context."""
from __future__ import annotations

import base64
import json
from typing import Any, Optional, Sequence

from .errors import ToolError

DEFAULT_LIMIT = 25
MAX_LIMIT = 100
DEFAULT_MAX_CHARS = 8000
MAX_STRING_CHARS = 2000


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefix, num = raw.split(":", 1)
        offset = int(num)
        if prefix != "o" or offset < 0:
            raise ValueError
        return offset
    except Exception:
        raise ToolError("invalid_cursor", "cursor is not valid; restart from the first page") from None


def paginate(
    items: Sequence[Any], cursor: Optional[str] = None, limit: int = DEFAULT_LIMIT, max_limit: int = MAX_LIMIT
) -> dict:
    """Opaque-cursor pagination. Limit is clamped to [1, max_limit]."""
    limit = max(1, min(int(limit), max_limit))
    offset = decode_cursor(cursor)
    page = list(items[offset : offset + limit])
    end = offset + len(page)
    return {
        "items": page,
        "offset": offset,
        "total": len(items),
        "next_cursor": encode_cursor(end) if end < len(items) else None,
    }


def _size(o: Any) -> int:
    return len(json.dumps(o, default=str))


def _clip_strings(o: Any, limit: int = MAX_STRING_CHARS) -> Any:
    if isinstance(o, str):
        if len(o) > limit:
            return o[:limit] + f"...[truncated {len(o) - limit} chars]"
        return o
    if isinstance(o, dict):
        return {k: _clip_strings(v, limit) for k, v in o.items()}
    if isinstance(o, list):
        return [_clip_strings(v, limit) for v in o]
    return o


def cap_output(data: Any, max_chars: int = DEFAULT_MAX_CHARS) -> Any:
    """Return data that serializes to at most ~max_chars.

    Strategy: clip long strings; if a dict has an 'items' list, keep the largest prefix
    that fits and repair next_cursor so the caller can continue without gaps; otherwise
    fall back to a text preview. Truncation is always flagged, never silent.
    """
    data = _clip_strings(data)
    if _size(data) <= max_chars:
        return data

    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
        out = dict(data)
        lo, hi = 0, len(items)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            out["items"] = items[:mid]
            out["truncated"] = True
            out["truncation_note"] = "x" * 160
            if _size(out) <= max_chars:
                lo = mid
            else:
                hi = mid - 1
        if lo > 0:
            out["items"] = items[:lo]
            out["truncated"] = True
            out["truncation_note"] = (
                f"output capped at {max_chars} chars; showing {lo} of {len(items)} items "
                "on this page. Use a smaller limit or narrower filters."
            )
            if "offset" in out and lo < len(items):
                out["next_cursor"] = encode_cursor(out["offset"] + lo)
            return out

    text = json.dumps(data, default=str)
    keep = max(0, max_chars - 300)
    while True:
        wrapped = {
            "truncated": True,
            "truncation_note": f"output capped at {max_chars} chars; preview only",
            "original_chars": len(text),
            "preview": text[:keep],
        }
        if _size(wrapped) <= max_chars or keep == 0:
            return wrapped
        keep = int(keep * 0.8)
