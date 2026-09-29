from enum import StrEnum


class JobStatus(StrEnum):
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCELLATION_REQUESTED = "cancellation_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    CANCELLED = "cancelled"


class JobPriority(StrEnum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class AttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"


class FailureKind(StrEnum):
    TEMPORARY = "temporary"
    PERMANENT = "permanent"
    WORKER_LOST = "worker_lost"
    CANCELLED = "cancelled"


class TransitionActor(StrEnum):
    API = "api"
    SCHEDULER = "scheduler"
    WORKER = "worker"
    RECOVERY = "recovery"
    ADMIN = "admin"
    SYSTEM = "system"


TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.DEAD_LETTERED,
        JobStatus.CANCELLED,
    }
)
