import re
import secrets
from contextlib import AbstractContextManager

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    SpanKind,
    TraceFlags,
    TraceState,
    set_span_in_context,
)

TRACEPARENT_PATTERN = re.compile(
    r"^00-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def parse_traceparent(value: str | None) -> tuple[str, str] | None:
    parsed = parse_traceparent_full(value)
    return (parsed[0], parsed[2]) if parsed is not None else None


def parse_traceparent_full(value: str | None) -> tuple[str, str, str] | None:
    if value is None:
        return None
    match = TRACEPARENT_PATTERN.fullmatch(value.lower())
    if match is None or set(match.group("trace_id")) == {"0"}:
        return None
    return match.group("trace_id"), match.group("span_id"), match.group("flags")


def bind_trace(trace_id: str) -> str:
    span_id = new_span_id()
    structlog.contextvars.bind_contextvars(trace_id=trace_id, span_id=span_id)
    return span_id


def current_trace_id() -> str:
    context = structlog.contextvars.get_contextvars()
    trace_id = context.get("trace_id")
    return trace_id if isinstance(trace_id, str) else new_trace_id()


def current_span_id() -> str:
    context = structlog.contextvars.get_contextvars()
    span_id = context.get("span_id")
    return span_id if isinstance(span_id, str) else new_span_id()


def configure_tracing(
    *, service: str, enabled: bool, endpoint: str, sample_ratio: float
) -> TracerProvider | None:
    if not enabled:
        return None
    provider = TracerProvider(
        resource=Resource.create({"service.name": f"forge-{service}"}),
        sampler=TraceIdRatioBased(sample_ratio),
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    return provider


def start_span(
    name: str,
    *,
    trace_id: str,
    parent_span_id: str | None,
    kind: SpanKind,
) -> tuple[AbstractContextManager[Span], str]:
    context = None
    if parent_span_id is not None:
        parent = SpanContext(
            trace_id=int(trace_id, 16),
            span_id=int(parent_span_id, 16),
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        )
        context = set_span_in_context(NonRecordingSpan(parent))
    manager = trace.get_tracer("forge").start_as_current_span(name, context=context, kind=kind)
    span = manager.__enter__()
    span_context = span.get_span_context()
    span_id = f"{span_context.span_id:016x}" if span_context.is_valid else new_span_id()
    return manager, span_id
