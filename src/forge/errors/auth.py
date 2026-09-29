class AuthenticationError(Exception):
    """Base class for API-key authentication and authorization failures."""


class InvalidApiKeyError(AuthenticationError):
    """The supplied API-key credential cannot be authenticated."""


class RateLimitExceededError(AuthenticationError):
    """An authenticated API key exhausted its request allowance."""

    def __init__(self, *, limit: int, window_seconds: int, retry_after_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.retry_after_seconds = retry_after_seconds
        super().__init__("API key request limit exceeded")


class InsufficientScopeError(AuthenticationError):
    """The authenticated API key lacks a required scope."""

    def __init__(self, required_scope: str) -> None:
        self.required_scope = required_scope
        super().__init__(f"API key requires the {required_scope!r} scope")


class UnsupportedScopesError(AuthenticationError):
    """One or more requested API-key scopes are unsupported."""

    def __init__(self, scopes: set[str]) -> None:
        self.scopes = frozenset(scopes)
        super().__init__(f"Unsupported scopes: {', '.join(sorted(scopes))}")
