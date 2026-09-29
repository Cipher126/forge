import json

from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from starlette.requests import Request

from forge.api.error_handlers import redis_unavailable_handler, register_error_handlers


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


def test_dependency_handlers_are_registered() -> None:
    app = FastAPI()
    register_error_handlers(app)

    assert RedisError in app.exception_handlers
