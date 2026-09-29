from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class CreateApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    scopes: set[str] = Field(min_length=1)
    expires_at: datetime | None = None
    rate_limit_requests: int = Field(default=100, ge=1)
    rate_limit_window_s: int = Field(default=60, ge=1)


class CreateApiKeyResponse(BaseModel):
    id: str
    name: str
    scopes: list[str]
    api_key: str
    expires_at: datetime | None


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    public_id: str
    name: str
    scopes: list[str]
    enabled: bool
    expires_at: datetime | None
    rate_limit_requests: int
    rate_limit_window_s: int
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyResponse]
    next_cursor: UUID | None
