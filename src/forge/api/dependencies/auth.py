from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from forge.config import get_settings
from forge.errors.auth import InsufficientScopeError, InvalidApiKeyError, RateLimitExceededError
from forge.persistence.models import ApiKey
from forge.security.api_keys import authenticate_api_key
from forge.security.rate_limits import check_rate_limit

bearer_scheme = HTTPBearer(auto_error=False)


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.resources.sessions() as session:
        yield session


async def authenticated_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(database_session)],
    request: Request,
    response: Response,
) -> ApiKey:
    record = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        record = await authenticate_api_key(
            session,
            credential=credentials.credentials,
            pepper=get_settings().api_key_pepper.get_secret_value(),
        )
    if record is None:
        raise InvalidApiKeyError("Invalid or expired API key")
    decision = await check_rate_limit(
        request.app.state.resources.redis,
        public_id=record.public_id,
        limit=record.rate_limit_requests,
        window_seconds=record.rate_limit_window_s,
    )
    if not decision.allowed:
        raise RateLimitExceededError(
            limit=record.rate_limit_requests,
            window_seconds=record.rate_limit_window_s,
            retry_after_seconds=decision.retry_after_seconds,
        )
    response.headers["X-RateLimit-Limit"] = str(record.rate_limit_requests)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    return record


def require_scope(
    scope: str,
) -> Callable[..., Coroutine[Any, Any, ApiKey]]:
    async def dependency(
        api_key: Annotated[ApiKey, Depends(authenticated_api_key)],
    ) -> ApiKey:
        if scope not in api_key.scopes and "admin" not in api_key.scopes:
            raise InsufficientScopeError(scope)
        return api_key

    return dependency


def require_scope_detached(
    scope: str,
) -> Callable[..., Coroutine[Any, Any, ApiKey]]:
    async def dependency(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
        request: Request,
        response: Response,
    ) -> ApiKey:
        record = None
        if credentials is not None and credentials.scheme.lower() == "bearer":
            async with request.app.state.resources.sessions() as session:
                record = await authenticate_api_key(
                    session,
                    credential=credentials.credentials,
                    pepper=get_settings().api_key_pepper.get_secret_value(),
                )
        if record is None:
            raise InvalidApiKeyError("Invalid or expired API key")
        decision = await check_rate_limit(
            request.app.state.resources.redis,
            public_id=record.public_id,
            limit=record.rate_limit_requests,
            window_seconds=record.rate_limit_window_s,
        )
        if not decision.allowed:
            raise RateLimitExceededError(
                limit=record.rate_limit_requests,
                window_seconds=record.rate_limit_window_s,
                retry_after_seconds=decision.retry_after_seconds,
            )
        if scope not in record.scopes and "admin" not in record.scopes:
            raise InsufficientScopeError(scope)
        response.headers["X-RateLimit-Limit"] = str(record.rate_limit_requests)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return record

    return dependency
