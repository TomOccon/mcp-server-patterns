"""Minimal MCP-compatible JSON-RPC 2.0 server over stdio, stdlib only.

Framing: one JSON message per line (the MCP stdio transport). Implements initialize,
notifications/initialized, ping, tools/list and tools/call. The class has no I/O of its
own (`handle` maps a dict to a dict), so the same core can sit behind any transport or
behind the official SDK.
"""
from __future__ import annotations

import json
from typing import IO, Any, Optional

from . import __version__
from .registry import ToolRegistry
from .shaping import paginate
from .workspaces import WorkspaceRouter

SUPPORTED_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
TOOLS_PAGE_SIZE = 100


class JsonRpcServer:
    def __init__(self, registry: ToolRegistry, router: WorkspaceRouter, name: str = "mcp-server-patterns"):
        self.registry = registry
        self.router = router
        self.name = name

    # message-level -------------------------------------------------------------
    def handle(self, msg: Any) -> Optional[dict]:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "invalid request")
        method, params = msg["method"], msg.get("params") or {}
        is_notification = "id" not in msg
        rid = msg.get("id")
        if is_notification:
            return None  # notifications (e.g. notifications/initialized) get no response
        if not isinstance(params, dict):
            return _error(rid, INVALID_PARAMS, "params must be an object")
        try:
            if method == "initialize":
                return _ok(rid, self._initialize(params))
            if method == "ping":
                return _ok(rid, {})
            if method == "tools/list":
                page = paginate(self.registry.list_tools(), params.get("cursor"), TOOLS_PAGE_SIZE, TOOLS_PAGE_SIZE)
                result: dict = {"tools": page["items"]}
                if page["next_cursor"]:
                    result["nextCursor"] = page["next_cursor"]
                return _ok(rid, result)
            if method == "tools/call":
                return self._call(rid, params)
        except Exception as exc:
            from .errors import ToolError

            if isinstance(exc, ToolError):
                return _error(rid, INVALID_PARAMS, exc.message)
            return _error(rid, -32603, f"internal error ({type(exc).__name__})")
        return _error(rid, METHOD_NOT_FOUND, f"method not found: {method}")

    def _initialize(self, params: dict) -> dict:
        wanted = params.get("protocolVersion")
        version = wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": __version__},
        }

    def _call(self, rid: Any, params: dict) -> dict:
        name = params.get("name")
        if not isinstance(name, str):
            return _error(rid, INVALID_PARAMS, "tools/call requires a string 'name'")
        result = self.registry.call(name, params.get("arguments"), self.router)
        return _ok(rid, {
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
            "structuredContent": result,
            "isError": result.get("status") == "error",
        })

    # transport -------------------------------------------------------------------
    def serve(self, stdin: IO[str], stdout: IO[str]) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                resp: Optional[dict] = _error(None, PARSE_ERROR, "parse error")
            else:
                resp = self.handle(msg)
            if resp is not None:
                stdout.write(json.dumps(resp) + "\n")
                stdout.flush()


def _ok(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
