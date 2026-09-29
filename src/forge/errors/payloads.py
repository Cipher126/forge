class PayloadError(Exception):
    """Base class for request and job payload size failures."""


class InlinePayloadTooLargeError(PayloadError):
    """A validated job payload exceeds PostgreSQL's configured inline limit."""

    def __init__(self, *, size_bytes: int, limit_bytes: int) -> None:
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes
        super().__init__("Job payload exceeds the inline storage limit")
