"""Secret redaction for results and logs.

Three layers: (1) exact secret values registered at runtime (tokens read from the
environment), (2) key-name based redaction for dict fields, (3) regex patterns for
common secret shapes in free text.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(?i)^(.*[_-])?(token|secret|password|passwd|api[_-]?key|authorization|credentials?)$"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_KEYVAL = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|authorization)(\s*[=:]\s*)[^\s,;&\"']+"
)
_SK = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")


class Redactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def register(self, value: str | None) -> None:
        """Remember an exact secret value. Very short values are ignored (false positives)."""
        if value and len(value) >= 6:
            self._secrets.add(value)

    def text(self, s: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            s = s.replace(secret, REDACTED)
        s = _BEARER.sub(f"Bearer {REDACTED}", s)
        s = _KEYVAL.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", s)
        return _SK.sub(REDACTED, s)

    def obj(self, o: Any) -> Any:
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if isinstance(k, str) and _SENSITIVE_KEY.match(k) and v is not None:
                    out[k] = REDACTED
                else:
                    out[self.text(k) if isinstance(k, str) else k] = self.obj(v)
            return out
        if isinstance(o, (list, tuple)):
            return [self.obj(v) for v in o]
        return o


redactor = Redactor()


class RedactingFilter(logging.Filter):
    """Formats the record, redacts the message, and drops args so nothing re-expands."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redactor.text(record.getMessage())
        record.args = ()
        record.exc_info = None  # tracebacks can embed secrets; log a message instead
        record.exc_text = None
        return True


def get_logger() -> logging.Logger:
    """Single flat logger. Filters on a logger only apply to its own records, so do not
    create child loggers. Logs go to stderr; stdout belongs to the protocol."""
    log = logging.getLogger("mcp_patterns")
    if not getattr(log, "_configured", False):
        log.addFilter(RedactingFilter())
        if not log.handlers:
            h = logging.StreamHandler(sys.stderr)
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(h)
        log.setLevel(os.environ.get("MCP_LOG_LEVEL", "INFO").upper())
        log._configured = True  # type: ignore[attr-defined]
    return log
