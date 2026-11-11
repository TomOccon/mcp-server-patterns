import pytest

from mcp_patterns.errors import ToolError
from mcp_patterns.shaping import cap_output, decode_cursor, paginate
from mcp_patterns.tools import registry


def test_paginate_walks_every_item_once():
    items = list(range(23))
    seen, cursor = [], None
    while True:
        page = paginate(items, cursor, limit=5)
        seen += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == items


def test_limit_is_clamped():
    assert len(paginate(list(range(500)), None, limit=10_000)["items"]) == 100
    assert len(paginate(list(range(5)), None, limit=0)["items"]) == 1


def test_invalid_cursor():
    with pytest.raises(ToolError):
        decode_cursor("not-a-cursor!!")


def test_tool_pagination_end_to_end(call):
    names, cursor = [], None
    while True:
        args = {"limit": 15, **({"cursor": cursor} if cursor else {})}
        d = call("list_tables", **args)["data"]
        names += [t["name"] for t in d["items"]]
        cursor = d["next_cursor"]
        if not cursor:
            break
    assert len(names) == len(set(names)) == 40 == d["total"]
    assert call("list_tables", cursor="garbage")["code"] == "invalid_cursor"


def test_default_page_is_bounded(call):
    assert len(call("list_tables")["data"]["items"]) == 25


def test_cap_trims_items_and_cursor_resumes_without_gaps(call):
    registry.max_output_chars = 900
    first = call("list_tables", limit=100)["data"]
    assert first["truncated"] is True and "truncation_note" in first
    kept = len(first["items"])
    assert 0 < kept < 40
    second = call("list_tables", limit=100, cursor=first["next_cursor"])["data"]
    assert second["items"][0]["name"] != first["items"][-1]["name"]
    all_names = [t["name"] for t in call("list_tables", limit=100)["data"]["items"]]
    # the first two capped pages line up with the uncapped listing
    registry.max_output_chars = 100_000
    full = [t["name"] for t in call("list_tables", limit=100)["data"]["items"]]
    assert [t["name"] for t in first["items"]] + [t["name"] for t in second["items"]] == full[: kept + len(second["items"])]
    assert all_names  # sanity


def test_output_never_exceeds_cap():
    for cap in (400, 1000, 3000):
        out = cap_output({"items": [{"k": "v" * 50} for _ in range(500)], "offset": 0, "next_cursor": None}, cap)
        assert len(str(out)) < cap * 1.3
        assert out["truncated"] is True


def test_long_strings_clipped_and_preview_fallback():
    out = cap_output({"blob": "z" * 50_000}, 8000)
    assert "truncated" in out["blob"] and len(out["blob"]) < 2100
    out = cap_output({"nested": {"a": ["q" * 1500] * 40}}, 2000)
    assert out["truncated"] is True and "preview" in out
