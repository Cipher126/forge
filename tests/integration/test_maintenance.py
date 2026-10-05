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
from forge.errors.object_store import ObjectStoreUnavailableError
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
    archive_keys: set[str] = set()
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
            retained_failure = Job(
                id=uuid7(),
                api_key_id=owner.record.id,
                job_type="demo.generate_report",
                handler_version="v1",
                status="failed",
                priority="normal",
                payload={"report_id": "retained-failure", "format": "json"},
                max_attempts=3,
                attempt_count=3,
                finished_at=old,
                created_at=old,
                updated_at=old,
            )
            expired_failure_time = datetime.now(UTC) - timedelta(days=100)
            expired_failure = Job(
                id=uuid7(),
                api_key_id=owner.record.id,
                job_type="demo.generate_report",
                handler_version="v1",
                status="dead_lettered",
                priority="normal",
                payload={"report_id": "expired-failure", "format": "json"},
                max_attempts=3,
                attempt_count=3,
                finished_at=expired_failure_time,
                created_at=expired_failure_time,
                updated_at=expired_failure_time,
            )
            session.add_all([retained_failure, expired_failure])
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
                successful_retention_days=30,
                failed_retention_days=90,
                batch_size=100,
            )
        assert archived == [expired_failure.id, job.id]
        archive_keys = {
            f"archives/jobs/{job.id}.json.gz",
            f"archives/jobs/{expired_failure.id}.json.gz",
        }
        archive = await resources.object_store.get_json_gzip(
            f"archives/jobs/{job.id}.json.gz"
        )
        assert archive["schema_version"] == 1
        assert archive["job"]["id"] == str(job.id)
        assert archive["attempts"][0]["status"] == "succeeded"
        assert archive["transitions"][0]["reason"] == "handler_succeeded"
        failure_archive = await resources.object_store.get_json_gzip(
            f"archives/jobs/{expired_failure.id}.json.gz"
        )
        assert failure_archive["job"]["status"] == "dead_lettered"

        async with resources.sessions() as session:
            assert await session.get(Job, job.id) is None
            assert await session.get(Job, expired_failure.id) is None
            assert await session.get(Job, retained_failure.id) is not None
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
        for archive_key in archive_keys:
            await resources.object_store.delete(archive_key)
        if "owner" in locals():
            async with resources.sessions.begin() as session:
                await session.execute(delete(Job).where(Job.api_key_id == owner.record.id))
                await session.execute(delete(ApiKey).where(ApiKey.id == owner.record.id))
        await resources.close()


async def test_archive_storage_failure_keeps_database_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    old = datetime.now(UTC) - timedelta(days=40)
    async with resources.sessions.begin() as session:
        owner = await issue_api_key(
            session,
            name="failed archive owner",
            scopes={"jobs:read"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        job = Job(
            id=uuid7(),
            api_key_id=owner.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="succeeded",
            priority="normal",
            payload={"report_id": "archive-failure", "format": "json"},
            max_attempts=3,
            attempt_count=1,
            finished_at=old,
            created_at=old,
            updated_at=old,
        )
        session.add(job)

    async def fail_archive(_key: str, _value: object) -> None:
        raise ObjectStoreUnavailableError("storage unavailable")

    monkeypatch.setattr(resources.object_store, "put_json_gzip_at", fail_archive)
    try:
        with pytest.raises(ObjectStoreUnavailableError, match="storage unavailable"):
            async with resources.sessions.begin() as session:
                await archive_terminal_jobs(
                    session,
                    resources.object_store,
                    successful_retention_days=30,
                    failed_retention_days=90,
                    batch_size=100,
                )

        async with resources.sessions() as session:
            assert await session.get(Job, job.id) is not None
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.id == job.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == owner.record.id))
        await resources.close()
