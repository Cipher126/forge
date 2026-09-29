import os
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from sqlalchemy import delete

from forge.application.maintenance import (
    archive_terminal_jobs,
    delete_expired_idempotency_keys,
    delete_orphaned_job_objects,
)
from forge.config import get_settings
from forge.persistence.models import (
    ApiKey,
    IdempotencyKey,
    Job,
    JobAttempt,
    JobTransition,
)
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_maintenance_archives_jobs_and_reclaims_expired_data() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    archive_key: str | None = None
    try:
        old = datetime.now(UTC) - timedelta(days=40)
        payload_key = await resources.object_store.put_json(
            "payloads", {"maintenance_test": str(uuid7())}
        )
        result_key = await resources.object_store.put_json(
            "results", {"maintenance_test": str(uuid7())}
        )
        async with resources.sessions.begin() as session:
            owner = await issue_api_key(
                session,
                name="maintenance test owner",
                scopes={"jobs:read", "jobs:write"},
                pepper=settings.api_key_pepper.get_secret_value(),
            )
            job = Job(
                id=uuid7(),
                api_key_id=owner.record.id,
                job_type="demo.generate_report",
                handler_version="v1",
                status="succeeded",
                priority="normal",
                payload_blob_key=payload_key,
                max_attempts=3,
                attempt_count=1,
                finished_at=old,
                result_blob_key=result_key,
                created_at=old,
                updated_at=old,
            )
            session.add(job)
            await session.flush()
            attempt = JobAttempt(
                id=uuid7(),
                job_id=job.id,
                attempt_number=1,
                status="succeeded",
                worker_id="maintenance-test-worker",
                ownership_token=uuid7(),
                started_at=old,
                finished_at=old,
                retryable=False,
                created_at=old,
                updated_at=old,
            )
            session.add(attempt)
            await session.flush()
            session.add_all(
                [
                    JobTransition(
                        job_id=job.id,
                        attempt_id=attempt.id,
                        from_status="running",
                        to_status="succeeded",
                        reason="handler_succeeded",
                        actor_type="worker",
                        actor_id="maintenance-test-worker",
                    ),
                    IdempotencyKey(
                        api_key_id=owner.record.id,
                        idempotency_key=f"expired-{uuid7()}",
                        request_fingerprint=b"x" * 32,
                        job_id=job.id,
                        created_at=old,
                        expires_at=old + timedelta(days=1),
                    ),
                ]
            )

        async with resources.sessions.begin() as session:
            assert await delete_expired_idempotency_keys(session, batch_size=100) == 1
            archived = await archive_terminal_jobs(
                session,
                resources.object_store,
                retention_days=30,
                batch_size=100,
            )
        assert archived == [job.id]
        archive_key = f"archives/jobs/{job.id}.json"
        archive = await resources.object_store.get_json(archive_key)
        assert archive["schema_version"] == 1
        assert archive["job"]["id"] == str(job.id)
        assert archive["attempts"][0]["status"] == "succeeded"
        assert archive["transitions"][0]["reason"] == "handler_succeeded"

        async with resources.sessions() as session:
            assert await session.get(Job, job.id) is None
            deleted = await delete_orphaned_job_objects(
                session, resources.object_store, grace_hours=0
            )
        assert deleted >= 2
        remaining_objects = [
            *await resources.object_store.list_objects("payloads/"),
            *await resources.object_store.list_objects("results/"),
        ]
        assert all(item.key not in {payload_key, result_key} for item in remaining_objects)
    finally:
        if archive_key is not None:
            await resources.object_store.delete(archive_key)
        if "owner" in locals():
            async with resources.sessions.begin() as session:
                await session.execute(delete(ApiKey).where(ApiKey.id == owner.record.id))
        await resources.close()
