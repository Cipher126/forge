import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from forge.api.app import create_app
from forge.config import get_settings
from forge.persistence.models import ApiKey, Job
from forge.queue.names import api_key_rate_limit_key
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_request_body_limit_rejects_before_job_creation() -> None:
    settings = get_settings()
    original_request_limit = settings.max_request_body_bytes
    original_inline_limit = settings.max_inline_payload_bytes
    resources = RuntimeResources.create(settings)
    async with resources.sessions.begin() as session:
        issued = await issue_api_key(
            session,
            name="payload limit test writer",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )

    app = create_app()
    headers = {"Authorization": f"Bearer {issued.credential}"}
    body = {
        "type": "demo.generate_report",
        "payload": {"report_id": "payload-limit", "format": "json"},
    }
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                settings.max_request_body_bytes = 32
                request_rejected = await client.post("/v1/jobs", headers=headers, json=body)
                assert request_rejected.status_code == 413
                assert request_rejected.json()["error"]["code"] == "request_too_large"
                assert request_rejected.json()["error"]["details"]["limit_bytes"] == 32

        async with resources.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Job).where(Job.api_key_id == issued.record.id)
                )
                == 0
            )
    finally:
        settings.max_request_body_bytes = original_request_limit
        settings.max_inline_payload_bytes = original_inline_limit
        await resources.redis.delete(api_key_rate_limit_key(issued.record.public_id))
        async with resources.sessions.begin() as session:
            await session.execute(delete(ApiKey).where(ApiKey.id == issued.record.id))
        await resources.close()
