import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid7

import pytest
from sqlalchemy import delete

from forge.config import get_settings
from forge.persistence.models import ApiKey, Job
from forge.processes.worker import handle_reservation
from forge.queue.client import (
    acquire_job_type_permit,
    release_job_type_permit,
    renew_job_type_permit,
)
from forge.queue.names import job_type_permits_key, processing_queue_key, queue_key
from forge.resources import RuntimeResources
from forge.schemas.execution import JobTypePermit, ReservedQueueMessage
from forge.schemas.outbox import QueueMessage
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_job_type_capacity_defers_without_consuming_an_attempt() -> None:
    base_settings = get_settings()
    settings = base_settings.model_copy(
        update={
            "job_type_concurrency_limits": {"demo.generate_report": 1},
            "worker_poll_interval_ms": 10,
        }
    )
    resources = RuntimeResources.create(settings)
    worker_id = "capacity-test-worker"
    processing = processing_queue_key(worker_id)
    source = queue_key("normal")
    permit_key = job_type_permits_key("demo.generate_report")
    await resources.redis.delete(processing, source, permit_key)

    async with resources.sessions.begin() as session:
        owner = await issue_api_key(
            session,
            name="capacity test owner",
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
            payload={"report_id": "capacity", "format": "json"},
            max_attempts=3,
            attempt_count=0,
        )
        session.add(job)

    raw = QueueMessage(
        event_id=uuid7(),
        job_id=job.id,
        job_type=job.job_type,
        handler_version=job.handler_version,
        priority="normal",
    ).model_dump_json()
    await resources.redis.rpush(processing, raw)
    reservation = ReservedQueueMessage(
        raw=raw,
        source_queue=source,
        processing_queue=processing,
    )
    blocker = JobTypePermit(job_type=job.job_type, token=uuid7())

    try:
        assert await acquire_job_type_permit(
            resources.redis,
            permit=blocker,
            limit=1,
            lease_seconds=30,
        )
        assert await renew_job_type_permit(
            resources.redis,
            permit=blocker,
            lease_seconds=30,
        )

        with ThreadPoolExecutor(max_workers=1) as thread_pool:
            await handle_reservation(
                resources,
                settings,
                reservation,
                worker_id,
                thread_pool,
                thread_pool,
            )

        async with resources.sessions() as session:
            persisted = await session.get(Job, job.id)
            assert persisted is not None
            assert persisted.status == "queued"
            assert persisted.attempt_count == 0
        assert await resources.redis.llen(processing) == 0
        assert await resources.redis.lindex(source, 0) == raw
        assert await release_job_type_permit(resources.redis, blocker)
        assert not await renew_job_type_permit(
            resources.redis,
            permit=blocker,
            lease_seconds=30,
        )
    finally:
        await resources.redis.delete(processing, source, permit_key)
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.id == job.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == owner.record.id))
        await resources.close()
