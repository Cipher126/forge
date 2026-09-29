from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.dependencies.auth import database_session, require_scope
from forge.application.api_keys import list_api_keys, revoke_api_key
from forge.application.audit import record_audit_event
from forge.config import get_settings
from forge.errors.auth import UnsupportedScopesError
from forge.persistence.models import ApiKey
from forge.schemas.api_keys import (
    ApiKeyListResponse,
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
)
from forge.security.api_keys import SUPPORTED_SCOPES, issue_api_key

router = APIRouter(prefix="/v1/admin/api-keys", tags=["API keys"])


@router.get("", response_model=ApiKeyListResponse)
async def get_api_keys(
    _admin: Annotated[ApiKey, Depends(require_scope("admin"))],
    session: Annotated[AsyncSession, Depends(database_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: UUID | None = None,
    enabled: bool | None = None,
    scope: str | None = None,
) -> ApiKeyListResponse:
    page = await list_api_keys(
        session,
        limit=limit,
        cursor=cursor,
        enabled=enabled,
        scope=scope,
    )
    return ApiKeyListResponse(
        items=[ApiKeyResponse.model_validate(record) for record in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=CreateApiKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: CreateApiKeyRequest,
    admin: Annotated[ApiKey, Depends(require_scope("admin"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> CreateApiKeyResponse:
    unsupported = body.scopes - SUPPORTED_SCOPES
    if unsupported:
        raise UnsupportedScopesError(unsupported)

    issued = await issue_api_key(
        session,
        name=body.name,
        scopes=body.scopes,
        pepper=get_settings().api_key_pepper.get_secret_value(),
        expires_at=body.expires_at,
        rate_limit_requests=body.rate_limit_requests,
        rate_limit_window_s=body.rate_limit_window_s,
    )
    record_audit_event(
        session,
        actor=admin,
        action="api_key.created",
        resource_type="api_key",
        resource_id=str(issued.record.id),
        details={
            "name": issued.record.name,
            "public_id": issued.record.public_id,
            "scopes": issued.record.scopes,
        },
    )
    await session.commit()
    return CreateApiKeyResponse(
        id=str(issued.record.id),
        name=issued.record.name,
        scopes=issued.record.scopes,
        api_key=issued.credential,
        expires_at=issued.record.expires_at,
    )


@router.post("/{api_key_id}/revoke", response_model=ApiKeyResponse)
async def revoke_existing_api_key(
    api_key_id: UUID,
    admin: Annotated[ApiKey, Depends(require_scope("admin"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> ApiKeyResponse:
    try:
        record = await revoke_api_key(session, api_key_id=api_key_id, actor=admin)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return ApiKeyResponse.model_validate(record)
