from mcp_patterns.backend import FakeBackend, ReadOnlyBackend
from mcp_patterns.errors import ToolError

import pytest


def table_exists(call, name):
    return call("describe_table", table=name)["status"] == "ok"


def test_destructive_without_confirm_does_nothing(call):
    r = call("drop_table", table="orders")
    assert r["status"] == "confirmation_required"
    assert "confirm=true" in r["how_to_proceed"]
    assert table_exists(call, "orders")


def test_confirm_false_is_not_confirmation(call):
    assert call("drop_table", table="orders", confirm=False)["status"] == "confirmation_required"
    assert table_exists(call, "orders")


def test_confirm_must_be_real_boolean(call):
    r = call("drop_table", table="orders", confirm="true")
    assert r["status"] == "error" and r["code"] == "invalid_arguments"
    assert table_exists(call, "orders")


def test_dry_run_previews_without_mutating(call):
    r = call("drop_table", table="orders", dry_run=True)
    assert r["status"] == "dry_run"
    assert r["data"]["would_drop"] == "orders"
    assert table_exists(call, "orders")


def test_dry_run_wins_over_confirm(call):
    r = call("drop_table", table="orders", dry_run=True, confirm=True)
    assert r["status"] == "dry_run" and table_exists(call, "orders")


def test_confirm_executes(call):
    r = call("drop_table", table="orders", confirm=True)
    assert r["status"] == "ok" and r["data"]["dropped"] == "orders"
    assert not table_exists(call, "orders")


def test_every_destructive_tool_is_gated(call):
    from mcp_patterns.tools import registry

    for name in registry.names():
        spec = registry.get(name)
        if spec.destructive:
            assert "confirm" in spec.input_schema["properties"], name
    assert call("cancel_job", job_id="job-0003")["status"] == "confirmation_required"
    assert call("terminate_cluster", name="etl-small")["status"] == "confirmation_required"
    states = {c["name"]: c["state"] for c in call("list_clusters")["data"]["items"]}
    assert states["etl-small"] == "running"


def test_invalid_state_is_a_clean_error(call):
    r = call("cancel_job", job_id="job-0001", confirm=True)  # succeeded job
    assert r["status"] == "error" and r["code"] == "invalid_state"


def test_read_only_proxy_blocks_mutation():
    proxy = ReadOnlyBackend(FakeBackend("w", "tok-123456"))
    assert proxy.list_tables()
    with pytest.raises(ToolError) as e:
        proxy.drop_table("orders")
    assert e.value.code == "dry_run_violation"
