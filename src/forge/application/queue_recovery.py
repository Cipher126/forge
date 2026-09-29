from typing import Literal, cast
from uuid import UUID, uuid7

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.application.execution import parse_queue_message
from forge.domain.jobs import JobStatus
from forge.errors.execution import InvalidQueueMessageError
from forge.observability.tracing import current_trace_id
from forge.persistence.models import Job
from forge.queue.names import PRIORITY_CYCLE, queue_key
from forge.schemas.execution import ReservedQueueMessage
from forge.schemas.outbox import QueueMessage

PROCESSING_PATTERN = "forge:processing:*"


async def _delivered_job_ids(redis: Redis) -> set[UUID]:
    keys = [queue_key(priority) for priority in dict.fromkeys(PRIORITY_CYCLE)]
    async for raw in redis.scan_iter(match=PROCESSING_PATTERN):
        keys.append(raw.decode() if isinstance(raw, bytes) else raw)
    delivered: set[UUID] = set()
    for key in keys:
        for raw in await redis.lrange(key, 0, -1):
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                message = parse_queue_message(
                    ReservedQueueMessage(raw=raw, source_queue=key, processing_queue="")
                )
            except InvalidQueueMessageError:
                continue
            delivered.add(message.job_id)
    return delivered


async def restore_missing_queued_deliveries(
    session: AsyncSession, redis: Redis
) -> list[UUID]:
    delivered = await _delivered_job_ids(redis)
    jobs = list(
        await session.scalars(
            select(Job).where(Job.status == JobStatus.QUEUED.value).order_by(Job.created_at, Job.id)
        )
    )
    restored: list[UUID] = []
    for job in jobs:
        if job.id in delivered:
            continue
        message = QueueMessage(
            event_id=uuid7(),
            job_id=job.id,
            job_type=job.job_type,
            handler_version=job.handler_version,
            priority=cast(Literal["high", "normal", "low"], job.priority),
            trace_id=current_trace_id(),
        )
        await redis.rpush(queue_key(job.priority), message.model_dump_json())
        restored.append(job.id)
    return restored
