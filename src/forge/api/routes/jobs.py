from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.dependencies.auth import database_session, require_scope
from forge.application.jobs import (
    Submission,
    cancel_job,
    get_job_result,
    list_job_attempts,
    list_job_transitions,
    list_jobs,
    replay_dead_lettered_job,
    submit_job,
)
from forge.domain.jobs import JobPriority, JobStatus
from forge.errors.jobs import JobNotFoundError
from forge.persistence.models import ApiKey, Job
from forge.schemas.jobs import (
    JobAttemptListResponse,
    JobAttemptResponse,
    JobListResponse,
    JobResponse,
    JobResultResponse,
    JobTransitionListResponse,
    JobTransitionResponse,
    SubmitJobRequest,
)

router = APIRouter(prefix="/v1/jobs", tags=["Jobs"])


@router.get("", response_model=JobListResponse)
async def get_jobs(
    api_key: Annotated[ApiKey, Depends(require_scope("jobs:read"))],
    session: Annotated[AsyncSession, Depends(database_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: UUID | None = None,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    job_type: Annotated[str | None, Query(alias="type", min_length=1, max_length=128)] = None,
    priority: JobPriority | None = None,
) -> JobListResponse:
    page = await list_jobs(
        session,
        api_key=api_key,
        limit=limit,
        cursor=cursor,
        status=job_status,
        job_type=job_type,
        priority=priority,
    )
    return JobListResponse(
        items=[JobResponse.model_validate(job) for job in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    body: SubmitJobRequest,
    request: Request,
    response: Response,
    api_key: Annotated[ApiKey, Depends(require_scope("jobs:write"))],
    session: Annotated[AsyncSession, Depends(database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ] = None,
) -> JobResponse:
    try:
        result = await submit_job(
            session,
            handlers=request.app.state.resources.handlers,
            api_key=api_key,
            submission=Submission(
                job_type=body.type,
                payload=body.payload,
                priority=body.priority,
                run_at=body.run_at,
                max_attempts=body.max_attempts,
                idempotency_key=idempotency_key,
            ),
            backlog_limit=request.app.state.resources.settings.admission_backlog_limit,
            retry_after_seconds=(
                request.app.state.resources.settings.admission_retry_after_seconds
            ),
            inline_payload_limit_bytes=(
                request.app.state.resources.settings.max_inline_payload_bytes
            ),
            object_store=request.app.state.resources.object_store,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    if result.deduplicated:
        response.status_code = status.HTTP_200_OK
    return JobResponse.model_validate(result.job)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    api_key: Annotated[ApiKey, Depends(require_scope("jobs:read"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> JobResponse:
    job = await session.get(Job, job_id)
    if job is None or (job.api_key_id != api_key.id and "admin" not in api_key.scopes):
        raise JobNotFoundError(job_id)
    return JobResponse.model_validate(job)


@router.get("/{job_id}/result", response_model=JobResultResponse)
async def get_existing_job_result(
    job_id: UUID,
    request: Request,
    api_key: Annotated[ApiKey, Depends(require_scope("jobs:read"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> JobResultResponse:
    result, stored_externally = await get_job_result(
        session,
        job_id=job_id,
        api_key=api_key,
        object_store=request.app.state.resources.object_store,
    )
    return JobResultResponse(result=result, stored_externally=stored_externally)


@router.get("/{job_id}/attempts", response_model=JobAttemptListResponse)
async def get_job_attempts(
    job_id: UUID,
    api_key: Annotated[ApiKey, Depends(require_scope("jobs:read"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> JobAttemptListResponse:
    attempts = await list_job_attempts(session, job_id=job_id, api_key=api_key)
    return JobAttemptListResponse(
        items=[JobAttemptResponse.model_validate(attempt) for attempt in attempts]
    )


@router.get("/{job_id}/transitions", response_model=JobTransitionListResponse)
async def get_job_transitions(
    job_id: UUID,
    api_key: Annotated[ApiKey, Depends(require_scope("jobs:read"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> JobTransitionListResponse:
    transitions = await list_job_transitions(session, job_id=job_id, api_key=api_key)
    return JobTransitionListResponse(
        items=[JobTransitionResponse.model_validate(transition) for transition in transitions]
    )


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_existing_job(
    job_id: UUID,
    api_key: Annotated[ApiKey, Depends(require_scope("jobs:write"))],
    session: Annotated[AsyncSession, Depends(database_session)],
) -> JobResponse:
    try:
        job = await cancel_job(session, job_id=job_id, api_key=api_key)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return JobResponse.model_validate(job)


@router.post("/{job_id}/replay", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def replay_job(
    job_id: UUID,
    request: Request,
    response: Response,
    api_key: Annotated[ApiKey, Depends(require_scope("jobs:write"))],
    session: Annotated[AsyncSession, Depends(database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ] = None,
) -> JobResponse:
    try:
        result = await replay_dead_lettered_job(
            session,
            handlers=request.app.state.resources.handlers,
            source_job_id=job_id,
            api_key=api_key,
            idempotency_key=idempotency_key,
            backlog_limit=request.app.state.resources.settings.admission_backlog_limit,
            retry_after_seconds=(
                request.app.state.resources.settings.admission_retry_after_seconds
            ),
            inline_payload_limit_bytes=(
                request.app.state.resources.settings.max_inline_payload_bytes
            ),
            object_store=request.app.state.resources.object_store,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if result.deduplicated:
        response.status_code = status.HTTP_200_OK
    return JobResponse.model_validate(result.job)
