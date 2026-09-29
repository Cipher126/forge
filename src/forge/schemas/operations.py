from datetime import datetime

from pydantic import BaseModel


class QueueStatistics(BaseModel):
    queued_by_priority: dict[str, int]
    queued_total: int
    processing_by_worker: dict[str, int]
    processing_total: int
    durable_outstanding: int


class WorkerHealth(BaseModel):
    worker_id: str
    status: str
    heartbeat_ttl_seconds: int
    active_attempts: int
    reserved_messages: int


class WorkerHealthListResponse(BaseModel):
    items: list[WorkerHealth]


class OperationsSummary(BaseModel):
    generated_at: datetime
    jobs_by_status: dict[str, int]
    outstanding_jobs: int
    terminal_jobs: int
    active_workers: int
    queues: QueueStatistics


class DurationDistribution(BaseModel):
    sample_count: int
    p50_seconds: float | None
    p95_seconds: float | None


class PerformanceStatistics(BaseModel):
    generated_at: datetime
    window_minutes: int
    submitted_jobs: int
    completed_jobs: int
    succeeded_jobs: int
    failed_jobs: int
    dead_lettered_jobs: int
    cancelled_jobs: int
    retries_scheduled: int
    success_rate: float | None
    throughput_per_minute: float
    queue_wait: DurationDistribution
    execution_time: DurationDistribution
    scheduling_lag: DurationDistribution
