import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid7

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.persistence.models import ApiKey

KEY_PREFIX = "forge_live_"
SUPPORTED_SCOPES = frozenset({"jobs:read", "jobs:write", "admin"})


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    record: ApiKey
    secret: str

    @property
    def credential(self) -> str:
        return f"{KEY_PREFIX}{self.record.public_id}_{self.secret}"


def digest_secret(secret: str, pepper: str) -> bytes:
    return hmac.digest(pepper.encode(), secret.encode(), hashlib.sha256)


def parse_credential(credential: str) -> tuple[str, str] | None:
    if not credential.startswith(KEY_PREFIX):
        return None
    remainder = credential.removeprefix(KEY_PREFIX)
    public_id, separator, secret = remainder.partition("_")
    if not separator or not public_id or not secret:
        return None
    return public_id, secret


async def issue_api_key(
    session: AsyncSession,
    *,
    name: str,
    scopes: set[str],
    pepper: str,
    expires_at: datetime | None = None,
    rate_limit_requests: int = 100,
    rate_limit_window_s: int = 60,
) -> IssuedApiKey:
    unsupported = scopes - SUPPORTED_SCOPES
    if unsupported:
        raise ValueError(f"unsupported scopes: {', '.join(sorted(unsupported))}")
    if not scopes:
        raise ValueError("at least one scope is required")

    secret = secrets.token_urlsafe(32)
    record = ApiKey(
        id=uuid7(),
        public_id=uuid7().hex,
        secret_digest=digest_secret(secret, pepper),
        pepper_version=1,
        name=name,
        scopes=sorted(scopes),
        enabled=True,
        expires_at=expires_at,
        rate_limit_requests=rate_limit_requests,
        rate_limit_window_s=rate_limit_window_s,
    )
    session.add(record)
    await session.flush()
    return IssuedApiKey(record=record, secret=secret)


async def authenticate_api_key(
    session: AsyncSession,
    *,
    credential: str,
    pepper: str,
) -> ApiKey | None:
    parsed = parse_credential(credential)
    if parsed is None:
        return None
    public_id, secret = parsed
    record = await session.scalar(
        select(ApiKey).where(
            ApiKey.public_id == public_id,
            ApiKey.enabled.is_(True),
            ApiKey.revoked_at.is_(None),
            or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > func.clock_timestamp()),
        )
    )
    if record is None:
        return None
    if not hmac.compare_digest(record.secret_digest, digest_secret(secret, pepper)):
        return None
    return record
