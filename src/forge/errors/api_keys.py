from uuid import UUID


class ApiKeyError(Exception):
    """Base class for API-key lifecycle failures."""


class ApiKeyNotFoundError(ApiKeyError, LookupError):
    """The requested API-key record does not exist."""

    def __init__(self, api_key_id: UUID) -> None:
        self.api_key_id = api_key_id
        super().__init__("API key not found")
