import os
from datetime import timedelta
from uuid import uuid7

import pytest
from sqlalchemy import delete, func, select, update

from forge.application.outbox import (
    claim_outbox_events,
    mark_outbox_published,
    publish_outbox_event,
)
from forge.config import get_settings
from forge.errors.outbox import OutboxClaimLostError
from forge.persistence.models import ApiKey, Job, OutboxEvent
from forge.processes.dispatcher import dispatch_once
from forge.queue.names import queue_key
from forge.resources import RuntimeResources
from forge.schemas.outbox import QueueMessage
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def create_queued_event(resources: RuntimeResources, name: str) -> tuple[ApiKey, OutboxEvent]:
    settings = get_settings()
    async with resources.sessions.begin() as session:
        issued = await issue_api_key(
            session,
            name=name,
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        job = Job(
            id=uuid7(),
            api_key_id=issued.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="queued",
            priority="normal",
            payload={"report_id": name, "format": "pdf"},
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
                "job_id": str(job.id),
                "job_type": job.job_type,
                "handler_version": job.handler_version,
                "priority": job.priority,
            },
        )
        session.add(event)
        await session.flush()
        return issued.record, event


async def remove_test_data(resources: RuntimeResources, api_key: ApiKey) -> None:
    async with resources.sessions.begin() as session:
        await session.execute(delete(Job).where(Job.api_key_id == api_key.id))
        await session.execute(delete(ApiKey).where(ApiKey.id == api_key.id))
    await resources.redis.delete(queue_key("normal"))


async def test_dispatcher_publishes_and_finalizes_event() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    await resources.redis.delete(queue_key("normal"))
    api_key, event = await create_queued_event(resources, "dispatcher integration")
    try:
        assert await dispatch_once(resources, settings) == 1

        raw_message = await resources.redis.lindex(queue_key("normal"), 0)
        message = QueueMessage.model_validate_json(raw_message)
        assert message.event_id == event.id
        assert message.job_id == event.job_id

        async with resources.sessions() as session:
            persisted = await session.get(OutboxEvent, event.id)
            assert persisted is not None
            assert persisted.published_at is not None
            assert persisted.lease_token is None
            assert persisted.dispatch_attempts == 1
    finally:
        await remove_test_data(resources, api_key)
        await resources.close()


async def test_publish_before_finalize_can_deliver_a_duplicate() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    await resources.redis.delete(queue_key("normal"))
    api_key, event = await create_queued_event(resources, "duplicate integration")
    try:
        async with resources.sessions.begin() as session:
            first = (await claim_outbox_events(session, batch_size=1, lease_seconds=30))[0]
        await publish_outbox_event(resources.redis, first)

        async with resources.sessions.begin() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event.id)
                .values(lease_expires_at=func.clock_timestamp() - timedelta(seconds=1))
            )
        async with resources.sessions.begin() as session:
            second = (await claim_outbox_events(session, batch_size=1, lease_seconds=30))[0]

        with pytest.raises(OutboxClaimLostError):
            async with resources.sessions.begin() as session:
                await mark_outbox_published(session, first)

        await publish_outbox_event(resources.redis, second)
        async with resources.sessions.begin() as session:
            await mark_outbox_published(session, second)

        assert await resources.redis.llen(queue_key("normal")) == 2
        async with resources.sessions() as session:
            attempts = await session.scalar(
                select(OutboxEvent.dispatch_attempts).where(OutboxEvent.id == event.id)
            )
            assert attempts == 2
    finally:
        await remove_test_data(resources, api_key)
        await resources.close()
