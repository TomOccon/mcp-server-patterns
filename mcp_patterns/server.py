"""Entry point: python -m mcp_patterns.server [--demo | --config FILE]"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .redaction import get_logger
from .rpc import JsonRpcServer
from .tools import registry
from .workspaces import WorkspaceRouter, token_env_var

DEMO_CONFIG = {
    "default_workspace": "dev",
    "workspaces": {
        "dev": {"url": "https://dev.example.invalid"},
        # an older workspace without model serving or vector search
        "legacy": {"url": "https://legacy.example.invalid", "features": ["catalog", "jobs", "compute"]},
    },
}


def build_router(config: dict, demo: bool = False) -> WorkspaceRouter:
    if demo:
        # Placeholder credentials so the demo runs headless. Real deployments set
        # WORKSPACE_<NAME>_TOKEN in the environment (secret manager, CI, etc).
        for name in config["workspaces"]:
            os.environ.setdefault(token_env_var(name), f"demo-{name}-token-not-a-real-secret")
    return WorkspaceRouter.from_dict(config)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mcp_patterns.server", description=__doc__)
    ap.add_argument("--demo", action="store_true", help="use built-in fake workspaces and placeholder tokens")
    ap.add_argument("--config", help="path to a JSON workspace config")
    args = ap.parse_args(argv)
    if args.demo == bool(args.config):
        ap.error("pass exactly one of --demo or --config FILE")
    config = DEMO_CONFIG if args.demo else json.load(open(args.config))
    router = build_router(config, demo=args.demo)
    registry.max_output_chars = int(os.environ.get("MCP_MAX_OUTPUT_CHARS", registry.max_output_chars))

    protocol_out = sys.stdout
    sys.stdout = sys.stderr  # stray print() in a tool must not corrupt the protocol stream
    get_logger().info("serving %d tools, workspaces=%s", len(registry.names()), ",".join(router.workspaces))
    JsonRpcServer(registry, router).serve(sys.stdin, protocol_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
