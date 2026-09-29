from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.persistence.models import ApiKey, AuditEvent


@dataclass(frozen=True, slots=True)
class AuditEventPage:
    items: list[AuditEvent]
    next_cursor: int | None


def record_audit_event(
    session: AsyncSession,
    *,
    actor: ApiKey,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_api_key_id=actor.id,
        actor_public_id=actor.public_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
    )
    session.add(event)
    return event


async def list_audit_events(
    session: AsyncSession,
    *,
    limit: int,
    cursor: int | None,
    action: str | None,
    resource_type: str | None,
    actor_public_id: str | None,
) -> AuditEventPage:
    query = select(AuditEvent)
    if cursor is not None:
        query = query.where(AuditEvent.id < cursor)
    if action is not None:
        query = query.where(AuditEvent.action == action)
    if resource_type is not None:
        query = query.where(AuditEvent.resource_type == resource_type)
    if actor_public_id is not None:
        query = query.where(AuditEvent.actor_public_id == actor_public_id)
    events = list(
        (await session.scalars(query.order_by(AuditEvent.id.desc()).limit(limit + 1))).all()
    )
    has_more = len(events) > limit
    items = events[:limit]
    return AuditEventPage(items=items, next_cursor=items[-1].id if has_more else None)
