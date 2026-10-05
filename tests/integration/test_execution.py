import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid7

import pytest
from sqlalchemy import delete, func, select, update

from forge.application.execution import (
    claim_job_attempt,
    complete_attempt_failure,
    complete_attempt_success,
    execute_handler,
    parse_queue_message,
    renew_attempt_lease,
)
from forge.application.jobs import cancel_job
from forge.application.outbox import queue_message
from forge.application.queue_recovery import restore_missing_queued_deliveries
from forge.application.recovery import recover_expired_attempts
from forge.application.reservation_recovery import reconcile_orphaned_reservations
from forge.config import get_settings
from forge.errors.execution import AttemptOwnershipLostError, HandlerCancelledError
from forge.handlers.registry import create_handler_registry
from forge.persistence.models import ApiKey, Job, JobAttempt, JobTransition
from forge.processes.worker import handle_reservation
from forge.queue.client import acknowledge_message, reserve_next_message
from forge.queue.names import processing_queue_key, queue_key
from forge.resources import RuntimeResources
from forge.schemas.execution import ClaimedJob, LeaseRenewalOutcome, ReservedQueueMessage
from forge.schemas.outbox import ClaimedOutboxEvent, QueueMessage
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_unsupported_handler_version_fails_without_retry() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    worker_id = "unsupported-version-worker"
    processing = processing_queue_key(worker_id)
    async with resources.sessions.begin() as session:
        issued = await issue_api_key(
            session,
            name="unsupported handler version owner",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        job = Job(
            id=uuid7(),
            api_key_id=issued.record.id,
            job_type="demo.generate_report",
            handler_version="retired-v0",
            status="queued",
            priority="normal",
            payload={"report_id": "unsupported", "format": "pdf"},
            max_attempts=3,
            attempt_count=0,
        )
        session.add(job)
        await session.flush()

    raw = QueueMessage(
        event_id=uuid7(),
        job_id=job.id,
        job_type=job.job_type,
        handler_version=job.handler_version,
        priority="normal",
    ).model_dump_json()
    reservation = ReservedQueueMessage(
        raw=raw,
        source_queue=queue_key("normal"),
        processing_queue=processing,
    )
    await resources.redis.rpush(processing, raw)

    try:
        with ThreadPoolExecutor(max_workers=1) as thread_pool:
            await handle_reservation(
                resources,
                settings,
                reservation,
                worker_id,
                thread_pool,
                thread_pool,
            )

        async with resources.sessions() as session:
            failed_job = await session.get(Job, job.id)
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job.id)
            )
            transition = await session.scalar(
                select(JobTransition)
                .where(
                    JobTransition.job_id == job.id,
                    JobTransition.reason == "permanent_failure",
                )
                .order_by(JobTransition.id.desc())
            )
            assert failed_job is not None
            assert attempt is not None
            assert transition is not None
            assert failed_job.status == "failed"
            assert failed_job.attempt_count == 1
            assert failed_job.active_attempt_id is None
            assert attempt.status == "failed"
            assert attempt.failure_kind == "permanent"
            assert attempt.retryable is False
            assert attempt.error_code == "handler_version_unsupported"
            assert transition.details == {
                "failure_kind": "permanent",
                "error_code": "handler_version_unsupported",
            }
            assert await resources.redis.llen(processing) == 0
    finally:
        await resources.redis.delete(processing)
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.id == job.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == issued.record.id))
        await resources.close()


async def test_duplicate_messages_create_one_attempt_owner() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    worker_id = "integration-worker"
    await resources.redis.delete(queue_key("normal"), processing_queue_key(worker_id))
    async with resources.sessions.begin() as session:
        issued = await issue_api_key(
            session,
            name="execution integration owner",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        job = Job(
            id=uuid7(),
            api_key_id=issued.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="queued",
            priority="normal",
            payload={"report_id": "duplicate", "format": "pdf"},
            max_attempts=3,
            attempt_count=0,
        )
        session.add(job)
        await session.flush()

    event = ClaimedOutboxEvent(
        id=uuid7(),
        job_id=job.id,
        event_type="job.queued",
        payload={
            "job_type": job.job_type,
            "handler_version": job.handler_version,
            "priority": job.priority,
        },
        dispatch_attempt=1,
        lease_token=uuid7(),
    )
    raw = queue_message(event).model_dump_json()
    await resources.redis.rpush(queue_key("normal"), raw, raw)

    try:
        first, position = await reserve_next_message(
            resources.redis, worker_id=worker_id, cycle_position=0
        )
        second, _ = await reserve_next_message(
            resources.redis, worker_id=worker_id, cycle_position=position
        )
        assert first is not None
        assert second is not None
        assert await resources.redis.llen(processing_queue_key(worker_id)) == 2

        async def claim(reservation: ReservedQueueMessage) -> object:
            message = parse_queue_message(reservation)
            async with resources.sessions.begin() as session:
                return await claim_job_attempt(
                    session,
                    message=message,
                    worker_id=worker_id,
                    lease_seconds=settings.worker_attempt_lease_seconds,
                )

        claims = await asyncio.gather(claim(first), claim(second))
        assert sum(item is not None for item in claims) == 1
        claimed = next(item for item in claims if isinstance(item, ClaimedJob))
        assert await acknowledge_message(resources.redis, first)
        assert await acknowledge_message(resources.redis, second)

        async with resources.sessions() as session:
            persisted_job = await session.get(Job, job.id)
            assert persisted_job is not None
            assert persisted_job.status == "running"
            assert persisted_job.attempt_count == 1
            assert persisted_job.active_attempt_id is not None
            assert (
                await session.scalar(
                    select(func.count()).select_from(JobAttempt).where(JobAttempt.job_id == job.id)
                )
                == 1
            )

        async with resources.sessions.begin() as session:
            assert await renew_attempt_lease(
                session,
                claimed=claimed,
                lease_seconds=settings.worker_attempt_lease_seconds,
            )
        definition = create_handler_registry().resolve(claimed.job_type, claimed.handler_version)
        result = await execute_handler(definition, claimed.payload)
        async with resources.sessions.begin() as session:
            await complete_attempt_success(session, claimed=claimed, result=result)
        async with resources.sessions() as session:
            completed_job = await session.get(Job, job.id)
            assert completed_job is not None
            assert completed_job.status == "succeeded"
            assert completed_job.result == {
                "report_id": "duplicate",
                "format": "pdf",
                "artifact": "reports/duplicate.pdf",
            }
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(JobTransition)
                    .where(
                        JobTransition.job_id == job.id,
                        JobTransition.reason == "attempt_claimed",
                    )
                )
                == 1
            )
    finally:
        await resources.redis.delete(queue_key("normal"), processing_queue_key(worker_id))
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.id == job.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == issued.record.id))
        await resources.close()


@pytest.mark.failure
async def test_expired_owner_cannot_persist_a_late_result() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    async with resources.sessions.begin() as session:
        issued = await issue_api_key(
            session,
            name="expired execution owner",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        job = Job(
            id=uuid7(),
            api_key_id=issued.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="queued",
            priority="normal",
            payload={"report_id": "late", "format": "json"},
            max_attempts=3,
            attempt_count=0,
        )
        session.add(job)
        await session.flush()
        claimed = await claim_job_attempt(
            session,
            message=QueueMessage(
                event_id=uuid7(),
                job_id=job.id,
                job_type=job.job_type,
                handler_version=job.handler_version,
                priority="normal",
            ),
            worker_id="expired-worker",
            lease_seconds=30,
        )
        assert claimed is not None

    try:
        async with resources.sessions.begin() as session:
            await session.execute(
                update(JobAttempt)
                .where(JobAttempt.id == claimed.attempt_id)
                .values(lease_expires_at=func.clock_timestamp() - timedelta(seconds=1))
            )

        with pytest.raises(AttemptOwnershipLostError):
            async with resources.sessions.begin() as session:
                await complete_attempt_success(
                    session,
                    claimed=claimed,
                    result={"must_not": "persist"},
                )

        async with resources.sessions() as session:
            persisted_job = await session.get(Job, job.id)
            assert persisted_job is not None
            assert persisted_job.status == "running"
            assert persisted_job.result is None

        async with resources.sessions.begin() as session:
            recovered = await recover_expired_attempts(
                session,
                batch_size=10,
                retry_base_ms=1,
                retry_cap_ms=1,
            )
        assert recovered == [job.id]
        async with resources.sessions() as session:
            recovered_job = await session.get(Job, job.id)
            recovered_attempt = await session.get(JobAttempt, claimed.attempt_id)
            assert recovered_job is not None
            assert recovered_attempt is not None
            assert recovered_job.status == "retry_scheduled"
            assert recovered_job.active_attempt_id is None
            assert recovered_attempt.status == "abandoned"
            assert recovered_attempt.failure_kind == "worker_lost"
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.id == job.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == issued.record.id))
        await resources.close()


async def test_running_cancellation_is_completed_by_current_owner() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    async with resources.sessions.begin() as session:
        issued = await issue_api_key(
            session,
            name="cancellation execution owner",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        job = Job(
            id=uuid7(),
            api_key_id=issued.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="queued",
            priority="normal",
            payload={"report_id": "cancel", "format": "pdf"},
            max_attempts=3,
            attempt_count=0,
        )
        session.add(job)
        await session.flush()
        claimed = await claim_job_attempt(
            session,
            message=QueueMessage(
                event_id=uuid7(),
                job_id=job.id,
                job_type=job.job_type,
                handler_version=job.handler_version,
                priority="normal",
            ),
            worker_id="cancellation-worker",
            lease_seconds=30,
        )
        assert claimed is not None

    try:
        async with resources.sessions.begin() as session:
            cancelling = await cancel_job(
                session,
                job_id=job.id,
                api_key=issued.record,
            )
            assert cancelling.status == "cancellation_requested"

        async with resources.sessions.begin() as session:
            outcome = await renew_attempt_lease(
                session,
                claimed=claimed,
                lease_seconds=30,
            )
        assert outcome is LeaseRenewalOutcome.CANCELLATION_REQUESTED

        async with resources.sessions.begin() as session:
            final_status = await complete_attempt_failure(
                session,
                claimed=claimed,
                error=HandlerCancelledError("job cancellation was requested"),
                retry_base_ms=1,
                retry_cap_ms=1,
            )
        assert final_status.value == "cancelled"

        async with resources.sessions() as session:
            cancelled_job = await session.get(Job, job.id)
            cancelled_attempt = await session.get(JobAttempt, claimed.attempt_id)
            assert cancelled_job is not None
            assert cancelled_attempt is not None
            assert cancelled_job.status == "cancelled"
            assert cancelled_job.active_attempt_id is None
            assert cancelled_attempt.status == "cancelled"
            assert cancelled_attempt.failure_kind == "cancelled"
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.id == job.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == issued.record.id))
        await resources.close()


@pytest.mark.failure
async def test_orphaned_reservation_is_atomically_requeued() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    worker_id = "crashed-worker"
    processing = processing_queue_key(worker_id)
    await resources.redis.delete(processing, queue_key("normal"))
    async with resources.sessions.begin() as session:
        issued = await issue_api_key(
            session,
            name="reservation recovery owner",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        job = Job(
            id=uuid7(),
            api_key_id=issued.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="queued",
            priority="normal",
            payload={"report_id": "orphan", "format": "pdf"},
            max_attempts=3,
            attempt_count=0,
        )
        session.add(job)
        await session.flush()
    raw = QueueMessage(
        event_id=uuid7(),
        job_id=job.id,
        job_type=job.job_type,
        handler_version=job.handler_version,
        priority="normal",
    ).model_dump_json()
    await resources.redis.rpush(processing, raw)

    try:
        async with resources.sessions() as session:
            summary = await reconcile_orphaned_reservations(session, resources.redis)
        assert summary.requeued == 1
        assert await resources.redis.llen(processing) == 0
        assert await resources.redis.lindex(queue_key("normal"), 0) == raw
    finally:
        await resources.redis.delete(processing, queue_key("normal"))
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.id == job.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == issued.record.id))
        await resources.close()


@pytest.mark.failure
async def test_missing_redis_delivery_is_reconstructed_from_postgres() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    destination = queue_key("high")
    await resources.redis.delete(destination)
    async with resources.sessions.begin() as session:
        issued = await issue_api_key(
            session,
            name="redis loss recovery owner",
            scopes={"jobs:write"},
            pepper=settings.api_key_pepper.get_secret_value(),
        )
        job = Job(
            id=uuid7(),
            api_key_id=issued.record.id,
            job_type="demo.generate_report",
            handler_version="1",
            status="queued",
            priority="high",
            payload={"report_id": "redis-loss", "format": "json"},
            max_attempts=3,
            attempt_count=0,
        )
        session.add(job)

    try:
        async with resources.sessions() as session:
            restored = await restore_missing_queued_deliveries(session, resources.redis)
        assert job.id in restored
        assert await resources.redis.llen(destination) == 1

        async with resources.sessions() as session:
            restored_again = await restore_missing_queued_deliveries(
                session, resources.redis
            )
        assert job.id not in restored_again
        assert await resources.redis.llen(destination) == 1

        raw = await resources.redis.lindex(destination, 0)
        assert raw is not None
        restored_message = QueueMessage.model_validate_json(raw)
        assert restored_message.job_id == job.id
        assert restored_message.priority == "high"
    finally:
        await resources.redis.delete(destination)
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.id == job.id))
            await session.execute(delete(ApiKey).where(ApiKey.id == issued.record.id))
        await resources.close()
