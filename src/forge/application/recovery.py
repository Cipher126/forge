from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.application.admission import release_admission_slot
from forge.application.execution import retry_delay_ms
from forge.domain.jobs import FailureKind, JobStatus, TransitionActor
from forge.persistence.models import Job, JobAttempt, JobTransition


async def recover_expired_attempts(
    session: AsyncSession,
    *,
    batch_size: int,
    retry_base_ms: int,
    retry_cap_ms: int,
) -> list[UUID]:
    database_now = await session.scalar(select(func.clock_timestamp()))
    if database_now is None:
        raise RuntimeError("PostgreSQL did not return its current time")
    jobs = list(
        await session.scalars(
            select(Job)
            .join(JobAttempt, JobAttempt.id == Job.active_attempt_id)
            .where(
                Job.status.in_([JobStatus.RUNNING.value, JobStatus.CANCELLATION_REQUESTED.value]),
                JobAttempt.status == "running",
                JobAttempt.lease_expires_at <= database_now,
            )
            .order_by(JobAttempt.lease_expires_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=Job)
        )
    )
    recovered: list[UUID] = []
    for job in jobs:
        attempt = await session.scalar(
            select(JobAttempt).where(JobAttempt.id == job.active_attempt_id).with_for_update()
        )
        if (
            attempt is None
            or attempt.status != "running"
            or attempt.lease_expires_at is None
            or attempt.lease_expires_at > database_now
        ):
            continue
        previous_status = job.status
        attempt.status = "abandoned"
        attempt.finished_at = database_now
        attempt.failure_kind = FailureKind.WORKER_LOST.value
        attempt.error_code = "attempt_lease_expired"
        attempt.error_message = "worker did not renew the attempt lease"
        attempt.updated_at = database_now
        job.active_attempt_id = None

        if previous_status == JobStatus.CANCELLATION_REQUESTED.value:
            next_status = JobStatus.CANCELLED
            reason = "cancelled_after_worker_loss"
            attempt.retryable = False
            job.finished_at = database_now
        elif job.attempt_count < job.max_attempts:
            next_status = JobStatus.RETRY_SCHEDULED
            reason = "worker_lost_retry_scheduled"
            attempt.retryable = True
            delay = retry_delay_ms(
                job.attempt_count,
                base_ms=retry_base_ms,
                cap_ms=retry_cap_ms,
            )
            job.run_at = database_now + timedelta(milliseconds=delay)
        else:
            next_status = JobStatus.DEAD_LETTERED
            reason = "worker_lost_attempts_exhausted"
            attempt.retryable = False
            job.finished_at = database_now

        job.status = next_status.value
        job.updated_at = database_now
        if next_status in {JobStatus.CANCELLED, JobStatus.DEAD_LETTERED}:
            await release_admission_slot(session, job.id)
        session.add(
            JobTransition(
                job_id=job.id,
                attempt_id=attempt.id,
                from_status=previous_status,
                to_status=next_status.value,
                reason=reason,
                actor_type=TransitionActor.RECOVERY.value,
                actor_id=None,
                details={
                    "expired_ownership_token": str(attempt.ownership_token),
                    "lease_expired_at": attempt.lease_expires_at.isoformat(),
                },
            )
        )
        recovered.append(job.id)
    await session.flush()
    return recovered
