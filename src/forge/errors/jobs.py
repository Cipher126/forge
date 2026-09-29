from uuid import UUID


class JobError(Exception):
    """Base class for job application failures."""


class IdempotencyConflictError(JobError):
    """An idempotency key was reused for a different logical request."""


class JobNotFoundError(JobError, LookupError):
    """A job is absent or not visible to the requesting identity."""

    def __init__(self, job_id: UUID) -> None:
        self.job_id = job_id
        super().__init__("Job not found")


class JobCancellationConflictError(JobError):
    """A completed job cannot be cancelled."""


class JobReplayConflictError(JobError):
    """A job is not eligible for dead-letter replay."""


class JobResultUnavailableError(JobError):
    """A job does not yet have a retrievable result."""
