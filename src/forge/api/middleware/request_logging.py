from time import monotonic
from uuid import uuid7

import structlog
from fastapi import Request, Response
from opentelemetry.trace import SpanKind
from starlette.middleware.base import RequestResponseEndpoint

from forge.observability.tracing import new_trace_id, parse_traceparent_full, start_span

logger = structlog.get_logger()


async def log_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
    request_id = str(uuid7())
    parsed_trace = parse_traceparent_full(request.headers.get("traceparent"))
    trace_id, parent_span_id, flags = (
        parsed_trace if parsed_trace is not None else (new_trace_id(), None, "01")
    )
    span_manager, span_id = start_span(
        f"{request.method} {request.url.path}",
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        kind=SpanKind.SERVER,
    )
    structlog.contextvars.bind_contextvars(trace_id=trace_id, span_id=span_id)
    started = monotonic()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
        await logger.ainfo(
            "http_request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round((monotonic() - started) * 1000, 3),
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["traceparent"] = f"00-{trace_id}-{span_id}-{flags}"
        return response
    finally:
        structlog.contextvars.unbind_contextvars("request_id", "trace_id", "span_id")
        span_manager.__exit__(None, None, None)
