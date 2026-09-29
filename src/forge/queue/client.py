from redis.asyncio import Redis

from forge.queue.names import (
    PRIORITY_CYCLE,
    job_type_permits_key,
    processing_queue_key,
    queue_key,
    worker_heartbeat_key,
)
from forge.schemas.execution import JobTypePermit, ReservedQueueMessage

ACQUIRE_PERMIT_SCRIPT = """
local current = redis.call('TIME')
local now = tonumber(current[1]) + tonumber(current[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
    return 0
end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
redis.call('EXPIRE', KEYS[1], math.ceil(tonumber(ARGV[2]) * 2))
return 1
"""

RENEW_PERMIT_SCRIPT = """
if not redis.call('ZSCORE', KEYS[1], ARGV[2]) then
    return 0
end
local current = redis.call('TIME')
local now = tonumber(current[1]) + tonumber(current[2]) / 1000000
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[1]), ARGV[2])
redis.call('EXPIRE', KEYS[1], math.ceil(tonumber(ARGV[1]) * 2))
return 1
"""

REQUEUE_RESERVATION_SCRIPT = """
if redis.call('LREM', KEYS[1], 1, ARGV[1]) == 1 then
    redis.call('RPUSH', KEYS[2], ARGV[1])
    return 1
end
return 0
"""


def create_redis_client(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


async def probe_redis(client: Redis) -> bool:
    try:
        return bool(await client.ping())
    except Exception:
        return False


async def reserve_next_message(
    client: Redis,
    *,
    worker_id: str,
    cycle_position: int,
) -> tuple[ReservedQueueMessage | None, int]:
    processing_queue = processing_queue_key(worker_id)
    cycle_length = len(PRIORITY_CYCLE)
    for offset in range(cycle_length):
        position = (cycle_position + offset) % cycle_length
        source_queue = queue_key(PRIORITY_CYCLE[position])
        raw = await client.lmove(source_queue, processing_queue, "LEFT", "RIGHT")
        next_position = (position + 1) % cycle_length
        if raw is not None:
            decoded = raw.decode() if isinstance(raw, bytes) else raw
            return (
                ReservedQueueMessage(
                    raw=decoded,
                    source_queue=source_queue,
                    processing_queue=processing_queue,
                ),
                next_position,
            )
    return None, (cycle_position + 1) % cycle_length


async def acknowledge_message(client: Redis, reservation: ReservedQueueMessage) -> bool:
    removed = await client.lrem(reservation.processing_queue, 1, reservation.raw)
    return removed == 1


async def requeue_message(client: Redis, reservation: ReservedQueueMessage) -> bool:
    moved = await client.eval(
        REQUEUE_RESERVATION_SCRIPT,
        2,
        reservation.processing_queue,
        reservation.source_queue,
        reservation.raw,
    )
    return int(moved) == 1


async def acquire_job_type_permit(
    client: Redis,
    *,
    permit: JobTypePermit,
    limit: int,
    lease_seconds: int,
) -> bool:
    acquired = await client.eval(
        ACQUIRE_PERMIT_SCRIPT,
        1,
        job_type_permits_key(permit.job_type),
        limit,
        lease_seconds,
        str(permit.token),
    )
    return int(acquired) == 1


async def renew_job_type_permit(
    client: Redis,
    *,
    permit: JobTypePermit,
    lease_seconds: int,
) -> bool:
    renewed = await client.eval(
        RENEW_PERMIT_SCRIPT,
        1,
        job_type_permits_key(permit.job_type),
        lease_seconds,
        str(permit.token),
    )
    return int(renewed) == 1


async def release_job_type_permit(client: Redis, permit: JobTypePermit) -> bool:
    removed = await client.zrem(job_type_permits_key(permit.job_type), str(permit.token))
    return int(removed) == 1


async def refresh_worker_heartbeat(client: Redis, worker_id: str, ttl_seconds: int) -> None:
    await client.set(worker_heartbeat_key(worker_id), "alive", ex=ttl_seconds)


async def clear_worker_heartbeat(client: Redis, worker_id: str) -> None:
    await client.delete(worker_heartbeat_key(worker_id))
