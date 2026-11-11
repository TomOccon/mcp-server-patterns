"""Exceptions that tools may raise. The registry converts them to structured results."""


class ToolError(Exception):
    """A expected, user-facing failure (bad input, not found, invalid state)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class FeatureUnavailable(Exception):
    """The backend does not offer a feature. Becomes status='unavailable', never a crash."""

    def __init__(self, feature: str, reason: str = ""):
        super().__init__(reason or f"feature '{feature}' is unavailable")
        self.feature = feature
        self.reason = reason or f"feature '{feature}' is unavailable"
