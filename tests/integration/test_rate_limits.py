import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from forge.api.app import create_app
from forge.config import get_settings
from forge.persistence.models import ApiKey
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


async def test_api_key_sliding_window_limit_is_atomic() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    async with resources.sessions.begin() as session:
        issued = await issue_api_key(
            session,
            name="rate limit test reader",
            scopes={"jobs:read"},
            pepper=settings.api_key_pepper.get_secret_value(),
            rate_limit_requests=1,
            rate_limit_window_s=60,
        )

    rate_key = api_key_rate_limit_key(issued.record.public_id)
    await resources.redis.delete(rate_key)
    app = create_app()
    headers = {"Authorization": f"Bearer {issued.credential}"}
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                responses = await asyncio.gather(
                    client.get("/v1/jobs", headers=headers),
                    client.get("/v1/jobs", headers=headers),
                )

        assert sorted(response.status_code for response in responses) == [200, 429]
        accepted = next(response for response in responses if response.status_code == 200)
        rejected = next(response for response in responses if response.status_code == 429)
        assert accepted.headers["X-RateLimit-Limit"] == "1"
        assert accepted.headers["X-RateLimit-Remaining"] == "0"
        assert int(rejected.headers["Retry-After"]) >= 1
        assert rejected.headers["X-RateLimit-Limit"] == "1"
        assert rejected.headers["X-RateLimit-Remaining"] == "0"
        assert rejected.json()["error"] == {
            "code": "rate_limit_exceeded",
            "message": "API key request limit exceeded",
            "details": {"limit": 1, "window_seconds": 60},
        }
    finally:
        await resources.redis.delete(rate_key)
        async with resources.sessions.begin() as session:
            await session.execute(delete(ApiKey).where(ApiKey.id == issued.record.id))
        await resources.close()
