import json
import os
import subprocess
import sys
from pathlib import Path

from mcp_patterns.rpc import JsonRpcServer
from mcp_patterns.tools import registry

ROOT = Path(__file__).resolve().parent.parent


def run_server(messages, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("WORKSPACE_")}
    env["PYTHONPATH"] = str(ROOT)
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_patterns.server", "--demo"],
        input="\n".join(m if isinstance(m, str) else json.dumps(m) for m in messages) + "\n",
        capture_output=True, text=True, cwd=ROOT, env=env, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines()], proc.stderr


def test_stdio_round_trip():
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_tables", "arguments": {"limit": 3}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "vector_search", "arguments": {"index": "docs", "query": "x", "workspace": "legacy"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "drop_table", "arguments": {"table": "orders"}}},
    ]
    out, err = run_server(msgs)
    by_id = {m["id"]: m for m in out}
    assert len(out) == 5  # the notification got no response
    assert by_id[1]["result"]["serverInfo"]["name"] == "mcp-server-patterns"
    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"
    tools = by_id[2]["result"]["tools"]
    assert len(tools) >= 12
    drop = next(t for t in tools if t["name"] == "drop_table")
    assert drop["annotations"]["destructiveHint"] is True
    assert "confirm" in drop["inputSchema"]["properties"]

    r3 = by_id[3]["result"]
    assert r3["isError"] is False
    assert json.loads(r3["content"][0]["text"])["data"]["total"] == 40
    assert by_id[4]["result"]["structuredContent"]["status"] == "unavailable"
    assert by_id[5]["result"]["structuredContent"]["status"] == "confirmation_required"
    assert "demo-dev-token" not in err  # tokens are never logged


def test_stdout_carries_only_protocol_and_bad_input_is_survivable():
    out, _ = run_server(["{not json", {"jsonrpc": "2.0", "id": 9, "method": "ping"}])
    assert out[0]["error"]["code"] == -32700
    assert out[1] == {"jsonrpc": "2.0", "id": 9, "result": {}}


def test_error_codes_in_process(router):
    s = JsonRpcServer(registry, router)
    assert s.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"})["error"]["code"] == -32601
    assert s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {}})["error"]["code"] == -32602
    assert s.handle({"id": 3})["error"]["code"] == -32600
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    r = s.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "ghost"}})
    assert r["result"]["isError"] is True
    r = s.handle({"jsonrpc": "2.0", "id": 5, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert r["result"]["protocolVersion"] == "2025-06-18"
