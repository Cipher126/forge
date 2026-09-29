from uuid import UUID, uuid7

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.domain.jobs import JobStatus, TransitionActor
from forge.observability.tracing import current_span_id, current_trace_id
from forge.persistence.models import Job, JobTransition, OutboxEvent


async def promote_due_jobs(
    session: AsyncSession,
    *,
    batch_size: int,
) -> list[UUID]:
    database_now = await session.scalar(select(func.clock_timestamp()))
    if database_now is None:
        raise RuntimeError("PostgreSQL did not return its current time")

    jobs = list(
        await session.scalars(
            select(Job)
            .where(
                Job.status.in_([JobStatus.SCHEDULED.value, JobStatus.RETRY_SCHEDULED.value]),
                Job.run_at <= database_now,
            )
            .order_by(Job.run_at, Job.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        previous_status = job.status
        job.status = JobStatus.QUEUED.value
        job.queued_at = database_now
        job.updated_at = database_now
        session.add(
            JobTransition(
                job_id=job.id,
                from_status=previous_status,
                to_status=JobStatus.QUEUED.value,
                reason="scheduled_time_reached",
                actor_type=TransitionActor.SCHEDULER.value,
                actor_id=None,
                details={"scheduled_for": job.run_at.isoformat() if job.run_at else None},
            )
        )
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
    return [job.id for job in jobs]
