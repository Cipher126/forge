import os
from uuid import uuid7

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import delete

from forge.config import get_settings
from forge.persistence.models import ApiKey, Job, OutboxEvent
from forge.processes.dispatcher import dispatch_once
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.failure,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


class UnavailableRedis:
    async def rpush(self, *_args: object, **_kwargs: object) -> None:
        raise RedisConnectionError("simulated Redis outage")


async def test_redis_outage_releases_outbox_claim_for_retry() -> None:
    settings = get_settings().model_copy(
        update={"dispatcher_retry_base_ms": 1, "dispatcher_retry_cap_ms": 1}
    )
    resources = RuntimeResources.create(settings)
    real_redis = resources.redis
    async with resources.sessions.begin() as session:
        owner = await issue_api_key(
            session,
            name="failure suite redis owner",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        job = Job(
            id=uuid7(),
            api_key_id=owner.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="queued",
            priority="normal",
            payload={"report_id": "redis-failure", "format": "json"},
            max_attempts=3,
            attempt_count=0,
        )
        session.add(job)
        await session.flush()
        event = OutboxEvent(
            id=uuid7(),
            job_id=job.id,
            event_type="job.queued",
            payload={
                "job_type": job.job_type,
                "handler_version": job.handler_version,
                "priority": job.priority,
            },
        )
        session.add(event)

    resources.redis = UnavailableRedis()  # type: ignore[assignment]
    try:
        assert await dispatch_once(resources, settings) >= 1
        async with resources.sessions() as session:
            persisted = await session.get(OutboxEvent, event.id)
            assert persisted is not None
            assert persisted.published_at is None
            assert persisted.lease_token is None
            assert persisted.lease_expires_at is None
            assert persisted.dispatch_attempts == 1
            assert persisted.last_error is not None
            assert "could not publish outbox event" in persisted.last_error
            assert "simulated Redis outage" not in persisted.last_error
    finally:
        resources.redis = real_redis
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.id == job.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == owner.record.id))
        await resources.close()
