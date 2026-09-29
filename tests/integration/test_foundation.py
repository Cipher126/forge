import os
from uuid import uuid7

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from forge.api.app import create_app
from forge.config import get_settings
from forge.persistence.database import create_database_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_migration_created_expected_tables() -> None:
    engine = create_database_engine(get_settings().postgres_url)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_table_names()
            )
    finally:
        await engine.dispose()

    assert {
        "alembic_version",
        "api_keys",
        "audit_events",
        "idempotency_keys",
        "job_attempts",
        "job_transitions",
        "jobs",
        "outbox_events",
    }.issubset(tables)


async def test_database_rejects_an_unknown_job_status() -> None:
    engine = create_database_engine(get_settings().postgres_url)
    api_key_id = uuid7()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(
                text(
                    """
                    INSERT INTO api_keys (
                        id, public_id, secret_digest, pepper_version, name, scopes,
                        enabled, rate_limit_requests, rate_limit_window_s
                    ) VALUES (
                        :id, :public_id, :digest, 1, 'integration test',
                        ARRAY['jobs:write'], true, 100, 60
                    )
                    """
                ),
                {"id": api_key_id, "public_id": f"test_{api_key_id}", "digest": b"x" * 32},
            )

            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO jobs (
                                id, api_key_id, job_type, handler_version, status,
                                priority, payload, max_attempts, attempt_count
                            ) VALUES (
                                :id, :api_key_id, 'test', 'v1', 'invented',
                                'normal', '{}'::jsonb, 5, 0
                            )
                            """
                        ),
                        {"id": uuid7(), "api_key_id": api_key_id},
                    )
            await transaction.rollback()
    finally:
        await engine.dispose()


async def test_readiness_reports_both_dependencies() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "postgres": True,
        "redis": True,
        "object_store": True,
    }
