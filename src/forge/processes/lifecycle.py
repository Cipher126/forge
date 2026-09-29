import asyncio
import signal
from collections.abc import Awaitable, Callable
from types import FrameType


def install_shutdown_handlers(shutdown: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        shutdown.set()

    def fallback_handler(_number: int, _frame: FrameType | None) -> None:
        loop.call_soon_threadsafe(request_shutdown)

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, request_shutdown)
        except (NotImplementedError, RuntimeError):
            signal.signal(signal_number, fallback_handler)


async def wait_for_shutdown(shutdown: asyncio.Event, timeout_seconds: float) -> bool:
    try:
        await asyncio.wait_for(shutdown.wait(), timeout=timeout_seconds)
    except TimeoutError:
        return False
    return True


async def run_process(process: Callable[[asyncio.Event], Awaitable[None]]) -> None:
    shutdown = asyncio.Event()
    install_shutdown_handlers(shutdown)
    await process(shutdown)
