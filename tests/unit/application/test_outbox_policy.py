from uuid import uuid7

import pytest

from forge.application.outbox import queue_message, retry_delay_ms
from forge.errors.outbox import OutboxPublishError
from forge.schemas.outbox import ClaimedOutboxEvent


def claimed_event(payload: dict[str, object]) -> ClaimedOutboxEvent:
    return ClaimedOutboxEvent(
        id=uuid7(),
        job_id=uuid7(),
        event_type="job.queued",
        payload=payload,
        dispatch_attempt=1,
        lease_token=uuid7(),
    )


def test_queue_message_rejects_incomplete_outbox_payload() -> None:
    with pytest.raises(OutboxPublishError, match="invalid payload"):
        queue_message(claimed_event({"priority": "normal"}))


def test_retry_delay_uses_full_jitter_with_a_cap() -> None:
    for attempt in range(1, 20):
        delay = retry_delay_ms(attempt, base_ms=500, cap_ms=30_000)
        assert 0 <= delay <= 30_000
