import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from forge.api.app import create_app
from forge.config import get_settings
from forge.persistence.models import (
    ApiKey,
    AuditEvent,
    IdempotencyKey,
    Job,
    JobTransition,
    OutboxEvent,
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


async def test_job_submission_is_atomic_idempotent_and_owned() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    async with resources.sessions.begin() as session:
        owner = await issue_api_key(
            session,
            name="job integration owner",
            scopes={"jobs:read", "jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        stranger = await issue_api_key(
            session,
            name="job integration stranger",
            scopes={"jobs:read"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        dead_lettered = Job(
            id=uuid7(),
            api_key_id=owner.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="dead_lettered",
            priority="normal",
            payload={"report_id": "replay-source", "format": "json"},
            max_attempts=3,
            attempt_count=3,
            finished_at=datetime.now(UTC),
        )
        session.add(dead_lettered)

    app = create_app()
    headers = {
        "Authorization": f"Bearer {owner.credential}",
        "Idempotency-Key": "quarterly-report-2026",
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    }
    body = {
        "type": "demo.generate_report",
        "payload": {"report_id": "quarterly", "format": "pdf"},
        "priority": "high",
    }
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                created = await client.post("/v1/jobs", headers=headers, json=body)
                assert created.status_code == 202
                created_job = created.json()
                assert created_job["handler_version"] == "1"
                assert created_job["status"] == "queued"
                assert created_job["max_attempts"] == 3
                assert created.headers["traceparent"].startswith(
                    "00-4bf92f3577b34da6a3ce929d0e0e4736-"
                )
                async with resources.sessions() as session:
                    submission_event = await session.scalar(
                        select(OutboxEvent).where(
                            OutboxEvent.job_id == UUID(created_job["id"])
                        )
                    )
                    idempotency_record = await session.scalar(
                        select(IdempotencyKey).where(
                            IdempotencyKey.job_id == UUID(created_job["id"]),
                            IdempotencyKey.idempotency_key == "quarterly-report-2026",
                        )
                    )
                assert submission_event is not None
                assert idempotency_record is not None
                retention = idempotency_record.expires_at - idempotency_record.created_at
                assert timedelta(hours=settings.idempotency_retention_hours) <= retention
                assert retention < timedelta(
                    hours=settings.idempotency_retention_hours, seconds=5
                )
                assert (
                    submission_event.payload["trace_id"]
                    == "4bf92f3577b34da6a3ce929d0e0e4736"
                )

                repeated = await client.post("/v1/jobs", headers=headers, json=body)
                assert repeated.status_code == 200
                assert repeated.json()["id"] == created_job["id"]

                changed = await client.post(
                    "/v1/jobs",
                    headers=headers,
                    json={**body, "priority": "low"},
                )
                assert changed.status_code == 409
                assert changed.json()["error"]["code"] == "idempotency_conflict"

                concurrent_headers = {
                    "Authorization": f"Bearer {owner.credential}",
                    "Idempotency-Key": "concurrent-report-2026",
                }
                concurrent = await asyncio.gather(
                    client.post("/v1/jobs", headers=concurrent_headers, json=body),
                    client.post("/v1/jobs", headers=concurrent_headers, json=body),
                )
                assert sorted(item.status_code for item in concurrent) == [200, 202]
                assert concurrent[0].json()["id"] == concurrent[1].json()["id"]

                fetched = await client.get(
                    f"/v1/jobs/{created_job['id']}",
                    headers={"Authorization": f"Bearer {owner.credential}"},
                )
                assert fetched.status_code == 200

                hidden = await client.get(
                    f"/v1/jobs/{created_job['id']}",
                    headers={"Authorization": f"Bearer {stranger.credential}"},
                )
                assert hidden.status_code == 404
                assert hidden.json()["error"]["code"] == "job_not_found"

                cancelled = await client.post(
                    f"/v1/jobs/{created_job['id']}/cancel",
                    headers={"Authorization": f"Bearer {owner.credential}"},
                )
                assert cancelled.status_code == 200
                assert cancelled.json()["status"] == "cancelled"
                assert cancelled.json()["finished_at"] is not None

                repeated_cancel = await client.post(
                    f"/v1/jobs/{created_job['id']}/cancel",
                    headers={"Authorization": f"Bearer {owner.credential}"},
                )
                assert repeated_cancel.status_code == 200

                replay_headers = {
                    "Authorization": f"Bearer {owner.credential}",
                    "Idempotency-Key": "replay-dead-letter-2026",
                }
                replayed = await client.post(
                    f"/v1/jobs/{dead_lettered.id}/replay",
                    headers=replay_headers,
                )
                assert replayed.status_code == 202
                assert replayed.json()["status"] == "queued"
                assert replayed.json()["replayed_from_job_id"] == str(dead_lettered.id)
                assert replayed.json()["handler_version"] == "1"

                repeated_replay = await client.post(
                    f"/v1/jobs/{dead_lettered.id}/replay",
                    headers=replay_headers,
                )
                assert repeated_replay.status_code == 200
                assert repeated_replay.json()["id"] == replayed.json()["id"]

        async with resources.sessions() as session:
            job_id = created.json()["id"]
            assert (
                await session.scalar(select(func.count()).select_from(Job).where(Job.id == job_id))
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(JobTransition)
                    .where(JobTransition.job_id == job_id)
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.job_id == job_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(IdempotencyKey)
                    .where(IdempotencyKey.job_id == job_id)
                )
                == 1
            )
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(
                delete(AuditEvent).where(AuditEvent.actor_public_id == owner.record.public_id)
            )
            await session.execute(delete(Job).where(Job.api_key_id.in_([owner.record.id])))
            await session.execute(
                delete(ApiKey).where(ApiKey.id.in_([owner.record.id, stranger.record.id]))
            )
        await resources.close()


async def test_future_job_is_scheduled_without_an_outbox_event() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    async with resources.sessions.begin() as session:
        owner = await issue_api_key(
            session,
            name="scheduled job integration owner",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )

    app = create_app()
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/v1/jobs",
                    headers={"Authorization": f"Bearer {owner.credential}"},
                    json={
                        "type": "demo.generate_report",
                        "payload": {"report_id": "future", "format": "json"},
                        "run_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    },
                )
        assert response.status_code == 202
        assert response.json()["status"] == "scheduled"

        async with resources.sessions() as session:
            outbox_count = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.job_id == response.json()["id"])
            )
            assert outbox_count == 0
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.api_key_id == owner.record.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == owner.record.id))
        await resources.close()
