from typing import Annotated

from fastapi import APIRouter, Depends, Request

from forge.api.dependencies.auth import require_scope
from forge.persistence.models import ApiKey
from forge.schemas.handlers import HandlerListResponse, HandlerResponse

router = APIRouter(prefix="/v1/handlers", tags=["Handlers"])


@router.get("", response_model=HandlerListResponse)
async def get_handlers(
    request: Request,
    _api_key: Annotated[ApiKey, Depends(require_scope("jobs:read"))],
) -> HandlerListResponse:
    registry = request.app.state.resources.handlers
    definitions = sorted(registry, key=lambda item: (item.job_type, item.version))
    return HandlerListResponse(
        items=[
            HandlerResponse(
                job_type=definition.job_type,
                version=definition.version,
                active=registry.active_versions.get(definition.job_type) == definition.version,
                execution_mode=definition.execution_mode,
                timeout_seconds=definition.timeout_seconds,
                default_max_attempts=definition.default_max_attempts,
                payload_schema=definition.payload_model.model_json_schema(),
            )
            for definition in definitions
        ]
    )
