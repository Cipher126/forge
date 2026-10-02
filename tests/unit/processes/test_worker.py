import asyncio
from concurrent.futures import Executor
from types import SimpleNamespace
from typing import Any, cast

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from forge.config import Settings
from forge.processes.worker import handle_reservation_with_recovery
from forge.resources import RuntimeResources
from forge.schemas.execution import ReservedQueueMessage


async def test_interrupted_reservation_is_requeued_after_redis_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation = ReservedQueueMessage(
        raw='{"job_id":"test"}',
        source_queue="forge:queue:normal",
        processing_queue="forge:processing:worker-1",
    )
    calls = 0

    async def fail_during_handling(*_args: object) -> None:
        raise RedisConnectionError("simulated outage")

    async def recover_reservation(*_args: object) -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr("forge.processes.worker.handle_reservation", fail_during_handling)
    monkeypatch.setattr("forge.processes.worker.requeue_message", recover_reservation)
    settings = cast(
        Settings,
        cast(Any, type("RetrySettings", (), {"redis_retry_base_ms": 1, "redis_retry_cap_ms": 2})()),
    )

    resources = cast(RuntimeResources, cast(Any, SimpleNamespace(redis=object())))

    await handle_reservation_with_recovery(
        resources,
        settings,
        reservation,
        "worker-1",
        cast(Executor, cast(Any, object())),
        cast(Executor, cast(Any, object())),
        asyncio.Event(),
    )

    assert calls == 1
