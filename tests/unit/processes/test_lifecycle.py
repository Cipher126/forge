import asyncio

from forge.processes.lifecycle import wait_for_shutdown


async def test_shutdown_wait_returns_immediately_when_requested() -> None:
    shutdown = asyncio.Event()
    shutdown.set()

    assert await wait_for_shutdown(shutdown, 60) is True


async def test_shutdown_wait_reports_poll_timeout() -> None:
    shutdown = asyncio.Event()

    assert await wait_for_shutdown(shutdown, 0.001) is False
