import asyncio

import pytest

from forge.processes.lifecycle import retry_delay_seconds, wait_for_shutdown


async def test_shutdown_wait_returns_immediately_when_requested() -> None:
    shutdown = asyncio.Event()
    shutdown.set()

    assert await wait_for_shutdown(shutdown, 60) is True


async def test_shutdown_wait_reports_poll_timeout() -> None:
    shutdown = asyncio.Event()

    assert await wait_for_shutdown(shutdown, 0.001) is False


def test_retry_delay_grows_exponentially_and_stays_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("forge.processes.lifecycle.random.uniform", lambda low, high: high)

    assert retry_delay_seconds(0, base_ms=250, cap_ms=5000) == 0.25
    assert retry_delay_seconds(3, base_ms=250, cap_ms=5000) == 2.0
    assert retry_delay_seconds(100_000, base_ms=250, cap_ms=5000) == 5.0


def test_retry_delay_applies_equal_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("forge.processes.lifecycle.random.uniform", lambda low, high: low)

    assert retry_delay_seconds(2, base_ms=250, cap_ms=5000) == 0.5
