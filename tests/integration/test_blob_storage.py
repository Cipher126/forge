import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid7

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from forge.api.app import create_app
from forge.application.execution import claim_job_attempt
from forge.config import get_settings
from forge.persistence.models import ApiKey, AuditEvent, Job
from forge.processes.worker import execute_claimed_job
from forge.queue.names import api_key_rate_limit_key
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


async def test_blob_backed_payload_execution_result_and_replay() -> None:
    settings = get_settings()
    original_inline_limit = settings.max_inline_payload_bytes
    settings.max_inline_payload_bytes = 1
    resources = RuntimeResources.create(settings)
    async with resources.sessions.begin() as session:
        owner = await issue_api_key(
            session,
            name="blob storage test owner",
            scopes={"jobs:read", "jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )

    app = create_app()
    headers = {"Authorization": f"Bearer {owner.credential}"}
    object_keys: set[str] = set()
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                submitted = await client.post(
                    "/v1/jobs",
                    headers=headers,
                    json={
                        "type": "demo.generate_report",
                        "payload": {"report_id": "blob-backed", "format": "json"},
                    },
                )
                assert submitted.status_code == 202
                submitted_body = submitted.json()
                assert submitted_body["payload"] is None
                assert submitted_body["payload_stored_externally"] is True
                assert "payload_blob_key" not in submitted_body

                async with resources.sessions.begin() as session:
                    job = await session.get(Job, submitted_body["id"])
                    assert job is not None
                    assert job.payload_blob_key is not None
                    object_keys.add(job.payload_blob_key)
                    assert await resources.object_store.get_json(job.payload_blob_key) == {
                        "report_id": "blob-backed",
                        "format": "json",
                    }
                    claimed = await claim_job_attempt(
                        session,
                        message=QueueMessage(
                            event_id=uuid7(),
                            job_id=job.id,
                            job_type=job.job_type,
                            handler_version=job.handler_version,
                            priority="normal",
                        ),
                        worker_id="blob-test-worker",
                        lease_seconds=settings.worker_attempt_lease_seconds,
                    )
                assert claimed is not None

                with ThreadPoolExecutor(max_workers=1) as executor:
                    await execute_claimed_job(
                        resources,
                        settings,
                        claimed,
                        permit=None,
                        thread_pool=executor,
                        process_pool=executor,
                    )

                fetched = await client.get(
                    f"/v1/jobs/{submitted_body['id']}",
                    headers=headers,
                )
                assert fetched.status_code == 200
                fetched_body = fetched.json()
                assert fetched_body["status"] == "succeeded"
                assert fetched_body["result"] is None
                assert fetched_body["result_stored_externally"] is True
                assert "result_blob_key" not in fetched_body

                result_response = await client.get(
                    f"/v1/jobs/{submitted_body['id']}/result",
                    headers=headers,
                )
                assert result_response.status_code == 200
                assert result_response.json()["stored_externally"] is True
                assert result_response.json()["result"]["report_id"] == "blob-backed"

                async with resources.sessions.begin() as session:
                    completed = await session.get(Job, submitted_body["id"])
                    assert completed is not None
                    assert completed.result_blob_key is not None
                    object_keys.add(completed.result_blob_key)
                    external_result = await resources.object_store.get_json(
                        completed.result_blob_key
                    )
                    assert external_result["report_id"] == "blob-backed"
                    replay_source = Job(
                        id=uuid7(),
                        api_key_id=owner.record.id,
                        job_type=completed.job_type,
                        handler_version=completed.handler_version,
                        status="dead_lettered",
                        priority="normal",
                        payload_blob_key=completed.payload_blob_key,
                        max_attempts=3,
                        attempt_count=3,
                        finished_at=datetime.now(UTC),
                    )
                    session.add(replay_source)

                replayed = await client.post(
                    f"/v1/jobs/{replay_source.id}/replay",
                    headers={**headers, "Idempotency-Key": "blob-replay"},
                )
                assert replayed.status_code == 202
                assert replayed.json()["replayed_from_job_id"] == str(replay_source.id)
                assert replayed.json()["payload_stored_externally"] is True
    finally:
        settings.max_inline_payload_bytes = original_inline_limit
        async with resources.sessions() as session:
            jobs = (
                await session.scalars(select(Job).where(Job.api_key_id == owner.record.id))
            ).all()
            object_keys.update(
                key
                for job in jobs
                for key in (job.payload_blob_key, job.result_blob_key)
                if key is not None
            )
        for key in object_keys:
            await resources.object_store.delete(key)
        await resources.redis.delete(api_key_rate_limit_key(owner.record.public_id))
        async with resources.sessions.begin() as session:
            await session.execute(
                delete(AuditEvent).where(AuditEvent.actor_public_id == owner.record.public_id)
            )
            await session.execute(delete(Job).where(Job.api_key_id == owner.record.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == owner.record.id))
        await resources.close()
