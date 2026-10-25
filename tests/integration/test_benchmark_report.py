import os
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from sqlalchemy import delete

from forge.config import get_settings
from forge.persistence.models import ApiKey, Job, JobTransition
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key
from scripts.report_validation_jobs import report

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_large_run_report_counts_persisted_jobs_claim_timing_and_result_corruption() -> None:
    resources = RuntimeResources.create(get_settings())
    now = datetime.now(UTC)
    key_id = uuid7()
    try:
        async with resources.sessions.begin() as session:
            owner = await issue_api_key(
                session,
                name="benchmark report test",
                scopes={"jobs:write"},
                pepper=get_settings().api_key_pepper.get_secret_value(),
            )
            key_id = owner.record.id
            for index in range(2):
                job = Job(
                    id=uuid7(),
                    api_key_id=key_id,
                    job_type="demo.generate_report",
                    handler_version="1",
                    status="succeeded",
                    priority="normal",
                    payload={"report_id": "expected", "format": "json"},
                    result={"report_id": "expected" if index == 0 else "wrong", "format": "json"},
                    max_attempts=1,
                    attempt_count=1,
                    queued_at=now - timedelta(seconds=3),
                    started_at=now - timedelta(seconds=2),
                    finished_at=now - timedelta(seconds=1),
                )
                session.add(job)
                await session.flush()
                session.add(
                    JobTransition(
                        job_id=job.id,
                        from_status="queued",
                        to_status="running",
                        reason="attempt_claimed",
                        actor_type="worker",
                        actor_id="test",
                        details={"claim_seconds": 0.025},
                    )
                )
        result = await report(key_id)
        assert result["total_jobs"] == 2
        assert result["jobs_by_status"] == {"succeeded": 2}
        assert result["invalid_report_results"] == 1
        assert result["timings"]["claim"]["samples"] == 2  # type: ignore[index]
        assert result["timings"]["claim"]["p95_seconds"] == pytest.approx(0.025)  # type: ignore[index]
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.api_key_id == key_id))
            await session.execute(delete(ApiKey).where(ApiKey.id == key_id))
        await resources.close()
