from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from forge.application.execution import parse_queue_message
from forge.domain.jobs import JobStatus
from forge.errors.execution import InvalidQueueMessageError
from forge.persistence.models import Job
from forge.queue.names import queue_key, worker_heartbeat_key
from forge.schemas.execution import ReservedQueueMessage
from forge.schemas.recovery import ReservationRecoverySummary

PROCESSING_PREFIX = "forge:processing:"

REQUEUE_SCRIPT = """
local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
if removed == 1 then
    redis.call('RPUSH', KEYS[2], ARGV[1])
end
return removed
"""


async def reconcile_orphaned_reservations(
    session: AsyncSession,
    redis: Redis,
) -> ReservationRecoverySummary:
    requeued = 0
    removed = 0
    malformed = 0
    async for processing_queue in redis.scan_iter(match=f"{PROCESSING_PREFIX}*"):
        if isinstance(processing_queue, bytes):
            processing_queue = processing_queue.decode()
        worker_id = processing_queue.removeprefix(PROCESSING_PREFIX)
        if await redis.exists(worker_heartbeat_key(worker_id)):
            continue
        raw_messages = await redis.lrange(processing_queue, 0, -1)
        for raw in raw_messages:
            if isinstance(raw, bytes):
                raw = raw.decode()
            reservation = ReservedQueueMessage(
                raw=raw,
                source_queue="",
                processing_queue=processing_queue,
            )
            try:
                message = parse_queue_message(reservation)
            except InvalidQueueMessageError:
                malformed += await redis.lrem(processing_queue, 1, raw)
                continue
            job = await session.get(Job, message.job_id)
            if job is not None and job.status == JobStatus.QUEUED.value:
                requeued += int(
                    await redis.eval(
                        REQUEUE_SCRIPT,
                        2,
                        processing_queue,
                        queue_key(message.priority),
                        raw,
                    )
                )
            else:
                removed += await redis.lrem(processing_queue, 1, raw)
    return ReservationRecoverySummary(
        requeued=requeued,
        removed=removed,
        malformed=malformed,
    )
