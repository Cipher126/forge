import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from sqlalchemy import delete, func, select

from forge.application.scheduling import promote_due_jobs
from forge.config import get_settings
from forge.persistence.models import ApiKey, Job, JobTransition, OutboxEvent
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_concurrent_schedulers_promote_each_due_job_once() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    now = datetime.now(UTC)
    async with resources.sessions.begin() as session:
        issued = await issue_api_key(
            session,
            name="scheduler integration owner",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        due_jobs = [
            Job(
                id=uuid7(),
                api_key_id=issued.record.id,
                job_type="demo.generate_report",
                handler_version="1",
                status=status,
                priority="normal",
                payload={"report_id": f"due-{index}", "format": "pdf"},
                max_attempts=3,
                attempt_count=1 if status == "retry_scheduled" else 0,
                run_at=now - timedelta(seconds=index + 1),
            )
            for index, status in enumerate(["scheduled", "retry_scheduled"])
        ]
        future_job = Job(
            id=uuid7(),
            api_key_id=issued.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="scheduled",
            priority="normal",
            payload={"report_id": "future", "format": "pdf"},
            max_attempts=3,
            attempt_count=0,
            run_at=now + timedelta(hours=1),
        )
        session.add_all([*due_jobs, future_job])

    async def promote_one() -> list[object]:
        async with resources.sessions.begin() as session:
            return list(await promote_due_jobs(session, batch_size=1))

    try:
        results = await asyncio.gather(promote_one(), promote_one())
        promoted_ids = [job_id for batch in results for job_id in batch]
        assert len(promoted_ids) == 2
        assert len(set(promoted_ids)) == 2
        assert set(promoted_ids) == {job.id for job in due_jobs}

        async with resources.sessions() as session:
            for job in due_jobs:
                persisted = await session.get(Job, job.id)
                assert persisted is not None
                assert persisted.status == "queued"
                assert persisted.queued_at is not None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobTransition)
                        .where(
                            JobTransition.job_id == job.id,
                            JobTransition.reason == "scheduled_time_reached",
                        )
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(OutboxEvent.job_id == job.id)
                    )
                    == 1
                )

            persisted_future = await session.get(Job, future_job.id)
            assert persisted_future is not None
            assert persisted_future.status == "scheduled"
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.api_key_id == issued.record.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == issued.record.id))
        await resources.close()
