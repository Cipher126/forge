class CapacityError(Exception):
    """Base class for admission and execution capacity failures."""


class SystemOverloadedError(CapacityError):
    """Forge cannot safely accept more outstanding work."""

    def __init__(self, *, backlog: int, limit: int, retry_after_seconds: int) -> None:
        self.backlog = backlog
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Forge has reached its outstanding job limit")


class SubmissionCapacityExceededError(CapacityError):
    """An API process is already handling its safe number of submissions."""

    def __init__(
        self,
        *,
        active_submissions: int,
        limit: int,
        retry_after_seconds: int,
    ) -> None:
        self.active_submissions = active_submissions
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Forge is handling the maximum number of concurrent submissions")
