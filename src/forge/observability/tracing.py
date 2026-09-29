import re
import secrets

import structlog

TRACEPARENT_PATTERN = re.compile(
    r"^00-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def parse_traceparent(value: str | None) -> tuple[str, str] | None:
    if value is None:
        return None
    match = TRACEPARENT_PATTERN.fullmatch(value.lower())
    if match is None or set(match.group("trace_id")) == {"0"}:
        return None
    return match.group("trace_id"), match.group("flags")


def bind_trace(trace_id: str) -> str:
    span_id = new_span_id()
    structlog.contextvars.bind_contextvars(trace_id=trace_id, span_id=span_id)
    return span_id


def current_trace_id() -> str:
    context = structlog.contextvars.get_contextvars()
    trace_id = context.get("trace_id")
    return trace_id if isinstance(trace_id, str) else new_trace_id()
