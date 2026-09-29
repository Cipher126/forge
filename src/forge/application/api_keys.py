from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import any_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.application.audit import record_audit_event
from forge.errors.api_keys import ApiKeyNotFoundError
from forge.persistence.models import ApiKey


@dataclass(frozen=True, slots=True)
class ApiKeyPage:
    items: list[ApiKey]
    next_cursor: UUID | None


async def list_api_keys(
    session: AsyncSession,
    *,
    limit: int,
    cursor: UUID | None,
    enabled: bool | None,
    scope: str | None,
) -> ApiKeyPage:
    query = select(ApiKey)
    if cursor is not None:
        query = query.where(ApiKey.id < cursor)
    if enabled is not None:
        query = query.where(ApiKey.enabled.is_(enabled))
    if scope is not None:
        query = query.where(any_(ApiKey.scopes) == scope)
    records = list((await session.scalars(query.order_by(ApiKey.id.desc()).limit(limit + 1))).all())
    has_more = len(records) > limit
    items = records[:limit]
    return ApiKeyPage(items=items, next_cursor=items[-1].id if has_more else None)


async def revoke_api_key(
    session: AsyncSession,
    *,
    api_key_id: UUID,
    actor: ApiKey,
) -> ApiKey:
    record = await session.scalar(select(ApiKey).where(ApiKey.id == api_key_id).with_for_update())
    if record is None:
        raise ApiKeyNotFoundError(api_key_id)
    if record.revoked_at is not None:
        return record
    database_now = await session.scalar(select(func.clock_timestamp()))
    if database_now is None:
        raise RuntimeError("PostgreSQL did not return its current time")
    record.enabled = False
    record.revoked_at = database_now
    record_audit_event(
        session,
        actor=actor,
        action="api_key.revoked",
        resource_type="api_key",
        resource_id=str(record.id),
        details={"name": record.name, "public_id": record.public_id},
    )
    await session.flush()
    return record
