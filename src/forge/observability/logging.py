import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "credential",
        "password",
        "payload",
        "result",
        "secret",
        "secret_key",
        "token",
    }
)
REDACTED = "[REDACTED]"
API_KEY_PATTERN = re.compile(r"forge_(?:live|test)_[A-Za-z0-9_-]+")
BEARER_PATTERN = re.compile(r"(?i)Bearer\s+[^\s,;]+")


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, str):
        value = API_KEY_PATTERN.sub(REDACTED, value)
        return BEARER_PATTERN.sub(f"Bearer {REDACTED}", value)
    return value


def redact_sensitive_fields(
    _logger: object,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    return {key: _redact_value(value, key=key) for key, value in event_dict.items()}


def configure_logging(level: str, *, service: str = "forge") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, format="%(message)s", level=numeric_level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_sensitive_fields,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=service)
