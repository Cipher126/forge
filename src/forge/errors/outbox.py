class OutboxError(Exception):
    """Base class for transactional-outbox failures."""


class OutboxClaimLostError(OutboxError):
    """An event can no longer be changed by the supplied lease token."""


class OutboxPublishError(OutboxError):
    """Redis rejected or could not accept an outbox event."""
