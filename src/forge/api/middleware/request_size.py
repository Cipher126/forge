from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status

from forge.api.error_handlers import error_response
from forge.config import get_settings


async def enforce_request_size(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    limit = get_settings().max_request_body_bytes
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > limit:
            return _request_too_large(declared_size, limit)

    body = await request.body()
    if len(body) > limit:
        return _request_too_large(len(body), limit)
    return await call_next(request)


def _request_too_large(size_bytes: int, limit_bytes: int) -> Response:
    return error_response(
        status.HTTP_413_CONTENT_TOO_LARGE,
        code="request_too_large",
        message="HTTP request body exceeds the configured limit",
        details={"size_bytes": size_bytes, "limit_bytes": limit_bytes},
    )
