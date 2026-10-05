import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from forge.api.dependencies.auth import database_session, require_scope, require_scope_detached
from forge.application.operations import (
    database_now,
    job_status_counts,
    job_type_permit_usage,
    operational_rates,
    performance_statistics,
    process_metric_counts,
    queue_statistics,
    render_prometheus_metrics,
    worker_health,
)
from forge.domain.jobs import TERMINAL_JOB_STATUSES
from forge.persistence.models import ApiKey, JobTransition
from forge.schemas.operations import (
    OperationsSummary,
    PerformanceStatistics,
    QueueStatistics,
    WorkerHealthListResponse,
)

router = APIRouter(prefix="/v1/admin/operations", tags=["Operations"])


@router.get("/summary", response_model=OperationsSummary)
async def get_operations_summary(
    request: Request,
    _admin: Annotated[ApiKey, Depends(require_scope("admin"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> OperationsSummary:
    counts = await job_status_counts(session)
    queues = await queue_statistics(
        session, request.app.state.resources.redis, status_counts=counts
    )
    workers = await worker_health(
        session,
        request.app.state.resources.redis,
        processing_by_worker=queues.processing_by_worker,
    )
    terminal_statuses = {status.value for status in TERMINAL_JOB_STATUSES}
    terminal_jobs = sum(count for status, count in counts.items() if status in terminal_statuses)
    return OperationsSummary(
        generated_at=await database_now(session),
        jobs_by_status=counts,
        outstanding_jobs=queues.durable_outstanding,
        terminal_jobs=terminal_jobs,
        active_workers=len(workers),
        queues=queues,
    )


@router.get("/queues", response_model=QueueStatistics)
async def get_queue_statistics(
    request: Request,
    _admin: Annotated[ApiKey, Depends(require_scope("admin"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> QueueStatistics:
    return await queue_statistics(session, request.app.state.resources.redis)


@router.get("/workers", response_model=WorkerHealthListResponse)
async def get_worker_health(
    request: Request,
    _admin: Annotated[ApiKey, Depends(require_scope("admin"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> WorkerHealthListResponse:
    workers = await worker_health(session, request.app.state.resources.redis)
    return WorkerHealthListResponse(items=workers)


@router.get("/performance", response_model=PerformanceStatistics)
async def get_performance_statistics(
    _admin: Annotated[ApiKey, Depends(require_scope("admin"))],
    session: Annotated[AsyncSession, Depends(database_session)],
    window_minutes: Annotated[int, Query(ge=1, le=10_080)] = 60,
) -> PerformanceStatistics:
    return await performance_statistics(session, window_minutes=window_minutes)


@router.get("/metrics", response_class=Response)
async def get_prometheus_metrics(
    request: Request,
    _admin: Annotated[ApiKey, Depends(require_scope("admin"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> Response:
    counts = await job_status_counts(session)
    queues = await queue_statistics(
        session, request.app.state.resources.redis, status_counts=counts
    )
    workers = await worker_health(
        session,
        request.app.state.resources.redis,
        processing_by_worker=queues.processing_by_worker,
    )
    return Response(
        content=render_prometheus_metrics(
            counts,
            queues,
            len(workers),
            await process_metric_counts(session),
            await operational_rates(session),
            await job_type_permit_usage(
                request.app.state.resources.redis,
                request.app.state.resources.settings.job_type_concurrency_limits,
            ),
        ),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/events", response_class=StreamingResponse)
async def stream_operational_events(
    request: Request,
    _admin: Annotated[ApiKey, Depends(require_scope_detached("admin"))],
    after: Annotated[int | None, Query(ge=0)] = None,
    once: bool = False,
) -> StreamingResponse:
    resources = request.app.state.resources
    if after is None:
        async with resources.sessions() as session:
            cursor = int(await session.scalar(select(func.max(JobTransition.id))) or 0)
    else:
        cursor = after

    async def events() -> AsyncIterator[str]:
        nonlocal cursor
        quiet_polls = 0
        while True:
            async with resources.sessions() as session:
                transitions = list(
                    await session.scalars(
                        select(JobTransition)
                        .where(JobTransition.id > cursor)
                        .order_by(JobTransition.id)
                        .limit(100)
                    )
                )
            for transition in transitions:
                cursor = transition.id
                data = json.dumps(
                    jsonable_encoder(
                        {
                            "id": transition.id,
                            "job_id": transition.job_id,
                            "attempt_id": transition.attempt_id,
                            "from_status": transition.from_status,
                            "to_status": transition.to_status,
                            "reason": transition.reason,
                            "actor_type": transition.actor_type,
                            "created_at": transition.created_at,
                        }
                    ),
                    separators=(",", ":"),
                )
                yield f"id: {transition.id}\nevent: job.transition\ndata: {data}\n\n"
            if once:
                return
            quiet_polls = 0 if transitions else quiet_polls + 1
            if quiet_polls >= 15:
                yield ": keep-alive\n\n"
                quiet_polls = 0
            if await request.is_disconnected():
                return
            await asyncio.sleep(resources.settings.operational_stream_poll_ms / 1000)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
