import os
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from forge.api.app import create_app
from forge.config import get_settings
from forge.persistence.models import ApiKey, Job, JobAttempt, JobTransition
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_job_listing_and_attempt_history_are_scoped_and_paginated() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    now = datetime.now(UTC)
    async with resources.sessions.begin() as session:
        owner = await issue_api_key(
            session,
            name="observability owner",
            scopes={"jobs:read"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        stranger = await issue_api_key(
            session,
            name="observability stranger",
            scopes={"jobs:read"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        admin = await issue_api_key(
            session,
            name="observability admin",
            scopes={"admin"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        owner_jobs = [
            Job(
                id=uuid7(),
                api_key_id=owner.record.id,
                job_type="demo.generate_report",
                handler_version="1",
                status=status,
                priority=priority,
                payload={"report_id": f"observable-{index}", "format": "json"},
                max_attempts=3,
                attempt_count=1 if index == 0 else 0,
                queued_at=now,
                started_at=now if index == 0 else None,
                finished_at=now if status == "succeeded" else None,
            )
            for index, (status, priority) in enumerate(
                [("succeeded", "high"), ("queued", "normal"), ("queued", "low")]
            )
        ]
        stranger_job = Job(
            id=uuid7(),
            api_key_id=stranger.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="queued",
            priority="normal",
            payload={"report_id": "hidden", "format": "json"},
            max_attempts=3,
            attempt_count=0,
            queued_at=now,
        )
        session.add_all([*owner_jobs, stranger_job])
        await session.flush()
        attempt = JobAttempt(
            id=uuid7(),
            job_id=owner_jobs[0].id,
            attempt_number=1,
            status="succeeded",
            worker_id="worker-observability",
            ownership_token=uuid7(),
            lease_expires_at=now + timedelta(seconds=30),
            started_at=now,
            finished_at=now + timedelta(milliseconds=25),
            retryable=None,
        )
        session.add(attempt)
        session.add_all(
            [
                JobTransition(
                    job_id=owner_jobs[0].id,
                    from_status=None,
                    to_status="queued",
                    reason="job_submitted",
                    actor_type="api",
                    actor_id=owner.record.public_id,
                    details={},
                ),
                JobTransition(
                    job_id=owner_jobs[0].id,
                    from_status="running",
                    to_status="succeeded",
                    reason="attempt_succeeded",
                    actor_type="worker",
                    actor_id="worker-observability",
                    details={"duration_ms": 25},
                ),
            ]
        )

    app = create_app()
    owner_headers = {"Authorization": f"Bearer {owner.credential}"}
    stranger_headers = {"Authorization": f"Bearer {stranger.credential}"}
    admin_headers = {"Authorization": f"Bearer {admin.credential}"}
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                first_page = await client.get("/v1/jobs?limit=2", headers=owner_headers)
                assert first_page.status_code == 200
                first_body = first_page.json()
                assert len(first_body["items"]) == 2
                assert first_body["next_cursor"] is not None

                second_page = await client.get(
                    "/v1/jobs",
                    params={"limit": 2, "cursor": first_body["next_cursor"]},
                    headers=owner_headers,
                )
                assert second_page.status_code == 200
                second_body = second_page.json()
                assert len(second_body["items"]) == 1
                assert second_body["next_cursor"] is None
                listed_ids = {item["id"] for item in [*first_body["items"], *second_body["items"]]}
                assert listed_ids == {str(job.id) for job in owner_jobs}
                assert str(stranger_job.id) not in listed_ids

                filtered = await client.get(
                    "/v1/jobs",
                    params={"status": "queued", "priority": "low"},
                    headers=owner_headers,
                )
                assert filtered.status_code == 200
                assert [item["id"] for item in filtered.json()["items"]] == [str(owner_jobs[2].id)]

                admin_list = await client.get("/v1/jobs?limit=100", headers=admin_headers)
                assert admin_list.status_code == 200
                assert str(stranger_job.id) in {item["id"] for item in admin_list.json()["items"]}

                attempts = await client.get(
                    f"/v1/jobs/{owner_jobs[0].id}/attempts",
                    headers=owner_headers,
                )
                assert attempts.status_code == 200
                attempt_body = attempts.json()["items"][0]
                assert attempt_body["attempt_number"] == 1
                assert attempt_body["worker_id"] == "worker-observability"
                assert attempt_body["lease_expires_at"] is not None
                assert "ownership_token" not in attempt_body

                transitions = await client.get(
                    f"/v1/jobs/{owner_jobs[0].id}/transitions",
                    headers=owner_headers,
                )
                assert transitions.status_code == 200
                transition_items = transitions.json()["items"]
                assert [item["reason"] for item in transition_items] == [
                    "job_submitted",
                    "attempt_succeeded",
                ]
                assert transition_items[1]["actor_type"] == "worker"
                assert transition_items[1]["details"] == {"duration_ms": 25}

                hidden_attempts = await client.get(
                    f"/v1/jobs/{owner_jobs[0].id}/attempts",
                    headers=stranger_headers,
                )
                assert hidden_attempts.status_code == 404
                assert hidden_attempts.json()["error"]["code"] == "job_not_found"

                hidden_transitions = await client.get(
                    f"/v1/jobs/{owner_jobs[0].id}/transitions",
                    headers=stranger_headers,
                )
                assert hidden_transitions.status_code == 404
                assert hidden_transitions.json()["error"]["code"] == "job_not_found"
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(
                delete(Job).where(Job.api_key_id.in_([owner.record.id, stranger.record.id]))
            )
            await session.execute(
                delete(ApiKey).where(
                    ApiKey.id.in_([owner.record.id, stranger.record.id, admin.record.id])
                )
            )
        await resources.close()
