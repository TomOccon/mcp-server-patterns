"""Multi-workspace routing with headless token auth.

Each workspace gets its token from WORKSPACE_<NAME>_TOKEN (name upper-cased, non
alphanumerics become underscores). Tokens are read at call time (so rotation works),
registered with the redactor, and never logged or returned.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .backend import FakeBackend, ReadOnlyBackend
from .capabilities import CapabilityProbe
from .errors import FeatureUnavailable, ToolError
from .redaction import redactor

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class WorkspaceConfig:
    name: str
    url: str = ""
    features: Optional[frozenset] = None  # None = backend decides (fake: all features)


@dataclass
class Context:
    """What a tool function receives as its first argument."""

    workspace: Optional[str]
    backend: Any
    router: "WorkspaceRouter"
    probe: CapabilityProbe
    dry_run: bool = False

    def require(self, feature: str) -> None:
        """In-function feature gate. Raises FeatureUnavailable, which the registry turns
        into a structured 'unavailable' result."""
        self.probe.require(self.workspace or "", self.backend, feature)


def token_env_var(name: str) -> str:
    return "WORKSPACE_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper() + "_TOKEN"


class WorkspaceRouter:
    def __init__(
        self,
        workspaces: Mapping[str, WorkspaceConfig],
        default: str,
        env: Optional[Mapping[str, str]] = None,
        backend_factory: Optional[Callable[[WorkspaceConfig, str], Any]] = None,
        probe: Optional[CapabilityProbe] = None,
    ):
        if not workspaces:
            raise ValueError("at least one workspace is required")
        for n in workspaces:
            if not _NAME.match(n):
                raise ValueError(f"invalid workspace name: {n!r}")
        if default not in workspaces:
            raise ValueError(f"default workspace {default!r} is not configured")
        self.workspaces = dict(workspaces)
        self.default = default
        self.env = env if env is not None else os.environ
        self.probe = probe or CapabilityProbe()
        self._factory = backend_factory or (
            lambda cfg, token: FakeBackend(cfg.name, token, cfg.features)
        )
        self._backends: dict[str, Any] = {}

    @classmethod
    def from_dict(cls, cfg: Mapping[str, Any], env: Optional[Mapping[str, str]] = None, **kw: Any) -> "WorkspaceRouter":
        spaces = {}
        for name, body in cfg["workspaces"].items():
            feats = body.get("features")
            spaces[name] = WorkspaceConfig(name, body.get("url", ""), frozenset(feats) if feats is not None else None)
        return cls(spaces, cfg.get("default_workspace") or next(iter(spaces)), env=env, **kw)

    def describe(self) -> dict:
        """Safe description: reports whether a token is configured, never its value."""
        return {
            "default": self.default,
            "workspaces": [
                {
                    "name": n,
                    "url": c.url,
                    "token_env_var": token_env_var(n),
                    "token_configured": bool(self.env.get(token_env_var(n))),
                }
                for n, c in sorted(self.workspaces.items())
            ],
        }

    def resolve(self, name: Optional[str], dry_run: bool = False) -> Context:
        ws = name or self.default
        if ws not in self.workspaces:
            raise ToolError("unknown_workspace", f"unknown workspace '{ws}'; available: {', '.join(sorted(self.workspaces))}")
        var = token_env_var(ws)
        token = self.env.get(var, "")
        if not token:
            raise ToolError("auth_missing", f"no token for workspace '{ws}'; set environment variable {var}")
        redactor.register(token)
        backend = self._backends.get(ws)
        if backend is None:
            backend = self._backends[ws] = self._factory(self.workspaces[ws], token)
        elif hasattr(backend, "refresh_token"):
            backend.refresh_token(token)
        view = ReadOnlyBackend(backend) if dry_run else backend
        return Context(workspace=ws, backend=view, router=self, probe=self.probe, dry_run=dry_run)

    def bare_context(self) -> Context:
        return Context(workspace=None, backend=None, router=self, probe=self.probe)


__all__ = ["WorkspaceRouter", "WorkspaceConfig", "Context", "token_env_var", "FeatureUnavailable"]
