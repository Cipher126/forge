import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import delete, func, select, text, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from forge.application.admission import claim_admission_slot, release_admission_slot
from forge.application.audit import record_audit_event
from forge.domain.handlers import HandlerDefinition, HandlerRegistry
from forge.domain.jobs import JobPriority, JobStatus, TransitionActor
from forge.errors.jobs import (
    IdempotencyConflictError,
    JobCancellationConflictError,
    JobNotFoundError,
    JobReplayConflictError,
    JobResultUnavailableError,
)
from forge.errors.payloads import InlinePayloadTooLargeError
from forge.observability.tracing import current_span_id, current_trace_id
from forge.persistence.models import (
    ApiKey,
    IdempotencyKey,
    Job,
    JobAttempt,
    JobTransition,
    OutboxEvent,
)
from forge.persistence.object_store import ObjectStore


@dataclass(frozen=True, slots=True)
class Submission:
    job_type: str
    payload: dict[str, Any]
    priority: JobPriority
    run_at: datetime | None
    max_attempts: int | None
    idempotency_key: str | None
    replayed_from_job_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    job: Job
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class JobPage:
    items: list[Job]
    next_cursor: UUID | None


MAX_ATTEMPTS_LIMIT = 20
def _visible_job_condition(api_key: ApiKey) -> ColumnElement[bool]:
    if "admin" in api_key.scopes:
        return true()
    return Job.api_key_id == api_key.id


async def list_jobs(
    session: AsyncSession,
    *,
    api_key: ApiKey,
    limit: int,
    cursor: UUID | None,
    status: JobStatus | None,
    job_type: str | None,
    priority: JobPriority | None,
) -> JobPage:
    query = select(Job).where(_visible_job_condition(api_key))
    if cursor is not None:
        query = query.where(Job.id < cursor)
    if status is not None:
        query = query.where(Job.status == status.value)
    if job_type is not None:
        query = query.where(Job.job_type == job_type)
    if priority is not None:
        query = query.where(Job.priority == priority.value)
    jobs = list((await session.scalars(query.order_by(Job.id.desc()).limit(limit + 1))).all())
    has_more = len(jobs) > limit
    items = jobs[:limit]
    return JobPage(
        items=items,
        next_cursor=items[-1].id if has_more else None,
    )


async def list_job_attempts(
    session: AsyncSession,
    *,
    job_id: UUID,
    api_key: ApiKey,
) -> list[JobAttempt]:
    job = await session.scalar(select(Job).where(Job.id == job_id, _visible_job_condition(api_key)))
    if job is None:
        raise JobNotFoundError(job_id)
    return list(
        (
            await session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_id == job_id)
                .order_by(JobAttempt.attempt_number)
            )
        ).all()
    )


async def list_job_transitions(
    session: AsyncSession,
    *,
    job_id: UUID,
    api_key: ApiKey,
) -> list[JobTransition]:
    job = await session.scalar(select(Job).where(Job.id == job_id, _visible_job_condition(api_key)))
    if job is None:
        raise JobNotFoundError(job_id)
    return list(
        (
            await session.scalars(
                select(JobTransition)
                .where(JobTransition.job_id == job_id)
                .order_by(JobTransition.id)
            )
        ).all()
    )


async def get_job_result(
    session: AsyncSession,
    *,
    job_id: UUID,
    api_key: ApiKey,
    object_store: ObjectStore,
) -> tuple[dict[str, Any], bool]:
    job = await session.scalar(select(Job).where(Job.id == job_id, _visible_job_condition(api_key)))
    if job is None:
        raise JobNotFoundError(job_id)
    if job.result is not None:
        return dict(job.result), False
    if job.result_blob_key is not None:
        return await object_store.get_json(job.result_blob_key), True
    raise JobResultUnavailableError("Job result is not available")


def _advisory_lock_key(api_key_id: object, idempotency_key: str) -> int:
    digest = hashlib.sha256(f"{api_key_id}:{idempotency_key}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _request_fingerprint(
    *,
    job_type: str,
    handler_version: str,
    payload: BaseModel,
    priority: JobPriority,
    run_at: datetime | None,
    max_attempts: int,
    replayed_from_job_id: UUID | None,
) -> bytes:
    canonical_request = {
        "handler_version": handler_version,
        "max_attempts": max_attempts,
        "payload": payload.model_dump(mode="json"),
        "priority": priority.value,
        "replayed_from_job_id": (
            str(replayed_from_job_id) if replayed_from_job_id is not None else None
        ),
        "run_at": run_at.isoformat() if run_at is not None else None,
        "type": job_type,
    }
    encoded = json.dumps(
        canonical_request,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).digest()


async def _resolve_existing_idempotency_key(
    session: AsyncSession,
    *,
    api_key: ApiKey,
    idempotency_key: str,
    fingerprint: bytes,
    database_now: datetime,
) -> Job | None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _advisory_lock_key(api_key.id, idempotency_key)},
    )
    existing = await session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.api_key_id == api_key.id,
            IdempotencyKey.idempotency_key == idempotency_key,
        )
    )
    if existing is None:
        return None
    if existing.expires_at <= database_now:
        await session.execute(delete(IdempotencyKey).where(IdempotencyKey.id == existing.id))
        await session.flush()
        return None
    if existing.request_fingerprint != fingerprint:
        raise IdempotencyConflictError("idempotency key was already used for another request")
    job = await session.get(Job, existing.job_id)
    if job is None:
        raise RuntimeError("idempotency record refers to a missing job")
    return job


async def submit_job(
    session: AsyncSession,
    *,
    handlers: HandlerRegistry,
    api_key: ApiKey,
    submission: Submission,
    backlog_limit: int | None = None,
    retry_after_seconds: int = 1,
    inline_payload_limit_bytes: int | None = None,
    object_store: ObjectStore | None = None,
    idempotency_retention_hours: int = 24,
) -> SubmissionResult:
    definition: HandlerDefinition = handlers.resolve(submission.job_type)
    validated_payload = definition.validate_payload(submission.payload)
    database_now = await session.scalar(select(func.clock_timestamp()))
    if database_now is None:
        raise RuntimeError("PostgreSQL did not return its current time")

    scheduled_for = (
        submission.run_at if submission.run_at and submission.run_at > database_now else None
    )
    max_attempts = (
        definition.default_max_attempts
        if submission.max_attempts is None
        else submission.max_attempts
    )
    normalized_payload = validated_payload.model_dump(mode="json")
    payload_size = len(validated_payload.model_dump_json().encode("utf-8"))
    if not 1 <= max_attempts <= MAX_ATTEMPTS_LIMIT:
        raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS_LIMIT}")

    fingerprint = _request_fingerprint(
        job_type=submission.job_type,
        handler_version=definition.version,
        payload=validated_payload,
        priority=submission.priority,
        run_at=scheduled_for,
        max_attempts=max_attempts,
        replayed_from_job_id=submission.replayed_from_job_id,
    )
    if submission.idempotency_key is not None:
        existing_job = await _resolve_existing_idempotency_key(
            session,
            api_key=api_key,
            idempotency_key=submission.idempotency_key,
            fingerprint=fingerprint,
            database_now=database_now,
        )
        if existing_job is not None:
            return SubmissionResult(job=existing_job, deduplicated=True)

    job_id = uuid7()
    if backlog_limit is not None:
        await claim_admission_slot(
            session,
            job_id=job_id,
            limit=backlog_limit,
            retry_after_seconds=retry_after_seconds,
        )

    payload_blob_key: str | None = None
    inline_payload: dict[str, Any] | None = normalized_payload
    if inline_payload_limit_bytes is not None and payload_size > inline_payload_limit_bytes:
        if object_store is None:
            raise InlinePayloadTooLargeError(
                size_bytes=payload_size,
                limit_bytes=inline_payload_limit_bytes,
            )
        payload_blob_key = await object_store.put_json("payloads", normalized_payload)
        inline_payload = None

    status = JobStatus.SCHEDULED if scheduled_for is not None else JobStatus.QUEUED
    job = Job(
        id=job_id,
        api_key_id=api_key.id,
        replayed_from_job_id=submission.replayed_from_job_id,
        job_type=submission.job_type,
        handler_version=definition.version,
        status=status.value,
        priority=submission.priority.value,
        payload=inline_payload,
        payload_blob_key=payload_blob_key,
        max_attempts=max_attempts,
        attempt_count=0,
        run_at=scheduled_for,
        queued_at=database_now if status is JobStatus.QUEUED else None,
    )
    session.add(job)
    # These models use explicit foreign-key columns without ORM relationships,
    # so make the parent insert order explicit while remaining in one transaction.
    await session.flush()
    session.add(
        JobTransition(
            job_id=job.id,
            from_status=None,
            to_status=status.value,
            reason=("dead_letter_replayed" if submission.replayed_from_job_id else "job_submitted"),
            actor_type=TransitionActor.API.value,
            actor_id=api_key.public_id,
            details=(
                {"replayed_from_job_id": str(submission.replayed_from_job_id)}
                if submission.replayed_from_job_id
                else {}
            ),
        )
    )
    if submission.idempotency_key is not None:
        session.add(
            IdempotencyKey(
                api_key_id=api_key.id,
                idempotency_key=submission.idempotency_key,
                request_fingerprint=fingerprint,
                job_id=job.id,
                expires_at=database_now + timedelta(hours=idempotency_retention_hours),
            )
        )
    if status is JobStatus.QUEUED:
        session.add(
            OutboxEvent(
                id=uuid7(),
                job_id=job.id,
                event_type="job.queued",
                payload={
                    "job_id": str(job.id),
                    "job_type": job.job_type,
                    "handler_version": job.handler_version,
                    "priority": job.priority,
                    "trace_id": current_trace_id(),
                    "parent_span_id": current_span_id(),
                },
                available_at=database_now,
            )
        )
    await session.flush()
    await session.refresh(job)
    return SubmissionResult(job=job, deduplicated=False)


async def cancel_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    api_key: ApiKey,
) -> Job:
    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or (job.api_key_id != api_key.id and "admin" not in api_key.scopes):
        raise JobNotFoundError(job_id)
    if job.status in {JobStatus.CANCELLED.value, JobStatus.CANCELLATION_REQUESTED.value}:
        return job
    if job.status in {
        JobStatus.SUCCEEDED.value,
        JobStatus.FAILED.value,
        JobStatus.DEAD_LETTERED.value,
    }:
        raise JobCancellationConflictError(f"job in {job.status!r} state cannot be cancelled")

    database_now = await session.scalar(select(func.clock_timestamp()))
    if database_now is None:
        raise RuntimeError("PostgreSQL did not return its current time")
    previous_status = job.status
    if previous_status == JobStatus.RUNNING.value:
        job.status = JobStatus.CANCELLATION_REQUESTED.value
        reason = "cancellation_requested"
    else:
        job.status = JobStatus.CANCELLED.value
        job.finished_at = database_now
        await release_admission_slot(session, job.id)
        reason = "job_cancelled"
    job.updated_at = database_now
    session.add(
        JobTransition(
            job_id=job.id,
            attempt_id=job.active_attempt_id,
            from_status=previous_status,
            to_status=job.status,
            reason=reason,
            actor_type=TransitionActor.API.value,
            actor_id=api_key.public_id,
            details={},
        )
    )
    record_audit_event(
        session,
        actor=api_key,
        action="job.cancellation_requested",
        resource_type="job",
        resource_id=str(job.id),
        details={"from_status": previous_status, "to_status": job.status},
    )
    await session.flush()
    return job


async def replay_dead_lettered_job(
    session: AsyncSession,
    *,
    handlers: HandlerRegistry,
    source_job_id: UUID,
    api_key: ApiKey,
    idempotency_key: str | None,
    backlog_limit: int | None = None,
    retry_after_seconds: int = 1,
    inline_payload_limit_bytes: int | None = None,
    object_store: ObjectStore | None = None,
    idempotency_retention_hours: int = 24,
) -> SubmissionResult:
    source = await session.scalar(select(Job).where(Job.id == source_job_id).with_for_update())
    if source is None or (source.api_key_id != api_key.id and "admin" not in api_key.scopes):
        raise JobNotFoundError(source_job_id)
    if source.status != JobStatus.DEAD_LETTERED.value:
        raise JobReplayConflictError("only dead-lettered jobs can be replayed")
    if source.payload is not None:
        source_payload = dict(source.payload)
    elif source.payload_blob_key is not None and object_store is not None:
        source_payload = await object_store.get_json(source.payload_blob_key)
    else:
        raise JobReplayConflictError("job payload is unavailable for replay")
    result = await submit_job(
        session,
        handlers=handlers,
        api_key=api_key,
        submission=Submission(
            job_type=source.job_type,
            payload=source_payload,
            priority=JobPriority(source.priority),
            run_at=None,
            max_attempts=source.max_attempts,
            idempotency_key=idempotency_key,
            replayed_from_job_id=source.id,
        ),
        backlog_limit=backlog_limit,
        retry_after_seconds=retry_after_seconds,
        inline_payload_limit_bytes=inline_payload_limit_bytes,
        object_store=object_store,
        idempotency_retention_hours=idempotency_retention_hours,
    )
    if not result.deduplicated:
        record_audit_event(
            session,
            actor=api_key,
            action="job.replayed",
            resource_type="job",
            resource_id=str(result.job.id),
            details={"source_job_id": str(source.id)},
        )
        await session.flush()
    return result
