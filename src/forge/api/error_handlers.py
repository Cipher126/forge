from collections.abc import Awaitable, Callable

import structlog
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import (
    DisconnectionError,
    InterfaceError,
    OperationalError,
)
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.responses import JSONResponse

from forge.errors.api_keys import ApiKeyNotFoundError
from forge.errors.auth import (
    InsufficientScopeError,
    InvalidApiKeyError,
    RateLimitExceededError,
    UnsupportedScopesError,
)
from forge.errors.capacity import SubmissionCapacityExceededError, SystemOverloadedError
from forge.errors.dependencies import DependencyUnavailableError
from forge.errors.handlers import UnknownHandlerError
from forge.errors.jobs import (
    IdempotencyConflictError,
    JobCancellationConflictError,
    JobNotFoundError,
    JobReplayConflictError,
    JobResultUnavailableError,
)
from forge.errors.object_store import ObjectStoreUnavailableError
from forge.errors.payloads import InlinePayloadTooLargeError
from forge.persistence.database import DRIVER_CONNECTION_ERRORS
from forge.schemas.errors import ErrorDetail, ErrorResponse

ErrorHandler = Callable[[Request, Exception], Awaitable[JSONResponse]]
logger = structlog.get_logger()


def error_response(
    status_code: int,
    *,
    code: str,
    message: str,
    details: dict[str, object] | list[dict[str, object]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details or {}))
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(body.model_dump()),
        headers=headers,
    )


async def invalid_api_key_handler(_request: Request, error: InvalidApiKeyError) -> JSONResponse:
    return error_response(
        status.HTTP_401_UNAUTHORIZED,
        code="invalid_api_key",
        message=str(error),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def api_key_not_found_handler(_request: Request, error: ApiKeyNotFoundError) -> JSONResponse:
    return error_response(
        status.HTTP_404_NOT_FOUND,
        code="api_key_not_found",
        message=str(error),
    )


async def insufficient_scope_handler(
    _request: Request, error: InsufficientScopeError
) -> JSONResponse:
    return error_response(
        status.HTTP_403_FORBIDDEN,
        code="insufficient_scope",
        message=str(error),
        details={"required_scope": error.required_scope},
    )


async def rate_limit_exceeded_handler(
    _request: Request, error: RateLimitExceededError
) -> JSONResponse:
    return error_response(
        status.HTTP_429_TOO_MANY_REQUESTS,
        code="rate_limit_exceeded",
        message=str(error),
        details={"limit": error.limit, "window_seconds": error.window_seconds},
        headers={
            "Retry-After": str(error.retry_after_seconds),
            "X-RateLimit-Limit": str(error.limit),
            "X-RateLimit-Remaining": "0",
        },
    )


async def unsupported_scopes_handler(
    _request: Request, error: UnsupportedScopesError
) -> JSONResponse:
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="unsupported_scopes",
        message=str(error),
        details={"scopes": sorted(error.scopes)},
    )


async def unknown_handler_handler(_request: Request, error: UnknownHandlerError) -> JSONResponse:
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="unknown_handler",
        message=str(error),
    )


async def idempotency_conflict_handler(
    _request: Request, error: IdempotencyConflictError
) -> JSONResponse:
    return error_response(
        status.HTTP_409_CONFLICT,
        code="idempotency_conflict",
        message=str(error),
    )


async def job_not_found_handler(_request: Request, error: JobNotFoundError) -> JSONResponse:
    return error_response(
        status.HTTP_404_NOT_FOUND,
        code="job_not_found",
        message=str(error),
    )


async def job_cancellation_conflict_handler(
    _request: Request, error: JobCancellationConflictError
) -> JSONResponse:
    return error_response(
        status.HTTP_409_CONFLICT,
        code="job_cancellation_conflict",
        message=str(error),
    )


async def job_replay_conflict_handler(
    _request: Request, error: JobReplayConflictError
) -> JSONResponse:
    return error_response(
        status.HTTP_409_CONFLICT,
        code="job_replay_conflict",
        message=str(error),
    )


async def job_result_unavailable_handler(
    _request: Request, error: JobResultUnavailableError
) -> JSONResponse:
    return error_response(
        status.HTTP_409_CONFLICT,
        code="job_result_unavailable",
        message=str(error),
    )


async def system_overloaded_handler(
    _request: Request, error: SystemOverloadedError
) -> JSONResponse:
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        code="system_overloaded",
        message=str(error),
        details={"backlog": error.backlog, "limit": error.limit},
        headers={"Retry-After": str(error.retry_after_seconds)},
    )


async def submission_capacity_exceeded_handler(
    _request: Request, error: SubmissionCapacityExceededError
) -> JSONResponse:
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        code="submission_capacity_exceeded",
        message=str(error),
        details={
            "active_submissions": error.active_submissions,
            "limit": error.limit,
        },
        headers={"Retry-After": str(error.retry_after_seconds)},
    )


async def inline_payload_too_large_handler(
    _request: Request, error: InlinePayloadTooLargeError
) -> JSONResponse:
    return error_response(
        status.HTTP_413_CONTENT_TOO_LARGE,
        code="payload_too_large",
        message=str(error),
        details={"size_bytes": error.size_bytes, "limit_bytes": error.limit_bytes},
    )


async def dependency_unavailable_handler(
    request: Request, error: DependencyUnavailableError
) -> JSONResponse:
    await logger.awarning(
        "dependency_unavailable",
        dependency=error.dependency,
        path=request.url.path,
    )
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        code="dependency_unavailable",
        message=str(error),
        details={"dependency": error.dependency},
        headers={"Retry-After": str(error.retry_after_seconds)},
    )


async def redis_unavailable_handler(request: Request, _error: RedisError) -> JSONResponse:
    return await dependency_unavailable_handler(request, DependencyUnavailableError("redis"))


async def object_store_unavailable_handler(
    request: Request, _error: BotoCoreError | ClientError | ObjectStoreUnavailableError
) -> JSONResponse:
    return await dependency_unavailable_handler(request, DependencyUnavailableError("object_store"))


async def database_unavailable_handler(
    request: Request,
    _error: Exception,
) -> JSONResponse:
    return await dependency_unavailable_handler(request, DependencyUnavailableError("postgres"))


async def request_validation_handler(
    _request: Request, error: RequestValidationError
) -> JSONResponse:
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation_error",
        message="Request validation failed",
        details=jsonable_encoder(error.errors()),
    )


async def payload_validation_handler(_request: Request, error: ValidationError) -> JSONResponse:
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="payload_validation_error",
        message="Handler payload validation failed",
        details=jsonable_encoder(error.errors()),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(InvalidApiKeyError, invalid_api_key_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ApiKeyNotFoundError, api_key_not_found_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RateLimitExceededError, rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_exception_handler(InsufficientScopeError, insufficient_scope_handler)  # type: ignore[arg-type]
    app.add_exception_handler(UnsupportedScopesError, unsupported_scopes_handler)  # type: ignore[arg-type]
    app.add_exception_handler(UnknownHandlerError, unknown_handler_handler)  # type: ignore[arg-type]
    app.add_exception_handler(IdempotencyConflictError, idempotency_conflict_handler)  # type: ignore[arg-type]
    app.add_exception_handler(JobNotFoundError, job_not_found_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        JobCancellationConflictError,
        job_cancellation_conflict_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        JobReplayConflictError,
        job_replay_conflict_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(JobResultUnavailableError, job_result_unavailable_handler)  # type: ignore[arg-type]
    app.add_exception_handler(SystemOverloadedError, system_overloaded_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        SubmissionCapacityExceededError,
        submission_capacity_exceeded_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(InlinePayloadTooLargeError, inline_payload_too_large_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, request_validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationError, payload_validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(DependencyUnavailableError, dependency_unavailable_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RedisError, redis_unavailable_handler)  # type: ignore[arg-type]
    app.add_exception_handler(BotoCoreError, object_store_unavailable_handler)
    app.add_exception_handler(ClientError, object_store_unavailable_handler)
    app.add_exception_handler(ObjectStoreUnavailableError, object_store_unavailable_handler)
    app.add_exception_handler(OperationalError, database_unavailable_handler)
    app.add_exception_handler(InterfaceError, database_unavailable_handler)
    app.add_exception_handler(DisconnectionError, database_unavailable_handler)
    app.add_exception_handler(SQLAlchemyTimeoutError, database_unavailable_handler)
    app.add_exception_handler(OSError, database_unavailable_handler)
    for error_class in DRIVER_CONNECTION_ERRORS:
        app.add_exception_handler(error_class, database_unavailable_handler)
