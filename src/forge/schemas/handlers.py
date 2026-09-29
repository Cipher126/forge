from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from forge.domain.handlers import ExecutionMode


class GenerateReportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str = Field(min_length=1, max_length=128)
    format: Literal["pdf", "csv", "json"]


class HandlerResponse(BaseModel):
    job_type: str
    version: str
    active: bool
    execution_mode: ExecutionMode
    timeout_seconds: int
    default_max_attempts: int
    payload_schema: dict[str, Any]


class HandlerListResponse(BaseModel):
    items: list[HandlerResponse]
