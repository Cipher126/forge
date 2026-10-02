from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from forge.domain.handlers import (
    ExecutionMode,
    HandlerRegistry,
)
from forge.errors.handlers import HandlerRegistryError, UnknownHandlerError
from forge.handlers.registry import create_handler_registry
from forge.schemas.handlers import GenerateReportPayload, WaitPayload


class Payload(BaseModel):
    value: int


async def async_handler(payload: Payload) -> dict[str, Any]:
    return payload.model_dump()


def sync_handler(payload: Payload) -> dict[str, Any]:
    return payload.model_dump()


def test_active_version_and_exact_version_are_resolved_independently() -> None:
    registry = HandlerRegistry()
    version_1 = registry.register(
        job_type="tests.example",
        version="1",
        payload_model=Payload,
        handler=async_handler,
        execution_mode=ExecutionMode.ASYNC,
        active=True,
    )
    version_2 = registry.register(
        job_type="tests.example",
        version="2",
        payload_model=Payload,
        handler=sync_handler,
        execution_mode=ExecutionMode.THREAD,
    )

    assert registry.resolve("tests.example") is version_1
    assert registry.resolve("tests.example", "2") is version_2

    registry.activate("tests.example", "2")
    assert registry.resolve("tests.example") is version_2
    assert registry.resolve("tests.example", "1") is version_1


def test_duplicate_registration_fails_at_startup() -> None:
    registry = HandlerRegistry()
    arguments = {
        "job_type": "tests.example",
        "version": "1",
        "payload_model": Payload,
        "handler": async_handler,
        "execution_mode": ExecutionMode.ASYNC,
    }
    registry.register(**arguments)

    with pytest.raises(HandlerRegistryError, match="duplicated"):
        registry.register(**arguments)


def test_execution_mode_must_match_handler_function() -> None:
    registry = HandlerRegistry()

    with pytest.raises(HandlerRegistryError, match="async def"):
        registry.register(
            job_type="tests.example",
            version="1",
            payload_model=Payload,
            handler=sync_handler,
            execution_mode=ExecutionMode.ASYNC,
        )


def test_unknown_handler_is_explicit() -> None:
    with pytest.raises(UnknownHandlerError, match="no active handler"):
        HandlerRegistry().resolve("tests.missing")


def test_demo_payload_rejects_unknown_fields() -> None:
    definition = create_handler_registry().resolve("demo.generate_report")

    payload = definition.validate_payload({"report_id": "quarterly", "format": "pdf"})
    assert payload == GenerateReportPayload(report_id="quarterly", format="pdf")

    with pytest.raises(ValidationError):
        definition.validate_payload({"report_id": "quarterly", "format": "pdf", "unexpected": True})


def test_wait_payload_is_bounded_for_failure_validation() -> None:
    definition = create_handler_registry().resolve("demo.wait")

    payload = definition.validate_payload({"task_id": "worker-kill", "duration_seconds": 30})
    assert payload == WaitPayload(task_id="worker-kill", duration_seconds=30)

    with pytest.raises(ValidationError):
        definition.validate_payload({"task_id": "too-long", "duration_seconds": 121})
