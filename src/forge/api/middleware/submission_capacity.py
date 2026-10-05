from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from forge.api.error_handlers import submission_capacity_exceeded_handler
from forge.errors.capacity import SubmissionCapacityExceededError


def is_submission_request(request: Request) -> bool:
    if request.method != "POST":
        return False
    path = request.url.path.rstrip("/")
    return path == "/v1/jobs" or (path.startswith("/v1/jobs/") and path.endswith("/replay"))


async def enforce_submission_capacity(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if not is_submission_request(request):
        return await call_next(request)
    try:
        async with request.app.state.submission_gate.slot():
            return await call_next(request)
    except SubmissionCapacityExceededError as error:
        return await submission_capacity_exceeded_handler(request, error)
