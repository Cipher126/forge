import json

import httpx
import pytest
from asyncpg import (
    AdminShutdownError,
    CannotConnectNowError,
    CrashShutdownError,
    PostgresConnectionError,
    TooManyConnectionsError,
)
from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.requests import Request

from forge.api.error_handlers import (
    database_unavailable_handler,
    redis_unavailable_handler,
    register_error_handlers,
)


async def test_redis_failure_has_controlled_service_unavailable_contract() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/v1/jobs", "headers": []})

    response = await redis_unavailable_handler(
        request, RedisConnectionError("redis://user:secret@internal-host")
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert json.loads(response.body) == {
        "error": {
            "code": "dependency_unavailable",
            "message": "redis is temporarily unavailable",
            "details": {"dependency": "redis"},
        }
    }
    assert b"internal-host" not in response.body
    assert b"secret" not in response.body


async def test_database_pool_exhaustion_has_controlled_service_unavailable_contract() -> None:
    request = Request({"type": "http", "method": "POST", "path": "/v1/jobs", "headers": []})

    response = await database_unavailable_handler(
        request,
        SQLAlchemyTimeoutError("QueuePool limit reached for postgres://user:secret@internal-host"),
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert json.loads(response.body) == {
        "error": {
            "code": "dependency_unavailable",
            "message": "postgres is temporarily unavailable",
            "details": {"dependency": "postgres"},
        }
    }
    assert b"internal-host" not in response.body
    assert b"secret" not in response.body


def test_dependency_handlers_are_registered() -> None:
    app = FastAPI()
    register_error_handlers(app)

    assert RedisError in app.exception_handlers
    assert SQLAlchemyTimeoutError in app.exception_handlers


@pytest.mark.parametrize(
    "error_class",
    [
        CannotConnectNowError,
        AdminShutdownError,
        CrashShutdownError,
        PostgresConnectionError,
        TooManyConnectionsError,
    ],
)
async def test_raw_driver_connection_error_returns_sanitized_503(
    error_class: type[Exception],
) -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/connection-error")
    async def raise_connection_error() -> None:
        raise error_class("postgres://user:secret@internal-host")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/connection-error")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["details"] == {"dependency": "postgres"}
    assert "internal-host" not in response.text
    assert "secret" not in response.text
