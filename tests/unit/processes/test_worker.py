import asyncio
import json
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid7

import pytest
import structlog
from asyncpg import CannotConnectNowError
from redis.exceptions import ConnectionError as RedisConnectionError

from forge.config import Settings
from forge.processes.worker import (
    drain_worker_tasks,
    handle_reservation,
    handle_reservation_with_recovery,
)
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


async def test_raw_database_connection_error_requeues_unclaimed_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation = ReservedQueueMessage(
        raw='{"job_id":"test"}',
        source_queue="forge:queue:normal",
        processing_queue="forge:processing:worker-1",
    )
    handler = AsyncMock(side_effect=CannotConnectNowError("database is shutting down"))
    requeue = AsyncMock(return_value=True)
    monkeypatch.setattr("forge.processes.worker.handle_reservation", handler)
    monkeypatch.setattr("forge.processes.worker.requeue_message", requeue)
    resources = cast(RuntimeResources, SimpleNamespace(redis=object()))
    pool = cast(Executor, object())

    await handle_reservation_with_recovery(
        resources, Settings(), reservation, "worker-1", pool, pool, asyncio.Event()
    )

    requeue.assert_awaited_once_with(resources.redis, reservation)


async def test_worker_drain_finishes_completed_tasks() -> None:
    async def finish() -> None:
        await asyncio.sleep(0)

    task = asyncio.create_task(finish())

    assert await drain_worker_tasks({task}, timeout_seconds=1) is True
    assert task.done()
    assert not task.cancelled()


async def test_worker_drain_cancels_local_tasks_after_deadline() -> None:
    started = asyncio.Event()

    async def remain_active() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(remain_active())
    await started.wait()

    assert await drain_worker_tasks({task}, timeout_seconds=0.001) is False
    assert task.cancelled()


@pytest.mark.parametrize("outcome", ["denied", "acquire_failed", "release_failed"])
async def test_worker_closes_trace_on_permit_denial_and_redis_errors(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    reservation = ReservedQueueMessage(
        raw=json.dumps(
            {
                "event_id": str(uuid7()),
                "job_id": str(uuid7()),
                "job_type": "demo.generate_report",
                "handler_version": "1",
                "priority": "normal",
                "trace_id": "a" * 32,
            }
        ),
        source_queue="forge:queue:normal",
        processing_queue="forge:processing:test",
    )
    manager = MagicMock()
    acquire = AsyncMock(
        return_value=outcome != "denied",
        side_effect=RedisConnectionError("acquire failed") if outcome == "acquire_failed" else None,
    )
    release = AsyncMock(side_effect=RedisConnectionError("release failed"))
    requeue = AsyncMock(return_value=True)
    claim = AsyncMock(return_value=None)

    @asynccontextmanager
    async def transaction() -> Any:
        yield object()

    monkeypatch.setattr("forge.processes.worker.start_span", lambda *a, **k: (manager, "b" * 16))
    monkeypatch.setattr("forge.processes.worker.acquire_job_type_permit", acquire)
    monkeypatch.setattr("forge.processes.worker.release_job_type_permit", release)
    monkeypatch.setattr("forge.processes.worker.requeue_message", requeue)
    monkeypatch.setattr("forge.processes.worker.claim_job_attempt", claim)
    monkeypatch.setattr("forge.processes.worker.acknowledge_message", AsyncMock(return_value=True))
    resources = cast(
        RuntimeResources,
        SimpleNamespace(
            redis=object(),
            sessions=SimpleNamespace(begin=transaction),
        ),
    )
    pool = cast(Executor, object())
    try:
        if outcome == "denied":
            await handle_reservation(resources, Settings(), reservation, "test", pool, pool)
            requeue.assert_awaited_once()
            claim.assert_not_awaited()
        else:
            with pytest.raises(RedisConnectionError):
                await handle_reservation(resources, Settings(), reservation, "test", pool, pool)
        manager.__exit__.assert_called_once()
        assert "trace_id" not in structlog.contextvars.get_contextvars()
        if outcome != "release_failed":
            release.assert_not_awaited()
    finally:
        structlog.contextvars.clear_contextvars()
