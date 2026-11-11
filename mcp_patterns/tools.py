"""Example tools over the FakeBackend: catalog, jobs, model serving, vector search, compute.

This is the pattern to copy. Each tool is a plain function taking `ctx` first. The
registry adds workspace routing, capability gating, confirm/dry_run for destructive
tools, size caps and redaction, so none of that appears here.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from .errors import ToolError
from .registry import ToolRegistry
from .shaping import paginate

registry = ToolRegistry()

Limit = Annotated[int, "Page size (1-100)."]
Cursor = Annotated[Optional[str], "Opaque cursor from a previous page's next_cursor."]


# --- workspaces -----------------------------------------------------------------
@registry.tool(read_only=True, needs_workspace=False)
def list_workspaces(ctx):
    """List configured workspaces and whether a token is set (never the token itself)."""
    return ctx.router.describe()


# --- catalog ----------------------------------------------------------------------
@registry.tool(read_only=True)
def list_tables(ctx, name_contains: Optional[str] = None, limit: Limit = 25, cursor: Cursor = None):
    """List tables in the catalog, optionally filtered by name. Paginated."""
    return paginate(ctx.backend.list_tables(name_contains), cursor, limit)


@registry.tool(read_only=True)
def describe_table(ctx, table: str):
    """Return the schema and row count of one table."""
    return ctx.backend.get_table(table)


@registry.tool(read_only=True)
def sample_table(ctx, table: str, rows: Annotated[int, "Number of rows (1-50)."] = 5):
    """Return a small synthetic sample of rows from a table."""
    rows = max(1, min(rows, 50))
    return {"table": table, "items": ctx.backend.sample_rows(table, rows)}


@registry.tool(destructive=True, idempotent=False)
def drop_table(ctx, table: str):
    """Permanently drop a table. Requires confirm=true; use dry_run=true to preview."""
    if ctx.dry_run:
        t = ctx.backend.get_table(table)
        return {"would_drop": table, "rows_that_would_be_removed": t["rows"]}
    return ctx.backend.drop_table(table)


# --- jobs ---------------------------------------------------------------------------
@registry.tool(read_only=True)
def list_jobs(
    ctx,
    status: Optional[Literal["queued", "running", "succeeded", "failed", "cancelled"]] = None,
    limit: Limit = 25,
    cursor: Cursor = None,
):
    """List pipeline job runs, optionally filtered by status. Paginated."""
    return paginate(ctx.backend.list_jobs(status), cursor, limit)


@registry.tool(read_only=True)
def get_job(ctx, job_id: str):
    """Get one job run by id."""
    return ctx.backend.get_job(job_id)


@registry.tool(idempotent=False)
def run_job(ctx, pipeline: str):
    """Queue a new run of a pipeline. Not destructive, but it does change state."""
    return ctx.backend.run_job(pipeline)


@registry.tool(destructive=True, idempotent=True)
def cancel_job(ctx, job_id: str):
    """Cancel a queued or running job. Requires confirm=true; use dry_run=true to preview."""
    if ctx.dry_run:
        job = ctx.backend.get_job(job_id)
        return {"would_cancel": job_id, "current_status": job["status"]}
    return ctx.backend.cancel_job(job_id)


# --- model serving (feature-gated) ----------------------------------------------------
@registry.tool(read_only=True, requires="model_serving")
def list_models(ctx):
    """List served models and their state."""
    return {"items": ctx.backend.list_models()}


@registry.tool(read_only=True, requires="model_serving")
def invoke_model(ctx, model: str, inputs: Annotated[list[str], "1-20 text inputs."]):
    """Run inference against a served model."""
    if not 1 <= len(inputs) <= 20:
        raise ToolError("invalid_arguments", "inputs must contain between 1 and 20 items")
    return {"model": model, "items": ctx.backend.invoke_model(model, inputs)}


# --- vector search (feature-gated) ------------------------------------------------------
@registry.tool(read_only=True, requires="vector_search")
def vector_search(ctx, index: str, query: str, top_k: Annotated[int, "Results to return (1-20)."] = 5):
    """Semantic search over a vector index. Returns the top_k closest documents."""
    return {"index": index, "items": ctx.backend.vector_search(index, query, max(1, min(top_k, 20)))}


# --- compute lifecycle (feature-gated) ----------------------------------------------------
@registry.tool(read_only=True, requires="compute")
def list_clusters(ctx):
    """List compute clusters and their state."""
    return {"items": ctx.backend.list_clusters()}


@registry.tool(idempotent=True, requires="compute")
def start_cluster(ctx, name: str):
    """Start a cluster. Safe to repeat."""
    return ctx.backend.start_cluster(name)


@registry.tool(destructive=True, idempotent=True, requires="compute")
def terminate_cluster(ctx, name: str):
    """Terminate a running cluster. Requires confirm=true; use dry_run=true to preview."""
    if ctx.dry_run:
        current = next((c for c in ctx.backend.list_clusters() if c["name"] == name), None)
        if current is None:
            raise ToolError("not_found", f"cluster '{name}' not found")
        return {"would_terminate": name, "current_state": current["state"]}
    return ctx.backend.terminate_cluster(name)
