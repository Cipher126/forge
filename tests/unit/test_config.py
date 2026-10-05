import pytest
from pydantic import ValidationError

from forge.config import Settings


def test_lifecycle_policy_defaults_preserve_failure_evidence_longer() -> None:
    settings = Settings(_env_file=None)

    assert settings.idempotency_retention_hours == 24
    assert settings.successful_job_retention_days == 30
    assert settings.failed_job_retention_days == 90
    assert settings.orphan_object_grace_hours == 24
    assert settings.worker_drain_timeout_seconds == 90
    assert settings.api_submission_concurrency_limit == 6


def test_failure_retention_cannot_be_shorter_than_success_retention() -> None:
    with pytest.raises(ValidationError, match="failed-job retention"):
        Settings(
            _env_file=None,
            successful_job_retention_days=30,
            failed_job_retention_days=29,
        )


def test_submission_concurrency_cannot_exceed_database_pool_capacity() -> None:
    with pytest.raises(ValidationError, match="submission concurrency limit"):
        Settings(
            _env_file=None,
            postgres_pool_size=4,
            postgres_max_overflow=1,
            api_submission_concurrency_limit=6,
        )
