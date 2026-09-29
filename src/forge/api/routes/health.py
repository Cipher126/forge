import asyncio
from typing import cast

from fastapi import APIRouter, Request, Response, status

from forge.persistence.database import probe_database
from forge.queue.client import probe_redis
from forge.resources import RuntimeResources
from forge.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, include_in_schema=True)
async def health() -> HealthResponse:
    """Report that the API process is alive without probing dependencies."""
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    """Report whether the API can reach all required infrastructure services."""
    resources = cast(RuntimeResources, request.app.state.resources)
    postgres_ready, redis_ready, object_store_ready = await asyncio.gather(
        probe_database(resources.database),
        probe_redis(resources.redis),
        resources.object_store.probe(),
    )
    is_ready = postgres_ready and redis_ready and object_store_ready
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        postgres=postgres_ready,
        redis=redis_ready,
        object_store=object_store_ready,
    )
