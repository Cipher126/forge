import asyncio
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg import CannotConnectNowError

from forge.config import Settings
from forge.errors.object_store import ObjectStoreUnavailableError
from forge.processes import dispatcher, maintenance, recovery, scheduler


@pytest.mark.parametrize(
    "process,error",
    [
        (dispatcher, CannotConnectNowError("database is shutting down")),
        (scheduler, CannotConnectNowError("database is shutting down")),
        (recovery, CannotConnectNowError("database is shutting down")),
        (maintenance, CannotConnectNowError("database is shutting down")),
        (maintenance, ObjectStoreUnavailableError("storage unavailable")),
    ],
)
async def test_dependency_error_backs_off_without_exiting_process(
    monkeypatch: pytest.MonkeyPatch,
    process: ModuleType,
    error: Exception,
) -> None:
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(side_effect=error)
    resources = SimpleNamespace(sessions=MagicMock(), close=AsyncMock())
    resources.sessions.begin.return_value = transaction
    settings = Settings()
    shutdown = asyncio.Event()
    delays: list[float] = []

    async def request_shutdown(_event: asyncio.Event, delay: float) -> bool:
        delays.append(delay)
        shutdown.set()
        return True

    monkeypatch.setattr(process, "get_settings", lambda: settings)
    monkeypatch.setattr(process, "configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(process.RuntimeResources, "create", lambda _settings: resources)
    monkeypatch.setattr(process, "wait_for_shutdown", request_shutdown)
    run = getattr(process, f"run_{process.__name__.rsplit('.', 1)[1]}")

    await run(shutdown)

    assert len(delays) == 1
    assert 0 < delays[0] <= settings.postgres_retry_base_ms / 1000
    resources.close.assert_awaited_once()
