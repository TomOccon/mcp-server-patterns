"""Capability probing with graceful degradation.

A backend may not offer every feature (older workspace, missing entitlement, region).
Tools declare `requires="feature"`; the registry asks the probe first and returns a
structured 'unavailable' result instead of raising. Results are cached per workspace.
"""
from __future__ import annotations

from typing import Any

from .errors import FeatureUnavailable


class CapabilityProbe:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], tuple[bool, str]] = {}
        self.probe_count = 0  # exposed for tests and metrics

    def check(self, workspace: str, backend: Any, feature: str) -> tuple[bool, str]:
        key = (workspace, feature)
        if key not in self._cache:
            self.probe_count += 1
            try:
                ok = bool(backend.supports(feature))
                reason = "" if ok else f"feature '{feature}' is not enabled on workspace '{workspace}'"
            except Exception as exc:  # a failing probe means "treat as unavailable"
                ok = False
                reason = f"could not determine support for '{feature}' ({type(exc).__name__})"
            self._cache[key] = (ok, reason)
        return self._cache[key]

    def require(self, workspace: str, backend: Any, feature: str) -> None:
        ok, reason = self.check(workspace, backend, feature)
        if not ok:
            raise FeatureUnavailable(feature, reason)

    def invalidate(self, workspace: str | None = None) -> None:
        if workspace is None:
            self._cache.clear()
        else:
            self._cache = {k: v for k, v in self._cache.items() if k[0] != workspace}
