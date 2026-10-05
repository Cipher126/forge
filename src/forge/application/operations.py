import math
from datetime import datetime, timedelta
from typing import Any, cast

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.domain.jobs import TERMINAL_JOB_STATUSES, JobStatus
from forge.persistence.models import Job, JobAttempt, JobTransition, OutboxEvent
from forge.queue.names import PRIORITY_CYCLE, job_type_permits_key, queue_key
from forge.schemas.operations import (
    DurationDistribution,
    JobTypePermitUsage,
    OperationalRates,
    PerformanceStatistics,
    QueueStatistics,
    WorkerHealth,
)

HEARTBEAT_PREFIX = "forge:worker:"
HEARTBEAT_SUFFIX = ":heartbeat"
PROCESSING_PREFIX = "forge:processing:"
METRICS_RATE_WINDOW_MINUTES = 5


async def database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    if value is None:
        raise RuntimeError("PostgreSQL did not return its current time")
    return cast(datetime, value)


def _distribution(row: Any) -> DurationDistribution:
    return DurationDistribution(
        sample_count=int(row.sample_count),
        p50_seconds=float(row.p50) if row.p50 is not None else None,
        p95_seconds=float(row.p95) if row.p95 is not None else None,
    )


async def _duration_distribution(
    session: AsyncSession, duration: Any, *conditions: Any
) -> DurationDistribution:
    row = (
        await session.execute(
            select(
                func.count().label("sample_count"),
                func.percentile_cont(0.5).within_group(duration).label("p50"),
                func.percentile_cont(0.95).within_group(duration).label("p95"),
            ).where(*conditions)
        )
    ).one()
    return _distribution(row)


async def performance_statistics(
    session: AsyncSession, *, window_minutes: int
) -> PerformanceStatistics:
    now = await database_now(session)
    cutoff = now - timedelta(minutes=window_minutes)
    submitted = int(
        await session.scalar(select(func.count()).select_from(Job).where(Job.created_at >= cutoff))
        or 0
    )
    terminal_counts_rows = await session.execute(
        select(Job.status, func.count())
        .where(
            Job.finished_at >= cutoff,
            Job.status.in_([status.value for status in TERMINAL_JOB_STATUSES]),
        )
        .group_by(Job.status)
    )
    terminal_counts = {status: int(count) for status, count in terminal_counts_rows.tuples()}
    succeeded = terminal_counts.get(JobStatus.SUCCEEDED.value, 0)
    failed = terminal_counts.get(JobStatus.FAILED.value, 0)
    dead_lettered = terminal_counts.get(JobStatus.DEAD_LETTERED.value, 0)
    cancelled = terminal_counts.get(JobStatus.CANCELLED.value, 0)
    completed = sum(terminal_counts.values())
    retries = int(
        await session.scalar(
            select(func.count())
            .select_from(JobTransition)
            .where(
                JobTransition.to_status == JobStatus.RETRY_SCHEDULED.value,
                JobTransition.created_at >= cutoff,
            )
        )
        or 0
    )
    queue_wait_duration = func.extract("epoch", Job.started_at - Job.queued_at)
    execution_duration = func.extract("epoch", Job.finished_at - Job.started_at)
    scheduling_lag_duration = func.extract("epoch", Job.queued_at - Job.run_at)
    queue_wait = await _duration_distribution(
        session,
        queue_wait_duration,
        Job.started_at.is_not(None),
        Job.queued_at.is_not(None),
        Job.started_at >= cutoff,
    )
    execution_time = await _duration_distribution(
        session,
        execution_duration,
        Job.finished_at.is_not(None),
        Job.started_at.is_not(None),
        Job.finished_at >= cutoff,
    )
    scheduling_lag = await _duration_distribution(
        session,
        scheduling_lag_duration,
        Job.run_at.is_not(None),
        Job.queued_at.is_not(None),
        Job.queued_at >= cutoff,
        Job.queued_at >= Job.run_at,
    )
    return PerformanceStatistics(
        generated_at=now,
        window_minutes=window_minutes,
        submitted_jobs=submitted,
        completed_jobs=completed,
        succeeded_jobs=succeeded,
        failed_jobs=failed,
        dead_lettered_jobs=dead_lettered,
        cancelled_jobs=cancelled,
        retries_scheduled=retries,
        success_rate=succeeded / completed if completed else None,
        throughput_per_minute=completed / window_minutes,
        queue_wait=queue_wait,
        execution_time=execution_time,
        scheduling_lag=scheduling_lag,
    )


def render_prometheus_metrics(
    counts: dict[str, int],
    queues: QueueStatistics,
    active_workers: int,
    process_metrics: dict[str, int],
    rates: OperationalRates,
    permit_usage: dict[str, JobTypePermitUsage],
) -> str:
    lines = [
        "# HELP forge_jobs Current durable jobs by status.",
        "# TYPE forge_jobs gauge",
        *(f'forge_jobs{{status="{status}"}} {count}' for status, count in counts.items()),
        "# HELP forge_queue_messages Current Redis queue messages by priority.",
        "# TYPE forge_queue_messages gauge",
        *(
            f'forge_queue_messages{{priority="{priority}"}} {count}'
            for priority, count in queues.queued_by_priority.items()
        ),
        "# HELP forge_processing_messages Messages reserved by workers.",
        "# TYPE forge_processing_messages gauge",
        f"forge_processing_messages {queues.processing_total}",
        "# HELP forge_jobs_outstanding Current non-terminal durable jobs.",
        "# TYPE forge_jobs_outstanding gauge",
        f"forge_jobs_outstanding {queues.durable_outstanding}",
        "# HELP forge_workers_online Workers with a live heartbeat.",
        "# TYPE forge_workers_online gauge",
        f"forge_workers_online {active_workers}",
        "# HELP forge_outbox_unpublished Unpublished durable outbox events.",
        "# TYPE forge_outbox_unpublished gauge",
        f"forge_outbox_unpublished {process_metrics['outbox_unpublished']}",
        "# HELP forge_attempts_running Currently running database attempts.",
        "# TYPE forge_attempts_running gauge",
        f"forge_attempts_running {process_metrics['attempts_running']}",
        "# HELP forge_jobs_due Scheduled jobs whose run time has passed.",
        "# TYPE forge_jobs_due gauge",
        f"forge_jobs_due {process_metrics['jobs_due']}",
        "# HELP forge_attempt_leases_expired Running attempts with expired leases.",
        "# TYPE forge_attempt_leases_expired gauge",
        f"forge_attempt_leases_expired {process_metrics['attempt_leases_expired']}",
        "# HELP forge_jobs_enqueued_rate_per_minute "
        "Jobs entering a runnable queue per minute over the metrics window.",
        "# TYPE forge_jobs_enqueued_rate_per_minute gauge",
        f"forge_jobs_enqueued_rate_per_minute {rates.enqueued_per_minute}",
        "# HELP forge_jobs_completed_rate_per_minute "
        "Jobs entering a terminal state per minute over the metrics window.",
        "# TYPE forge_jobs_completed_rate_per_minute gauge",
        f"forge_jobs_completed_rate_per_minute {rates.completed_per_minute}",
        "# HELP forge_job_failure_ratio Failed or dead-lettered completions divided by "
        "non-cancelled completions over the metrics window.",
        "# TYPE forge_job_failure_ratio gauge",
        f"forge_job_failure_ratio {rates.failure_ratio}",
        "# HELP forge_metrics_rate_window_minutes Window used for Forge rate and ratio metrics.",
        "# TYPE forge_metrics_rate_window_minutes gauge",
        f"forge_metrics_rate_window_minutes {rates.window_minutes}",
        "# HELP forge_dead_letter_jobs Current durable jobs in the dead-letter state.",
        "# TYPE forge_dead_letter_jobs gauge",
        f"forge_dead_letter_jobs {counts[JobStatus.DEAD_LETTERED.value]}",
        "# HELP forge_job_type_permits_used Active concurrency permits by configured job type.",
        "# TYPE forge_job_type_permits_used gauge",
        *(
            f'forge_job_type_permits_used{{job_type="{_prometheus_label(job_type)}"}} {usage.used}'
            for job_type, usage in sorted(permit_usage.items())
        ),
        "# HELP forge_job_type_permits_limit Configured concurrency permit limit by job type.",
        "# TYPE forge_job_type_permits_limit gauge",
        *(
            "forge_job_type_permits_limit"
            f'{{job_type="{_prometheus_label(job_type)}"}} {usage.limit}'
            for job_type, usage in sorted(permit_usage.items())
        ),
        "# HELP forge_job_type_permit_utilization_ratio "
        "Active permits divided by the configured limit.",
        "# TYPE forge_job_type_permit_utilization_ratio gauge",
        *(
            "forge_job_type_permit_utilization_ratio"
            f'{{job_type="{_prometheus_label(job_type)}"}} {usage.used / usage.limit}'
            for job_type, usage in sorted(permit_usage.items())
        ),
    ]
    return "\n".join(lines) + "\n"


def _prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


async def operational_rates(
    session: AsyncSession,
    *,
    window_minutes: int = METRICS_RATE_WINDOW_MINUTES,
) -> OperationalRates:
    cutoff = await database_now(session) - timedelta(minutes=window_minutes)
    rows = await session.execute(
        select(JobTransition.to_status, func.count())
        .where(JobTransition.created_at >= cutoff)
        .group_by(JobTransition.to_status)
    )
    counts = {status: int(count) for status, count in rows.tuples()}
    enqueued = counts.get(JobStatus.QUEUED.value, 0)
    completed = sum(counts.get(status.value, 0) for status in TERMINAL_JOB_STATUSES)
    failed = counts.get(JobStatus.FAILED.value, 0) + counts.get(
        JobStatus.DEAD_LETTERED.value, 0
    )
    non_cancelled = completed - counts.get(JobStatus.CANCELLED.value, 0)
    return OperationalRates(
        window_minutes=window_minutes,
        enqueued_per_minute=enqueued / window_minutes,
        completed_per_minute=completed / window_minutes,
        failure_ratio=failed / non_cancelled if non_cancelled else 0.0,
    )


async def job_type_permit_usage(
    redis: Redis,
    limits: dict[str, int],
) -> dict[str, JobTypePermitUsage]:
    seconds, microseconds = await redis.time()
    now = math.fsum((float(seconds), float(microseconds) / 1_000_000))
    usage: dict[str, JobTypePermitUsage] = {}
    for job_type, limit in limits.items():
        used = int(await redis.zcount(job_type_permits_key(job_type), f"({now}", "+inf"))
        usage[job_type] = JobTypePermitUsage(used=used, limit=limit)
    return usage


async def process_metric_counts(session: AsyncSession) -> dict[str, int]:
    now = await database_now(session)
    values = await session.execute(
        select(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .scalar_subquery(),
            select(func.count())
            .select_from(JobAttempt)
            .where(JobAttempt.status == "running")
            .scalar_subquery(),
            select(func.count())
            .select_from(Job)
            .where(
                Job.status.in_([JobStatus.SCHEDULED.value, JobStatus.RETRY_SCHEDULED.value]),
                Job.run_at <= now,
            )
            .scalar_subquery(),
            select(func.count())
            .select_from(JobAttempt)
            .where(
                JobAttempt.status == "running",
                JobAttempt.lease_expires_at <= now,
            )
            .scalar_subquery(),
        )
    )
    outbox, running, due, expired = values.one()
    return {
        "outbox_unpublished": int(outbox),
        "attempts_running": int(running),
        "jobs_due": int(due),
        "attempt_leases_expired": int(expired),
    }


async def job_status_counts(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(select(Job.status, func.count()).group_by(Job.status))
    observed = {status: count for status, count in rows.tuples()}
    return {status.value: int(observed.get(status.value, 0)) for status in JobStatus}


async def _key_lengths(redis: Redis, pattern: str, prefix: str) -> dict[str, int]:
    result: dict[str, int] = {}
    async for raw_key in redis.scan_iter(match=pattern):
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        result[key.removeprefix(prefix)] = int(await redis.llen(key))
    return result


async def queue_statistics(
    session: AsyncSession,
    redis: Redis,
    *,
    status_counts: dict[str, int] | None = None,
) -> QueueStatistics:
    priorities = tuple(dict.fromkeys(PRIORITY_CYCLE))
    queued_by_priority = {
        priority: int(await redis.llen(queue_key(priority))) for priority in priorities
    }
    processing = await _key_lengths(redis, f"{PROCESSING_PREFIX}*", PROCESSING_PREFIX)
    counts = status_counts if status_counts is not None else await job_status_counts(session)
    terminal = {status.value for status in TERMINAL_JOB_STATUSES}
    durable_outstanding = sum(count for status, count in counts.items() if status not in terminal)
    return QueueStatistics(
        queued_by_priority=queued_by_priority,
        queued_total=sum(queued_by_priority.values()),
        processing_by_worker=processing,
        processing_total=sum(processing.values()),
        durable_outstanding=durable_outstanding,
    )


async def worker_health(
    session: AsyncSession,
    redis: Redis,
    *,
    processing_by_worker: dict[str, int] | None = None,
) -> list[WorkerHealth]:
    processing = processing_by_worker
    if processing is None:
        processing = await _key_lengths(redis, f"{PROCESSING_PREFIX}*", PROCESSING_PREFIX)
    active_rows = await session.execute(
        select(JobAttempt.worker_id, func.count())
        .where(JobAttempt.status == "running")
        .group_by(JobAttempt.worker_id)
    )
    active = {worker_id: int(count) for worker_id, count in active_rows.tuples()}
    workers: list[WorkerHealth] = []
    async for raw_key in redis.scan_iter(match=f"{HEARTBEAT_PREFIX}*{HEARTBEAT_SUFFIX}"):
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        worker_id = key.removeprefix(HEARTBEAT_PREFIX).removesuffix(HEARTBEAT_SUFFIX)
        ttl = int(await redis.ttl(key))
        if ttl < 0:
            continue
        workers.append(
            WorkerHealth(
                worker_id=worker_id,
                status="online",
                heartbeat_ttl_seconds=ttl,
                active_attempts=active.get(worker_id, 0),
                reserved_messages=processing.get(worker_id, 0),
            )
        )
    return sorted(workers, key=lambda worker: worker.worker_id)
