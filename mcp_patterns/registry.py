"""Tool registry: decorator, JSON schema from type hints, metadata, and the call pipeline.

Pipeline for every call (the one place where cross-cutting policy lives):
  validate args -> resolve workspace -> capability gate -> destructive gate -> run
  -> shape (size cap) -> redact.
Tool functions stay small: `def my_tool(ctx, ...)` returning plain data.
"""
from __future__ import annotations

import inspect
import time
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union, get_args, get_origin

from .errors import FeatureUnavailable, ToolError
from .redaction import get_logger, redactor
from .shaping import DEFAULT_MAX_CHARS, cap_output
from .workspaces import WorkspaceRouter

RESERVED = ("workspace", "confirm", "dry_run")
_log = get_logger()


# --- schema from type hints ---------------------------------------------------
def schema_for(tp: Any) -> dict:
    origin, args = get_origin(tp), get_args(tp)
    if origin is typing.Annotated:
        s = dict(schema_for(args[0]))
        desc = next((m for m in args[1:] if isinstance(m, str)), None)
        if desc:
            s["description"] = desc
        return s
    if tp is Any or tp is inspect.Parameter.empty:
        return {}
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is str:
        return {"type": "string"}
    if tp is type(None):
        return {"type": "null"}
    if origin is typing.Literal:
        return {"enum": list(args)}
    if origin in (Union, types.UnionType):
        return {"anyOf": [schema_for(a) for a in args]}
    if origin in (list, set, frozenset, tuple) or tp in (list, set, tuple):
        return {"type": "array", "items": schema_for(args[0]) if args else {}}
    if origin is dict or tp is dict:
        return {"type": "object"}
    raise TypeError(f"unsupported type for tool schema: {tp!r}")


def validate(value: Any, schema: dict, path: str = "") -> list[str]:
    """Validate against the JSON-schema subset that schema_for emits."""
    where = path or "value"
    if "anyOf" in schema:
        if any(not validate(value, s, path) for s in schema["anyOf"]):
            return []
        return [f"{where}: does not match any allowed type"]
    if "enum" in schema:
        return [] if value in schema["enum"] else [f"{where}: must be one of {schema['enum']}"]
    t = schema.get("type")
    if t is None:
        return []
    if t == "null":
        return [] if value is None else [f"{where}: must be null"]
    if t == "boolean":
        return [] if isinstance(value, bool) else [f"{where}: must be a boolean"]
    if t == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
        return [] if ok else [f"{where}: must be an integer"]
    if t == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        return [] if ok else [f"{where}: must be a number"]
    if t == "string":
        return [] if isinstance(value, str) else [f"{where}: must be a string"]
    if t == "array":
        if not isinstance(value, list):
            return [f"{where}: must be an array"]
        errs: list[str] = []
        for i, v in enumerate(value):
            errs += validate(v, schema.get("items", {}), f"{where}[{i}]")
        return errs
    if t == "object":
        return [] if isinstance(value, dict) else [f"{where}: must be an object"]
    return []


# --- tool spec ------------------------------------------------------------------
@dataclass
class ToolSpec:
    name: str
    description: str
    fn: Callable[..., Any]
    input_schema: dict
    read_only: bool
    destructive: bool
    idempotent: bool
    requires: Optional[str]
    needs_workspace: bool
    params: list[str] = field(default_factory=list)

    def annotations(self) -> dict:
        """MCP tool annotations (hints for clients, not enforcement)."""
        return {
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": False,
        }

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": self.annotations(),
        }


class ToolRegistry:
    def __init__(self, max_output_chars: int = DEFAULT_MAX_CHARS):
        self._tools: dict[str, ToolSpec] = {}
        self.max_output_chars = max_output_chars

    # registration
    def tool(
        self,
        *,
        name: Optional[str] = None,
        read_only: bool = False,
        destructive: bool = False,
        idempotent: Optional[bool] = None,
        requires: Optional[str] = None,
        needs_workspace: bool = True,
    ) -> Callable[[Callable], Callable]:
        if read_only and destructive:
            raise ValueError("a tool cannot be both read_only and destructive")

        def deco(fn: Callable) -> Callable:
            spec = self._build(fn, name or fn.__name__, read_only, destructive,
                               read_only if idempotent is None else idempotent, requires, needs_workspace)
            if spec.name in self._tools:
                raise ValueError(f"duplicate tool name: {spec.name}")
            self._tools[spec.name] = spec
            return fn

        return deco

    def _build(self, fn, name, read_only, destructive, idempotent, requires, needs_workspace) -> ToolSpec:
        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn, include_extras=True)
        params = list(sig.parameters.values())
        if not params or params[0].name != "ctx":
            raise TypeError(f"tool '{name}' must take 'ctx' as its first parameter")
        props: dict[str, dict] = {}
        required: list[str] = []
        names: list[str] = []
        for p in params[1:]:
            if p.name in RESERVED:
                raise TypeError(f"tool '{name}': parameter '{p.name}' is reserved (added by the registry)")
            s = dict(schema_for(hints.get(p.name, Any)))
            if p.default is not inspect.Parameter.empty:
                if p.default is not None:
                    s["default"] = p.default
            else:
                required.append(p.name)
            props[p.name] = s
            names.append(p.name)
        if needs_workspace:
            props["workspace"] = {"type": "string", "description": "Named workspace. Omit to use the default workspace."}
        if destructive:
            props["confirm"] = {"type": "boolean", "default": False,
                                "description": "Must be true to execute this destructive operation."}
            props["dry_run"] = {"type": "boolean", "default": False,
                                "description": "Preview what would happen without changing anything."}
        schema = {"type": "object", "properties": props, "additionalProperties": False}
        if required:
            schema["required"] = required
        doc = inspect.getdoc(fn) or name
        desc = " ".join(doc.split("\n\n")[0].split())
        return ToolSpec(name, desc, fn, schema, read_only, destructive, idempotent, requires, needs_workspace, names)

    # introspection
    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list_tools(self) -> list[dict]:
        return [self._tools[n].describe() for n in self.names()]

    # execution
    def call(self, name: str, arguments: Optional[dict], router: WorkspaceRouter) -> dict:
        """Run a tool. Always returns a dict with a 'status'; never raises for tool-level failures."""
        started = time.monotonic()
        result = self._call(name, arguments or {}, router)
        result = redactor.obj(result)
        _log.info("tool_call tool=%s status=%s workspace=%s ms=%d args=%s", name, result.get("status"),
                  result.get("workspace"), (time.monotonic() - started) * 1000, _arg_keys(arguments))
        return result

    def _call(self, name: str, args: dict, router: WorkspaceRouter) -> dict:
        spec = self._tools.get(name)
        if spec is None:
            return _err("unknown_tool", f"unknown tool '{name}'")
        if not isinstance(args, dict):
            return _err("invalid_arguments", "arguments must be an object")
        props = spec.input_schema["properties"]
        errors = [f"unknown argument '{k}'" for k in args if k not in props]
        for r in spec.input_schema.get("required", []):
            if r not in args:
                errors.append(f"missing required argument '{r}'")
        for k, v in args.items():
            if k in props:
                errors += validate(v, props[k], k)
        if errors:
            return _err("invalid_arguments", "; ".join(errors))

        confirm = bool(args.get("confirm", False))
        dry_run = bool(args.get("dry_run", False))
        kwargs = {k: v for k, v in args.items() if k in spec.params}
        try:
            if spec.needs_workspace:
                ctx = router.resolve(args.get("workspace"), dry_run=dry_run)
            else:
                ctx = router.bare_context()
            ws = ctx.workspace
            if spec.requires:
                ok, reason = ctx.probe.check(ws or "", ctx.backend, spec.requires)
                if not ok:
                    return _unavailable(spec.requires, reason, ws)
            if spec.destructive and not dry_run and not confirm:
                return {
                    "status": "confirmation_required",
                    "tool": name,
                    "workspace": ws,
                    "message": f"'{name}' is destructive and was NOT executed.",
                    "how_to_proceed": "Call again with confirm=true to execute, or dry_run=true to preview.",
                }
            data = spec.fn(ctx, **kwargs)
            data = cap_output(data, self.max_output_chars)
            status = "dry_run" if (spec.destructive and dry_run) else "ok"
            out = {"status": status, "workspace": ws, "data": data}
            if status == "dry_run":
                out["note"] = "dry run: no changes were made"
            return out
        except FeatureUnavailable as fu:
            return _unavailable(fu.feature, fu.reason, args.get("workspace") or router.default)
        except ToolError as te:
            return _err(te.code, te.message)
        except Exception as exc:  # last resort; message is redacted by call()
            _log.warning("tool_exception tool=%s type=%s", name, type(exc).__name__)
            return _err("internal_error", f"{type(exc).__name__} while running '{name}'")


def _err(code: str, message: str) -> dict:
    return {"status": "error", "code": code, "message": message}


def _unavailable(feature: str, reason: str, workspace: Optional[str]) -> dict:
    return {
        "status": "unavailable",
        "feature": feature,
        "workspace": workspace,
        "reason": reason,
        "hint": "Try another workspace that supports this feature, or skip this step.",
    }


def _arg_keys(arguments: Optional[dict]) -> str:
    """Log argument names only; values can carry secrets or customer data."""
    return ",".join(sorted(arguments)) if isinstance(arguments, dict) else ""
