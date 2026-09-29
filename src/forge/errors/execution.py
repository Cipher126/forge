class ExecutionError(Exception):
    """Base class for worker execution and ownership failures."""


class InvalidQueueMessageError(ExecutionError):
    """A Redis queue entry does not satisfy the versioned message contract."""


class AttemptClaimError(ExecutionError):
    """PostgreSQL state prevents a valid attempt claim."""


class AttemptOwnershipLostError(ExecutionError):
    """A worker can no longer mutate an attempt using its ownership token."""


class JobTypePermitLostError(ExecutionError):
    """A worker can no longer renew its leased job-type capacity permit."""


class HandlerExecutionError(ExecutionError):
    """Base class for errors raised while invoking a registered handler."""


class TemporaryHandlerError(HandlerExecutionError):
    """The operation may succeed when attempted again."""


class PermanentHandlerError(HandlerExecutionError):
    """Repeating the same operation is not expected to succeed."""


class HandlerCancelledError(HandlerExecutionError):
    """Handler execution stopped because cancellation was requested."""


class HandlerTimedOutError(TemporaryHandlerError):
    """Handler execution exceeded its registered timeout."""


class InvalidHandlerResultError(PermanentHandlerError):
    """A handler returned a result that cannot be stored as JSON."""
