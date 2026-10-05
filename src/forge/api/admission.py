import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from forge.errors.capacity import SubmissionCapacityExceededError


class SubmissionGate:
    """Bound concurrent submission requests before they compete for dependencies."""

    def __init__(self, *, limit: int, retry_after_seconds: int) -> None:
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        return self._active

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._lock:
            if self._active >= self.limit:
                raise SubmissionCapacityExceededError(
                    active_submissions=self._active,
                    limit=self.limit,
                    retry_after_seconds=self.retry_after_seconds,
                )
            self._active += 1
        try:
            yield
        finally:
            async with self._lock:
                self._active -= 1
