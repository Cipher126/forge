import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from forge.api.app import create_app
from forge.config import get_settings
from forge.persistence.models import ApiKey, Job
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_admission_limit_is_atomic_and_preserves_idempotent_retries() -> None:
    settings = get_settings()
    original_limit = settings.admission_backlog_limit
    original_retry_after = settings.admission_retry_after_seconds
    settings.admission_backlog_limit = 1
    settings.admission_retry_after_seconds = 7
    resources = RuntimeResources.create(settings)
    async with resources.sessions.begin() as session:
        owner = await issue_api_key(
            session,
            name="admission test owner",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )

    app = create_app()
    bodies = [
        {
            "type": "demo.generate_report",
            "payload": {"report_id": f"admission-{index}", "format": "json"},
        }
        for index in range(2)
    ]
    headers = [
        {
            "Authorization": f"Bearer {owner.credential}",
            "Idempotency-Key": f"admission-{index}",
        }
        for index in range(2)
    ]
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                responses = await asyncio.gather(
                    client.post("/v1/jobs", headers=headers[0], json=bodies[0]),
                    client.post("/v1/jobs", headers=headers[1], json=bodies[1]),
                )
                assert sorted(response.status_code for response in responses) == [202, 503]

                rejected = next(response for response in responses if response.status_code == 503)
                assert rejected.headers["Retry-After"] == "7"
                assert rejected.json()["error"] == {
                    "code": "system_overloaded",
                    "message": "Forge has reached its outstanding job limit",
                    "details": {"backlog": 1, "limit": 1},
                }

                accepted_index = next(
                    index for index, response in enumerate(responses) if response.status_code == 202
                )
                repeated = await client.post(
                    "/v1/jobs",
                    headers=headers[accepted_index],
                    json=bodies[accepted_index],
                )
                assert repeated.status_code == 200
                assert repeated.json()["id"] == responses[accepted_index].json()["id"]

                cancelled = await client.post(
                    f"/v1/jobs/{repeated.json()['id']}/cancel",
                    headers=headers[accepted_index],
                )
                assert cancelled.status_code == 200

                replacement_index = 1 - accepted_index
                replacement = await client.post(
                    "/v1/jobs",
                    headers=headers[replacement_index],
                    json=bodies[replacement_index],
                )
                assert replacement.status_code == 202
    finally:
        settings.admission_backlog_limit = original_limit
        settings.admission_retry_after_seconds = original_retry_after
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.api_key_id == owner.record.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == owner.record.id))
        await resources.close()
