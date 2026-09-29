import os
from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from forge.api.app import create_app
from forge.config import get_settings
from forge.persistence.models import ApiKey, AuditEvent, Job
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_sensitive_actions_create_admin_queryable_audit_events() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    async with resources.sessions.begin() as session:
        admin = await issue_api_key(
            session,
            name="audit test admin",
            scopes={"admin"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        writer = await issue_api_key(
            session,
            name="audit test writer",
            scopes={"jobs:read", "jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        queued = Job(
            id=uuid7(),
            api_key_id=writer.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="queued",
            priority="normal",
            payload={"report_id": "audit-cancel", "format": "json"},
            max_attempts=3,
            attempt_count=0,
        )
        dead_lettered = Job(
            id=uuid7(),
            api_key_id=writer.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="dead_lettered",
            priority="normal",
            payload={"report_id": "audit-replay", "format": "json"},
            max_attempts=3,
            attempt_count=3,
            finished_at=datetime.now(UTC),
        )
        session.add_all([queued, dead_lettered])

    app = create_app()
    admin_headers = {"Authorization": f"Bearer {admin.credential}"}
    writer_headers = {"Authorization": f"Bearer {writer.credential}"}
    replay_headers = {**writer_headers, "Idempotency-Key": "audit-replay-once"}
    created_key_id: str | None = None
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                created_key = await client.post(
                    "/v1/admin/api-keys",
                    headers=admin_headers,
                    json={"name": "audit-created reader", "scopes": ["jobs:read"]},
                )
                assert created_key.status_code == 201
                created_key_id = created_key.json()["id"]

                cancelled = await client.post(
                    f"/v1/jobs/{queued.id}/cancel",
                    headers=writer_headers,
                )
                assert cancelled.status_code == 200

                replayed = await client.post(
                    f"/v1/jobs/{dead_lettered.id}/replay",
                    headers=replay_headers,
                )
                assert replayed.status_code == 202
                repeated_replay = await client.post(
                    f"/v1/jobs/{dead_lettered.id}/replay",
                    headers=replay_headers,
                )
                assert repeated_replay.status_code == 200

                forbidden = await client.get(
                    "/v1/admin/audit-events",
                    headers=writer_headers,
                )
                assert forbidden.status_code == 403

                first_page = await client.get(
                    "/v1/admin/audit-events",
                    params={"actor_public_id": writer.record.public_id, "limit": 1},
                    headers=admin_headers,
                )
                assert first_page.status_code == 200
                first_body = first_page.json()
                assert len(first_body["items"]) == 1
                assert first_body["next_cursor"] is not None

                second_page = await client.get(
                    "/v1/admin/audit-events",
                    params={
                        "actor_public_id": writer.record.public_id,
                        "limit": 1,
                        "cursor": first_body["next_cursor"],
                    },
                    headers=admin_headers,
                )
                second_body = second_page.json()
                assert len(second_body["items"]) == 1
                assert second_body["next_cursor"] is None
                writer_events = [*first_body["items"], *second_body["items"]]
                assert {event["action"] for event in writer_events} == {
                    "job.cancellation_requested",
                    "job.replayed",
                }
                replay_event = next(
                    event for event in writer_events if event["action"] == "job.replayed"
                )
                assert replay_event["details"]["source_job_id"] == str(dead_lettered.id)

                key_events = await client.get(
                    "/v1/admin/audit-events",
                    params={
                        "actor_public_id": admin.record.public_id,
                        "action": "api_key.created",
                    },
                    headers=admin_headers,
                )
                assert key_events.status_code == 200
                assert len(key_events.json()["items"]) == 1
                assert key_events.json()["items"][0]["resource_id"] == created_key_id
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(
                delete(AuditEvent).where(
                    AuditEvent.actor_public_id.in_(
                        [admin.record.public_id, writer.record.public_id]
                    )
                )
            )
            await session.execute(delete(Job).where(Job.api_key_id == writer.record.id))
            key_ids = [admin.record.id, writer.record.id]
            if created_key_id is not None:
                key_ids.append(UUID(created_key_id))
            await session.execute(delete(ApiKey).where(ApiKey.id.in_(key_ids)))
        await resources.close()
