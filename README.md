# mcp-server-patterns

A reference implementation of production patterns for a Python MCP (Model Context Protocol) server in front of a data platform.

**This is a reference implementation running against a fake in-memory backend. It is not any vendor's code, does not call any real API, and contains no proprietary material.** The tools, data and workspaces are synthetic.

## The problem

Data platform vendors increasingly ship managed MCP servers. They cover the common surface well and leave gaps: the APIs they do not expose, plus the policy you need around them (multiple workspaces, destructive-action safety, context budgets). The usual answer is a small custom MCP server that sits beside the managed ones and fills the gaps.

A custom server with 40 or more tools gets messy fast if every tool re-implements auth, routing, paging and safety. This project shows one way to keep tools tiny and push cross-cutting policy into one pipeline. The example has 15 tools across catalog, jobs, model serving, vector search and compute lifecycle. The same shape scales to many more.

## Architecture

```
  MCP client (LLM host)
        |  JSON-RPC 2.0, one message per line
        v
  +-------------------------------+
  | rpc.py   JsonRpcServer        |  initialize, ping, tools/list, tools/call
  | (stdlib; swappable transport) |  pure handle(dict) -> dict
  +---------------+---------------+
                  |
                  v
  +-------------------------------------------------------------+
  | registry.py   ToolRegistry.call()  - the policy pipeline     |
  |                                                             |
  |  1 validate args against schema generated from type hints   |
  |  2 resolve workspace  --> workspaces.py (WORKSPACE_<N>_TOKEN)|
  |  3 capability gate    --> capabilities.py (cached probe)    |
  |  4 destructive gate   (confirm=true / dry_run=true)         |
  |  5 run tool fn(ctx, ...)           <-- tools.py             |
  |  6 shape: size cap, cursor repair  --> shaping.py           |
  |  7 redact secrets                  --> redaction.py         |
  +---------------+---------------------------------------------+
                  |
                  v
  +-------------------------------+
  | backend.py  FakeBackend       |  tables, jobs, models, vector index,
  | (replace with a real client)  |  clusters; one instance per workspace
  +-------------------------------+
```

Every call returns a dict with a `status`: `ok`, `dry_run`, `confirmation_required`, `unavailable` or `error`. Only `error` sets `isError` on the MCP response.

## Design decisions

1. **A registry with a decorator, not hand-written handlers.** Tools are `def name(ctx, ...)` functions. The registry generates the JSON schema from type hints (`Optional`, `Literal`, `list[str]`, `Annotated[..., "description"]`) and records metadata (`read_only`, `destructive`, `idempotent`, `requires`). Schemas cannot drift from signatures, and policy applies uniformly because there is one call path.
2. **Multi-workspace routing is a registry concern, not a tool concern.** Every workspace-scoped tool gets an optional `workspace` argument added automatically. Tokens come from `WORKSPACE_<NAME>_TOKEN` environment variables (headless, no interactive login), are read on each call so rotation works, are registered with the redactor, and are never logged or returned.
3. **Graceful degradation over exceptions.** Workspaces differ in what they support. A tool declaring `requires="vector_search"` gets a cached capability probe; if the feature is missing the caller receives `{"status": "unavailable", "feature": ..., "reason": ...}`. The model can adapt (use another workspace, skip a step) instead of seeing a stack trace. A probe that itself fails is treated as unavailable.
4. **Confirm gates for destructive tools.** A tool marked `destructive=True` gets `confirm` and `dry_run` arguments added by the registry. Without `confirm=true` nothing runs and the response says how to proceed. `dry_run=true` runs the tool against a read-only backend wrapper that raises on any mutating method, so a preview cannot change state even if a tool is written badly. `dry_run` wins over `confirm`. `confirm` must be a real boolean; the string `"true"` is rejected.
5. **Output caps and cursor pagination.** Lists use opaque cursors with a clamped page size. A global character cap trims `items` to the largest prefix that fits, flags `truncated`, and rewrites `next_cursor` so the caller resumes with no gaps. Long strings are clipped. Tools cannot flood the context window even when written carelessly.
6. **Redaction in depth.** Exact secret values (registered when read), sensitive key names (`token`, `api_key`, `authorization` and similar, but not `max_tokens`), and regexes for Bearer and key=value text. It applies to every result and, through a logging filter, every log line. Logs record argument names, not values, and exceptions are logged by type only.
7. **A transport-agnostic core and a stdlib adapter.** The registry and router know nothing about JSON-RPC. `rpc.py` is a small stdlib stdio server, so the project runs with zero dependencies. stdout is reserved for the protocol (the server redirects stray `print` to stderr). To use the official `mcp` SDK, wire its list and call handlers to `registry.list_tools()` and `registry.call()`; that adapter is not included.

## Run

Requires Python 3.10 or later. No runtime dependencies.

```
python -m mcp_patterns.server --demo
```

Then send newline-delimited JSON-RPC on stdin:

```
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"drop_table","arguments":{"table":"orders","dry_run":true}}}' \
  | python -m mcp_patterns.server --demo
```

`--demo` configures two fake workspaces (`dev`, with every feature, and `legacy`, without model serving or vector search) and sets placeholder tokens if the environment has none. For your own configuration use `--config workspaces.json` and export a token per workspace:

```
{"default_workspace": "prod",
 "workspaces": {"prod": {"url": "https://prod.example.invalid"},
                "staging": {"url": "https://staging.example.invalid", "features": ["catalog", "jobs"]}}}

export WORKSPACE_PROD_TOKEN=...      # name is upper-cased; non-alphanumerics become _
export WORKSPACE_STAGING_TOKEN=...
```

Run the tests:

```
pip install pytest
python -m pytest -q
```

Environment: `MCP_MAX_OUTPUT_CHARS` (default 8000) sets the response cap; `MCP_LOG_LEVEL` sets the stderr log level.

## Add a tool

Add a function to `mcp_patterns/tools.py` (or any module that imports `registry`):

```python
@registry.tool(read_only=True, requires="jobs")
def count_failed_jobs(ctx, pipeline: Annotated[Optional[str], "Filter by pipeline."] = None):
    """Count failed job runs, optionally for one pipeline."""
    jobs = ctx.backend.list_jobs("failed")
    return {"failed": sum(1 for j in jobs if pipeline in (None, j["pipeline"]))}
```

Rules:

- First parameter is `ctx` (`ctx.backend`, `ctx.workspace`, `ctx.dry_run`, `ctx.require(feature)`).
- Do not declare `workspace`, `confirm` or `dry_run`; the registry owns them.
- Return plain JSON-serializable data. For lists return `paginate(items, cursor, limit)` or `{"items": [...]}` so the output cap can trim them.
- Raise `ToolError(code, message)` for expected failures.
- For a destructive tool use `@registry.tool(destructive=True)` and, when `ctx.dry_run` is true, return a description of what would happen rather than acting.
- Add a method to the backend for the new operation, and add its name to `FakeBackend.MUTATING` if it changes state.

## Layout

```
mcp_patterns/
  registry.py      decorator, schema generation, validation, call pipeline
  workspaces.py    named workspaces, token env vars, Context
  capabilities.py  cached feature probe
  shaping.py       cursors, pagination, output cap
  redaction.py     redactor and logging filter
  backend.py       FakeBackend and the read-only dry-run wrapper
  tools.py         15 example tools
  rpc.py           stdlib JSON-RPC stdio server
  server.py        entry point
tests/             registry, routing, degradation, safety, redaction, shaping, stdio round trip
```

## License

MIT
