import pytest

from forge.application.execution import (
    classify_handler_failure,
    normalize_handler_result,
    parse_queue_message,
)
from forge.domain.jobs import FailureKind
from forge.errors.execution import (
    InvalidHandlerResultError,
    InvalidQueueMessageError,
    PermanentHandlerError,
    TemporaryHandlerError,
)
from forge.errors.object_store import ObjectStoreUnavailableError
from forge.queue.names import PRIORITY_CYCLE
from forge.schemas.execution import ReservedQueueMessage


def test_priority_cycle_has_the_accepted_weight() -> None:
    assert PRIORITY_CYCLE.count("high") == 8
    assert PRIORITY_CYCLE.count("normal") == 2
    assert PRIORITY_CYCLE.count("low") == 1


def test_invalid_reserved_message_is_rejected() -> None:
    reservation = ReservedQueueMessage(
        raw="not-json",
        source_queue="forge:queue:normal",
        processing_queue="forge:processing:test",
    )

    with pytest.raises(InvalidQueueMessageError):
        parse_queue_message(reservation)


def test_handler_results_must_be_json_objects() -> None:
    with pytest.raises(InvalidHandlerResultError):
        normalize_handler_result(["not", "an", "object"])
    with pytest.raises(InvalidHandlerResultError):
        normalize_handler_result({"number": float("nan")})


def test_handler_failures_have_explicit_retry_policy() -> None:
    assert classify_handler_failure(TemporaryHandlerError("try again"))[:2] == (
        FailureKind.TEMPORARY,
        True,
    )
    assert classify_handler_failure(PermanentHandlerError("bad input"))[:2] == (
        FailureKind.PERMANENT,
        False,
    )
    assert classify_handler_failure(RuntimeError("bug"))[:2] == (
        FailureKind.TEMPORARY,
        True,
    )
    assert classify_handler_failure(ObjectStoreUnavailableError("offline")) == (
        FailureKind.TEMPORARY,
        True,
        "object_store_unavailable",
    )
