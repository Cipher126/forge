import os
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from forge.api.app import create_app
from forge.config import get_settings
from forge.persistence.models import ApiKey, Job, JobAttempt, JobTransition
from forge.queue.client import (
    acquire_job_type_permit,
    refresh_worker_heartbeat,
    release_job_type_permit,
)
from forge.queue.names import api_key_rate_limit_key, processing_queue_key, queue_key
from forge.resources import RuntimeResources
from forge.schemas.execution import JobTypePermit
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_admin_operational_endpoints_combine_database_and_redis_state() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    worker_id = f"operations-test-{uuid7()}"
    heartbeat_key = f"forge:worker:{worker_id}:heartbeat"
    processing_key = processing_queue_key(worker_id)
    high_queue = queue_key("high")
    queued_message = f"operations-queued-{uuid7()}"
    now = datetime.now(UTC)
    async with resources.sessions.begin() as session:
        admin = await issue_api_key(
            session,
            name="operations test admin",
            scopes={"admin"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        reader = await issue_api_key(
            session,
            name="operations test reader",
            scopes={"jobs:read"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        running_job = Job(
            id=uuid7(),
            api_key_id=admin.record.id,
            job_type="demo.generate_report",
            handler_version="v1",
            status="queued",
            priority="high",
            payload={"report_id": "operations-running", "format": "json"},
            max_attempts=3,
            attempt_count=1,
            queued_at=now,
        )
        terminal_job = Job(
            id=uuid7(),
            api_key_id=admin.record.id,
            job_type="demo.generate_report",
            handler_version="v1",
            status="succeeded",
            priority="normal",
            payload={"report_id": "operations-complete", "format": "json"},
            max_attempts=3,
            attempt_count=1,
            run_at=now - timedelta(seconds=12),
            queued_at=now - timedelta(seconds=10),
            started_at=now - timedelta(seconds=6),
            finished_at=now - timedelta(seconds=1),
            result={"ok": True},
        )
        session.add_all([running_job, terminal_job])
        await session.flush()
        attempt = JobAttempt(
            id=uuid7(),
            job_id=running_job.id,
            attempt_number=1,
            status="running",
            worker_id=worker_id,
            ownership_token=uuid7(),
            lease_expires_at=now + timedelta(minutes=1),
            started_at=now,
        )
        session.add(attempt)
        session.add(
            JobTransition(
                job_id=terminal_job.id,
                from_status="running",
                to_status="retry_scheduled",
                reason="temporary_failure",
                actor_type="worker",
                actor_id=worker_id,
            )
        )
        session.add_all(
            [
                JobTransition(
                    job_id=terminal_job.id,
                    from_status="queued",
                    to_status="running",
                    reason="attempt_claimed",
                    actor_type="worker",
                    actor_id=worker_id,
                    details={"claim_seconds": 0.012},
                ),
                JobTransition(
                    job_id=terminal_job.id,
                    from_status="scheduled",
                    to_status="queued",
                    reason="scheduled_job_due",
                    actor_type="scheduler",
                    actor_id="operations-test",
                ),
                JobTransition(
                    job_id=terminal_job.id,
                    from_status="running",
                    to_status="succeeded",
                    reason="attempt_succeeded",
                    actor_type="worker",
                    actor_id=worker_id,
                ),
            ]
        )
        await session.flush()
        running_job.status = "running"
        running_job.active_attempt_id = attempt.id
        running_job.started_at = now

    await refresh_worker_heartbeat(resources.redis, worker_id, 60)
    await resources.redis.rpush(high_queue, queued_message)
    await resources.redis.rpush(processing_key, f"operations-reserved-{uuid7()}")
    permit = JobTypePermit(job_type="demo.generate_report", token=uuid7())
    assert await acquire_job_type_permit(
        resources.redis,
        permit=permit,
        limit=settings.job_type_concurrency_limits[permit.job_type],
        lease_seconds=60,
    )
    app = create_app()
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                admin_headers = {"Authorization": f"Bearer {admin.credential}"}
                summary_response = await client.get(
                    "/v1/admin/operations/summary", headers=admin_headers
                )
                assert summary_response.status_code == 200
                summary = summary_response.json()
                assert summary["jobs_by_status"]["running"] >= 1
                assert summary["jobs_by_status"]["succeeded"] >= 1
                assert summary["outstanding_jobs"] >= 1
                assert summary["terminal_jobs"] >= 1
                assert summary["active_workers"] >= 1
                assert summary["queues"]["queued_by_priority"]["high"] >= 1
                assert summary["queues"]["processing_by_worker"][worker_id] == 1

                workers_response = await client.get(
                    "/v1/admin/operations/workers", headers=admin_headers
                )
                worker = next(
                    item
                    for item in workers_response.json()["items"]
                    if item["worker_id"] == worker_id
                )
                assert worker["status"] == "online"
                assert worker["active_attempts"] == 1
                assert worker["reserved_messages"] == 1
                assert worker["heartbeat_ttl_seconds"] > 0

                queues_response = await client.get(
                    "/v1/admin/operations/queues", headers=admin_headers
                )
                assert queues_response.status_code == 200
                assert queues_response.json()["durable_outstanding"] >= 1

                performance_response = await client.get(
                    "/v1/admin/operations/performance?window_minutes=60",
                    headers=admin_headers,
                )
                assert performance_response.status_code == 200
                performance = performance_response.json()
                assert performance["submitted_jobs"] >= 2
                assert performance["completed_jobs"] >= 1
                assert performance["succeeded_jobs"] >= 1
                assert performance["retries_scheduled"] >= 1
                assert performance["throughput_per_minute"] > 0
                assert performance["queue_wait"]["sample_count"] >= 1
                assert performance["queue_wait"]["p50_seconds"] is not None
                assert performance["execution_time"]["sample_count"] >= 1
                assert performance["scheduling_lag"]["sample_count"] >= 1
                assert performance["claim_time"]["sample_count"] >= 1
                assert performance["claim_time"]["p95_seconds"] >= 0
                assert performance["end_to_end_time"]["sample_count"] >= 1

                metrics_response = await client.get(
                    "/v1/admin/operations/metrics", headers=admin_headers
                )
                assert metrics_response.status_code == 200
                assert "text/plain" in metrics_response.headers["content-type"]
                assert 'forge_jobs{status="running"}' in metrics_response.text
                assert 'forge_queue_messages{priority="high"}' in metrics_response.text
                assert "forge_workers_online" in metrics_response.text
                assert "forge_outbox_unpublished" in metrics_response.text
                assert "forge_attempts_running" in metrics_response.text
                assert "forge_jobs_due" in metrics_response.text
                assert "forge_jobs_enqueued_rate_per_minute" in metrics_response.text
                assert "forge_jobs_completed_rate_per_minute" in metrics_response.text
                assert "forge_job_failure_ratio" in metrics_response.text
                assert "forge_dead_letter_jobs" in metrics_response.text
                assert (
                    'forge_job_type_permits_used{job_type="demo.generate_report"}'
                    in metrics_response.text
                )
                assert (
                    'forge_job_type_permit_utilization_ratio{job_type="demo.generate_report"}'
                    in metrics_response.text
                )

                events_response = await client.get(
                    "/v1/admin/operations/events?after=0&once=true",
                    headers=admin_headers,
                )
                assert events_response.status_code == 200
                assert "text/event-stream" in events_response.headers["content-type"]
                assert events_response.text.startswith(": connected\n\n")
                assert "event: job.transition" in events_response.text

                forbidden = await client.get(
                    "/v1/admin/operations/summary",
                    headers={"Authorization": f"Bearer {reader.credential}"},
                )
                assert forbidden.status_code == 403
    finally:
        await release_job_type_permit(resources.redis, permit)
        await resources.redis.delete(heartbeat_key, processing_key)
        await resources.redis.lrem(high_queue, 1, queued_message)
        await resources.redis.delete(
            api_key_rate_limit_key(admin.record.public_id),
            api_key_rate_limit_key(reader.record.public_id),
        )
        async with resources.sessions.begin() as session:
            running = await session.get(Job, running_job.id)
            if running is not None:
                running.active_attempt_id = None
                running.status = "cancelled"
                running.finished_at = datetime.now(UTC)
            await session.flush()
            await session.execute(delete(Job).where(Job.id.in_([running_job.id, terminal_job.id])))
            await session.execute(
                delete(ApiKey).where(ApiKey.id.in_([admin.record.id, reader.record.id]))
            )
        await resources.close()
