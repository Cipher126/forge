from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FORGE_",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    otlp_tracing_enabled: bool = False
    otlp_traces_endpoint: str = "http://127.0.0.1:4318/v1/traces"
    trace_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_workers: int = Field(default=1, ge=1, le=32)
    api_submission_concurrency_limit: int = Field(default=6, ge=1, le=100)
    max_request_body_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    max_inline_payload_bytes: int = Field(default=256 * 1024, ge=1)
    postgres_url: str = "postgresql+asyncpg://forge:forge@127.0.0.1:54321/forge"
    postgres_pool_size: int = Field(default=5, ge=1, le=100)
    postgres_max_overflow: int = Field(default=5, ge=0, le=100)
    postgres_connect_timeout_seconds: float = Field(default=0.5, gt=0, le=60)
    postgres_pool_timeout_seconds: float = Field(default=1.0, gt=0, le=300)
    postgres_pool_recycle_seconds: int = Field(default=1800, ge=30)
    postgres_retry_base_ms: int = Field(default=250, ge=1, le=60_000)
    postgres_retry_cap_ms: int = Field(default=5000, ge=1, le=300_000)
    redis_url: str = "redis://127.0.0.1:56379/0"
    redis_connect_timeout_seconds: float = Field(default=0.5, gt=0, le=60)
    redis_operation_timeout_seconds: float = Field(default=1.0, gt=0, le=60)
    redis_retry_base_ms: int = Field(default=250, ge=1, le=60_000)
    redis_retry_cap_ms: int = Field(default=5000, ge=1, le=300_000)
    object_store_endpoint_url: str = "http://127.0.0.1:59000"
    object_store_access_key: str = "forge"
    object_store_secret_key: SecretStr = SecretStr("development-only-change-me")
    object_store_bucket: str = "forge-jobs"
    object_store_connect_timeout_seconds: float = Field(default=0.5, gt=0, le=60)
    object_store_read_timeout_seconds: float = Field(default=1.0, gt=0, le=300)
    object_store_max_attempts: int = Field(default=2, ge=1, le=10)
    object_store_operation_timeout_seconds: float = Field(default=3.0, gt=0, le=300)
    api_key_pepper: SecretStr = SecretStr("development-only-change-me")
    dispatcher_batch_size: int = Field(default=100, ge=1, le=1000)
    dispatcher_lease_seconds: int = Field(default=30, ge=1)
    dispatcher_poll_interval_ms: int = Field(default=500, ge=10)
    dispatcher_retry_base_ms: int = Field(default=500, ge=1)
    dispatcher_retry_cap_ms: int = Field(default=30_000, ge=1)
    worker_attempt_lease_seconds: int = Field(default=30, ge=1)
    worker_lease_renew_interval_seconds: int = Field(default=10, ge=1)
    worker_retry_base_ms: int = Field(default=1000, ge=1)
    worker_retry_cap_ms: int = Field(default=60_000, ge=1)
    worker_concurrency: int = Field(default=4, ge=1, le=256)
    worker_poll_interval_ms: int = Field(default=100, ge=10)
    worker_drain_timeout_seconds: float = Field(default=90.0, gt=0, le=3600)
    job_type_concurrency_limits: dict[str, int] = Field(
        default_factory=lambda: {"demo.generate_report": 2}
    )
    job_type_permit_lease_seconds: int = Field(default=30, ge=2)
    job_type_permit_renew_interval_seconds: int = Field(default=10, ge=1)
    admission_backlog_limit: int = Field(default=50_000, ge=1, le=50_000)
    admission_retry_after_seconds: int = Field(default=1, ge=1, le=3600)
    scheduler_batch_size: int = Field(default=100, ge=1, le=1000)
    scheduler_poll_interval_ms: int = Field(default=500, ge=10)
    recovery_batch_size: int = Field(default=100, ge=1, le=1000)
    recovery_poll_interval_ms: int = Field(default=1000, ge=10)
    delivery_reconciliation_interval_seconds: int = Field(default=30, ge=1)
    maintenance_interval_seconds: int = Field(default=3600, ge=1)
    maintenance_batch_size: int = Field(default=100, ge=1, le=1000)
    idempotency_retention_hours: int = Field(default=24, ge=1)
    successful_job_retention_days: int = Field(default=30, ge=1)
    failed_job_retention_days: int = Field(default=90, ge=1)
    orphan_object_grace_hours: int = Field(default=24, ge=0)
    operational_stream_poll_ms: int = Field(default=1000, ge=100, le=10_000)
    worker_heartbeat_interval_seconds: int = Field(default=5, ge=1)
    worker_heartbeat_ttl_seconds: int = Field(default=15, ge=2)

    @model_validator(mode="after")
    def reject_development_secrets_in_production(self) -> Settings:
        if self.max_inline_payload_bytes > self.max_request_body_bytes:
            raise ValueError("inline payload limit cannot exceed the HTTP request-body limit")
        if any(
            not job_type or limit < 1
            for job_type, limit in self.job_type_concurrency_limits.items()
        ):
            raise ValueError("job-type concurrency limits require a name and positive limit")
        if self.job_type_permit_renew_interval_seconds >= self.job_type_permit_lease_seconds:
            raise ValueError("job-type permit renewal interval must be shorter than its lease")
        if self.api_submission_concurrency_limit > (
            self.postgres_pool_size + self.postgres_max_overflow
        ):
            raise ValueError(
                "API submission concurrency limit cannot exceed PostgreSQL pool capacity"
            )
        if self.redis_retry_base_ms > self.redis_retry_cap_ms:
            raise ValueError("Redis retry base must not exceed its cap")
        if self.failed_job_retention_days < self.successful_job_retention_days:
            raise ValueError(
                "failed-job retention must be at least as long as successful-job retention"
            )
        if (
            self.environment == "production"
            and self.api_key_pepper.get_secret_value() == "development-only-change-me"
        ):
            raise ValueError("FORGE_API_KEY_PEPPER must be set in production")
        if (
            self.environment == "production"
            and self.object_store_secret_key.get_secret_value() == "development-only-change-me"
        ):
            raise ValueError("FORGE_OBJECT_STORE_SECRET_KEY must be set in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
