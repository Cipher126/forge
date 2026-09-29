from uuid import uuid7

from redis.asyncio import Redis

from forge.queue.names import api_key_rate_limit_key
from forge.schemas.api_keys import RateLimitDecision

SLIDING_WINDOW_SCRIPT = """
local current = redis.call('TIME')
local now = tonumber(current[1]) * 1000 + math.floor(tonumber(current[2]) / 1000)
local window = tonumber(ARGV[2]) * 1000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[1]) then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local retry = math.max(1, math.ceil((tonumber(oldest[2]) + window - now) / 1000))
    return {0, 0, retry}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return {1, tonumber(ARGV[1]) - count - 1, 0}
"""


async def check_rate_limit(
    redis: Redis,
    *,
    public_id: str,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    raw = await redis.eval(
        SLIDING_WINDOW_SCRIPT,
        1,
        api_key_rate_limit_key(public_id),
        limit,
        window_seconds,
        str(uuid7()),
    )
    allowed, remaining, retry_after = (int(value) for value in raw)
    return RateLimitDecision(
        allowed=bool(allowed),
        remaining=remaining,
        retry_after_seconds=retry_after,
    )
