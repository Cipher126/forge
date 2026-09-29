from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.dependencies.auth import database_session, require_scope
from forge.application.audit import list_audit_events
from forge.persistence.models import ApiKey
from forge.schemas.audit import AuditEventListResponse, AuditEventResponse

router = APIRouter(prefix="/v1/admin/audit-events", tags=["Audit"])


@router.get("", response_model=AuditEventListResponse)
async def get_audit_events(
    _admin: Annotated[ApiKey, Depends(require_scope("admin"))],
    session: Annotated[AsyncSession, Depends(database_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: int | None = None,
    action: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    resource_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    actor_public_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> AuditEventListResponse:
    page = await list_audit_events(
        session,
        limit=limit,
        cursor=cursor,
        action=action,
        resource_type=resource_type,
        actor_public_id=actor_public_id,
    )
    return AuditEventListResponse(
        items=[AuditEventResponse.model_validate(event) for event in page.items],
        next_cursor=page.next_cursor,
    )
