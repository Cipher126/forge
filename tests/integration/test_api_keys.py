import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from forge.api.app import create_app
from forge.config import get_settings
from forge.persistence.models import ApiKey, AuditEvent
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_admin_can_issue_key_and_scope_is_enforced() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    async with resources.sessions.begin() as session:
        admin = await issue_api_key(
            session,
            name="integration admin",
            scopes={"admin"},
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
                    "/v1/admin/api-keys",
                    headers={"Authorization": f"Bearer {admin.credential}"},
                    json={"name": "integration writer", "scopes": ["jobs:write"]},
                )
                assert response.status_code == 201
                writer_credential = response.json()["api_key"]
                writer_id = response.json()["id"]

                listed = await client.get(
                    "/v1/admin/api-keys",
                    params={"enabled": True, "scope": "jobs:write"},
                    headers={"Authorization": f"Bearer {admin.credential}"},
                )
                assert listed.status_code == 200
                listed_writer = next(
                    item for item in listed.json()["items"] if item["id"] == writer_id
                )
                assert listed_writer["name"] == "integration writer"
                assert "secret_digest" not in listed_writer

                handlers = await client.get(
                    "/v1/handlers",
                    headers={"Authorization": f"Bearer {admin.credential}"},
                )
                assert handlers.status_code == 200
                demo = next(
                    item
                    for item in handlers.json()["items"]
                    if item["job_type"] == "demo.generate_report"
                )
                assert demo["version"] == "1"
                assert demo["active"] is True
                assert demo["payload_schema"]["properties"]["report_id"]["maxLength"] == 128

                forbidden = await client.post(
                    "/v1/admin/api-keys",
                    headers={"Authorization": f"Bearer {writer_credential}"},
                    json={"name": "must fail", "scopes": ["jobs:read"]},
                )
                assert forbidden.status_code == 403
                assert forbidden.json()["error"]["code"] == "insufficient_scope"

                revoked = await client.post(
                    f"/v1/admin/api-keys/{writer_id}/revoke",
                    headers={"Authorization": f"Bearer {admin.credential}"},
                )
                assert revoked.status_code == 200
                assert revoked.json()["enabled"] is False
                assert revoked.json()["revoked_at"] is not None

                repeated_revoke = await client.post(
                    f"/v1/admin/api-keys/{writer_id}/revoke",
                    headers={"Authorization": f"Bearer {admin.credential}"},
                )
                assert repeated_revoke.status_code == 200

                revoked_credential = await client.post(
                    "/v1/admin/api-keys",
                    headers={"Authorization": f"Bearer {writer_credential}"},
                    json={"name": "must also fail", "scopes": ["jobs:read"]},
                )
                assert revoked_credential.status_code == 401
                assert revoked_credential.json()["error"]["code"] == "invalid_api_key"

                revoke_events = await client.get(
                    "/v1/admin/audit-events",
                    params={
                        "actor_public_id": admin.record.public_id,
                        "action": "api_key.revoked",
                    },
                    headers={"Authorization": f"Bearer {admin.credential}"},
                )
                assert revoke_events.status_code == 200
                assert len(revoke_events.json()["items"]) == 1
                assert revoke_events.json()["items"][0]["resource_id"] == writer_id

                unauthorized = await client.post(
                    "/v1/admin/api-keys",
                    headers={"Authorization": "Bearer forge_live_invalid_invalid"},
                    json={"name": "must fail", "scopes": ["jobs:read"]},
                )
                assert unauthorized.status_code == 401
                assert unauthorized.json()["error"]["code"] == "invalid_api_key"
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(
                delete(AuditEvent).where(AuditEvent.actor_public_id == admin.record.public_id)
            )
            await session.execute(
                delete(ApiKey).where(ApiKey.name.in_(["integration admin", "integration writer"]))
            )
        await resources.close()
