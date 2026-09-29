from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID


class LeaseRenewalOutcome(StrEnum):
    RENEWED = "renewed"
    CANCELLATION_REQUESTED = "cancellation_requested"
    OWNERSHIP_LOST = "ownership_lost"


@dataclass(frozen=True, slots=True)
class ReservedQueueMessage:
    raw: str
    source_queue: str
    processing_queue: str


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: UUID
    attempt_id: UUID
    attempt_number: int
    ownership_token: UUID
    job_type: str
    handler_version: str
    payload: dict[str, Any] | None
    payload_blob_key: str | None = None


@dataclass(frozen=True, slots=True)
class JobTypePermit:
    job_type: str
    token: UUID
