from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from forge.domain.jobs import AttemptStatus, FailureKind, JobPriority


class SubmitJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]
    priority: JobPriority = JobPriority.NORMAL
    run_at: datetime | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=20)

    @field_validator("run_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("run_at must include a timezone offset")
        return value


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    replayed_from_job_id: UUID | None
    type: str = Field(validation_alias="job_type")
    handler_version: str
    status: str
    priority: str
    payload: dict[str, Any] | None
    payload_stored_externally: bool
    max_attempts: int
    attempt_count: int
    run_at: datetime | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    result: dict[str, Any] | None
    result_stored_externally: bool
    created_at: datetime
    updated_at: datetime


class JobListResponse(BaseModel):
    items: list[JobResponse]
    next_cursor: UUID | None


class JobResultResponse(BaseModel):
    result: dict[str, Any]
    stored_externally: bool


class JobAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    attempt_number: int
    status: AttemptStatus
    worker_id: str
    lease_expires_at: datetime | None
    started_at: datetime
    finished_at: datetime | None
    failure_kind: FailureKind | None
    error_code: str | None
    error_message: str | None
    retryable: bool | None
    created_at: datetime
    updated_at: datetime


class JobAttemptListResponse(BaseModel):
    items: list[JobAttemptResponse]


class JobTransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: UUID
    attempt_id: UUID | None
    from_status: str | None
    to_status: str
    reason: str
    actor_type: str
    actor_id: str | None
    details: dict[str, Any]
    created_at: datetime


class JobTransitionListResponse(BaseModel):
    items: list[JobTransitionResponse]
