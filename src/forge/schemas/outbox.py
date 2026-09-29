from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True, slots=True)
class ClaimedOutboxEvent:
    id: UUID
    job_id: UUID
    event_type: str
    payload: dict[str, object]
    dispatch_attempt: int
    lease_token: UUID


class QueueMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    event_id: UUID
    job_id: UUID
    job_type: str
    handler_version: str
    priority: Literal["high", "normal", "low"]
    trace_id: str | None = None
    parent_span_id: str | None = None
