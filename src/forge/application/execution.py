import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor
from datetime import datetime, timedelta
from functools import partial
from typing import cast
from uuid import uuid7

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forge.domain.handlers import ExecutionMode, HandlerDefinition
from forge.domain.jobs import FailureKind, JobStatus, TransitionActor
from forge.errors.execution import (
    AttemptClaimError,
    AttemptOwnershipLostError,
    HandlerCancelledError,
    HandlerTimedOutError,
    InvalidHandlerResultError,
    InvalidQueueMessageError,
    JobTypePermitLostError,
    PermanentHandlerError,
    TemporaryHandlerError,
)
from forge.persistence.models import Job, JobAttempt, JobTransition
from forge.schemas.execution import ClaimedJob, LeaseRenewalOutcome, ReservedQueueMessage
from forge.schemas.outbox import QueueMessage


def normalize_handler_result(result: object) -> dict[str, object]:
    if not isinstance(result, dict):
        raise InvalidHandlerResultError("handler result must be a JSON object")
    try:
        encoded = json.dumps(result, allow_nan=False)
        normalized = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise InvalidHandlerResultError("handler result must contain valid JSON values") from error
    return cast(dict[str, object], normalized)


async def execute_handler(
    definition: HandlerDefinition,
    payload: dict[str, object],
    *,
    executor: Executor | None = None,
) -> dict[str, object]:
    validated_payload = definition.validate_payload(payload)
    try:
        if definition.execution_mode is ExecutionMode.ASYNC:
            invocation = cast(Awaitable[object], definition.handler(validated_payload))
            result = await asyncio.wait_for(invocation, timeout=definition.timeout_seconds)
        else:
            loop = asyncio.get_running_loop()
            invocation_function = cast(
                Callable[[], object], partial(definition.handler, validated_payload)
            )
            future = loop.run_in_executor(executor, invocation_function)
            result = await asyncio.wait_for(future, timeout=definition.timeout_seconds)
    except TimeoutError as error:
        raise HandlerTimedOutError(
            f"handler exceeded its {definition.timeout_seconds}-second timeout"
        ) from error
    return normalize_handler_result(result)


async def renew_attempt_lease(
    session: AsyncSession,
    *,
    claimed: ClaimedJob,
    lease_seconds: int,
) -> LeaseRenewalOutcome:
    job_status = await session.scalar(
        select(Job.status).where(
            Job.id == claimed.job_id,
            Job.active_attempt_id == claimed.attempt_id,
        )
    )
    if job_status == JobStatus.CANCELLATION_REQUESTED.value:
        return LeaseRenewalOutcome.CANCELLATION_REQUESTED
    if job_status != JobStatus.RUNNING.value:
        return LeaseRenewalOutcome.OWNERSHIP_LOST
    active_job = select(Job.id).where(
        Job.id == claimed.job_id,
        Job.status == JobStatus.RUNNING.value,
        Job.active_attempt_id == claimed.attempt_id,
    )
    result = await session.execute(
        update(JobAttempt)
        .where(
            JobAttempt.id == claimed.attempt_id,
            JobAttempt.job_id.in_(active_job),
            JobAttempt.status == "running",
            JobAttempt.ownership_token == claimed.ownership_token,
            JobAttempt.lease_expires_at > func.clock_timestamp(),
        )
        .values(
            lease_expires_at=func.clock_timestamp() + timedelta(seconds=lease_seconds),
            updated_at=func.clock_timestamp(),
        )
        .returning(JobAttempt.id)
    )
    if result.scalar_one_or_none() is None:
        return LeaseRenewalOutcome.OWNERSHIP_LOST
    return LeaseRenewalOutcome.RENEWED


async def _lock_owned_attempt(
    session: AsyncSession,
    claimed: ClaimedJob,
) -> tuple[Job, JobAttempt, datetime]:
    raw_database_now = await session.scalar(select(func.clock_timestamp()))
    if raw_database_now is None:
        raise RuntimeError("PostgreSQL did not return its current time")
    database_now = cast(datetime, raw_database_now)
    job = await session.scalar(select(Job).where(Job.id == claimed.job_id).with_for_update())
    attempt = await session.scalar(
        select(JobAttempt).where(JobAttempt.id == claimed.attempt_id).with_for_update()
    )
    if job is None or attempt is None:
        raise AttemptOwnershipLostError(f"ownership of attempt {claimed.attempt_id} was lost")
    owns_attempt = (
        job.status in {JobStatus.RUNNING.value, JobStatus.CANCELLATION_REQUESTED.value}
        and job.active_attempt_id == claimed.attempt_id
        and attempt.status == "running"
        and attempt.ownership_token == claimed.ownership_token
        and attempt.lease_expires_at is not None
        and attempt.lease_expires_at > database_now
    )
    if not owns_attempt:
        raise AttemptOwnershipLostError(f"ownership of attempt {claimed.attempt_id} was lost")
    return job, attempt, database_now


async def complete_attempt_success(
    session: AsyncSession,
    *,
    claimed: ClaimedJob,
    result: dict[str, object] | None,
    result_blob_key: str | None = None,
) -> None:
    if (result is None) == (result_blob_key is None):
        raise ValueError("successful attempt requires exactly one result location")
    job, attempt, database_now = await _lock_owned_attempt(session, claimed)
    if job.status != JobStatus.RUNNING.value:
        raise AttemptOwnershipLostError(f"job {job.id} no longer accepts a success result")
    attempt.status = "succeeded"
    attempt.finished_at = database_now
    attempt.updated_at = database_now
    job.status = JobStatus.SUCCEEDED.value
    job.result = result
    job.result_blob_key = result_blob_key
    job.active_attempt_id = None
    job.finished_at = database_now
    job.updated_at = database_now
    session.add(
        JobTransition(
            job_id=job.id,
            attempt_id=attempt.id,
            from_status=JobStatus.RUNNING.value,
            to_status=JobStatus.SUCCEEDED.value,
            reason="attempt_succeeded",
            actor_type=TransitionActor.WORKER.value,
            actor_id=attempt.worker_id,
            details={},
        )
    )
    await session.flush()


def retry_delay_ms(attempt_number: int, *, base_ms: int, cap_ms: int) -> int:
    maximum = min(cap_ms, base_ms * (2 ** max(0, attempt_number - 1)))
    return random.randint(0, maximum)


def classify_handler_failure(error: BaseException) -> tuple[FailureKind, bool, str]:
    if isinstance(error, HandlerCancelledError):
        return FailureKind.CANCELLED, False, "handler_cancelled"
    if isinstance(error, PermanentHandlerError):
        return FailureKind.PERMANENT, False, "permanent_handler_error"
    if isinstance(error, TemporaryHandlerError):
        return FailureKind.TEMPORARY, True, "temporary_handler_error"
    if isinstance(error, JobTypePermitLostError):
        return FailureKind.TEMPORARY, True, "capacity_permit_lost"
    return FailureKind.TEMPORARY, True, "unexpected_handler_error"


async def complete_attempt_failure(
    session: AsyncSession,
    *,
    claimed: ClaimedJob,
    error: BaseException,
    retry_base_ms: int,
    retry_cap_ms: int,
) -> JobStatus:
    job, attempt, database_now = await _lock_owned_attempt(session, claimed)
    previous_status = job.status
    failure_kind, retryable, error_code = classify_handler_failure(error)
    attempt.status = "cancelled" if failure_kind is FailureKind.CANCELLED else "failed"
    attempt.finished_at = database_now
    attempt.failure_kind = failure_kind.value
    attempt.error_code = error_code
    attempt.error_message = str(error)[:4096]
    attempt.retryable = retryable
    attempt.updated_at = database_now
    job.active_attempt_id = None

    if failure_kind is FailureKind.CANCELLED:
        next_status = JobStatus.CANCELLED
        reason = "attempt_cancelled"
    elif retryable and job.attempt_count < job.max_attempts:
        next_status = JobStatus.RETRY_SCHEDULED
        reason = "attempt_retry_scheduled"
        delay_ms = retry_delay_ms(
            job.attempt_count,
            base_ms=retry_base_ms,
            cap_ms=retry_cap_ms,
        )
        job.run_at = database_now + timedelta(milliseconds=delay_ms)
    elif retryable:
        next_status = JobStatus.DEAD_LETTERED
        reason = "attempts_exhausted"
    else:
        next_status = JobStatus.FAILED
        reason = "permanent_failure"

    job.status = next_status.value
    job.finished_at = (
        database_now
        if next_status in {JobStatus.CANCELLED, JobStatus.DEAD_LETTERED, JobStatus.FAILED}
        else None
    )
    job.updated_at = database_now
    session.add(
        JobTransition(
            job_id=job.id,
            attempt_id=attempt.id,
            from_status=previous_status,
            to_status=next_status.value,
            reason=reason,
            actor_type=TransitionActor.WORKER.value,
            actor_id=attempt.worker_id,
            details={"failure_kind": failure_kind.value, "error_code": error_code},
        )
    )
    await session.flush()
    return next_status


def parse_queue_message(reservation: ReservedQueueMessage) -> QueueMessage:
    try:
        return QueueMessage.model_validate_json(reservation.raw)
    except ValidationError as error:
        raise InvalidQueueMessageError("Redis entry is not a valid Forge queue message") from error


async def claim_job_attempt(
    session: AsyncSession,
    *,
    message: QueueMessage,
    worker_id: str,
    lease_seconds: int,
) -> ClaimedJob | None:
    job = await session.scalar(select(Job).where(Job.id == message.job_id).with_for_update())
    if job is None:
        return None
    if job.status != JobStatus.QUEUED.value:
        return None
    if job.attempt_count >= job.max_attempts:
        raise AttemptClaimError(f"queued job {job.id} has exhausted its attempt limit")
    if (
        job.job_type != message.job_type
        or job.handler_version != message.handler_version
        or job.priority != message.priority
    ):
        raise AttemptClaimError(f"queue message does not match durable job {job.id}")
    if job.payload is None and job.payload_blob_key is None:
        raise AttemptClaimError(f"job {job.id} payload has no storage location")

    database_now = await session.scalar(select(func.clock_timestamp()))
    if database_now is None:
        raise RuntimeError("PostgreSQL did not return its current time")
    attempt_id = uuid7()
    ownership_token = uuid7()
    attempt_number = job.attempt_count + 1
    attempt = JobAttempt(
        id=attempt_id,
        job_id=job.id,
        attempt_number=attempt_number,
        status="running",
        worker_id=worker_id,
        ownership_token=ownership_token,
        lease_expires_at=database_now + timedelta(seconds=lease_seconds),
        started_at=database_now,
    )
    session.add(attempt)
    await session.flush()

    job.status = JobStatus.RUNNING.value
    job.attempt_count = attempt_number
    job.active_attempt_id = attempt_id
    job.started_at = job.started_at or database_now
    job.updated_at = database_now
    session.add(
        JobTransition(
            job_id=job.id,
            attempt_id=attempt_id,
            from_status=JobStatus.QUEUED.value,
            to_status=JobStatus.RUNNING.value,
            reason="attempt_claimed",
            actor_type=TransitionActor.WORKER.value,
            actor_id=worker_id,
            details={"ownership_token": str(ownership_token)},
        )
    )
    await session.flush()
    return ClaimedJob(
        job_id=job.id,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        ownership_token=ownership_token,
        job_type=job.job_type,
        handler_version=job.handler_version,
        payload=dict(job.payload) if job.payload is not None else None,
        payload_blob_key=job.payload_blob_key,
    )
