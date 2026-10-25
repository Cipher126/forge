import asyncio
from time import monotonic

import structlog
from redis.exceptions import RedisError

from forge.application.queue_recovery import restore_missing_queued_deliveries
from forge.application.recovery import recover_expired_attempts
from forge.application.reservation_recovery import reconcile_orphaned_reservations
from forge.config import get_settings
from forge.observability.logging import configure_logging
from forge.persistence.database import DATABASE_RETRY_ERRORS
from forge.processes.lifecycle import retry_delay_seconds, run_process, wait_for_shutdown
from forge.resources import RuntimeResources

logger = structlog.get_logger()


async def run_recovery(shutdown: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service="recovery")
    resources = RuntimeResources.create(settings)
    try:
        next_delivery_reconciliation = 0.0
        redis_retry_attempt = 0
        database_retry_attempt = 0
        while not shutdown.is_set():
            try:
                async with resources.sessions.begin() as session:
                    recovered = await recover_expired_attempts(
                        session,
                        batch_size=settings.recovery_batch_size,
                        retry_base_ms=settings.worker_retry_base_ms,
                        retry_cap_ms=settings.worker_retry_cap_ms,
                    )
                async with resources.sessions() as session:
                    reservations = await reconcile_orphaned_reservations(
                        session,
                        resources.redis,
                    )
                    should_reconcile_deliveries = monotonic() >= next_delivery_reconciliation
                    restored = []
                    if should_reconcile_deliveries:
                        restored = await restore_missing_queued_deliveries(session, resources.redis)
            except RedisError as error:
                delay = retry_delay_seconds(
                    redis_retry_attempt,
                    base_ms=settings.redis_retry_base_ms,
                    cap_ms=settings.redis_retry_cap_ms,
                )
                redis_retry_attempt += 1
                await logger.awarning(
                    "redis_reconnect_wait",
                    operation="recovery",
                    delay_seconds=round(delay, 3),
                    error=str(error),
                )
                await wait_for_shutdown(shutdown, delay)
                continue
            except DATABASE_RETRY_ERRORS as error:
                delay = retry_delay_seconds(
                    database_retry_attempt,
                    base_ms=settings.postgres_retry_base_ms,
                    cap_ms=settings.postgres_retry_cap_ms,
                )
                database_retry_attempt += 1
                await logger.awarning(
                    "database_reconnect_wait",
                    operation="recovery",
                    delay_seconds=round(delay, 3),
                    error=str(error),
                )
                await wait_for_shutdown(shutdown, delay)
                continue
            redis_retry_attempt = 0
            database_retry_attempt = 0
            if should_reconcile_deliveries:
                next_delivery_reconciliation = (
                    monotonic() + settings.delivery_reconciliation_interval_seconds
                )
            if (
                recovered
                or restored
                or reservations.requeued
                or reservations.removed
                or reservations.malformed
            ):
                await logger.awarning(
                    "worker_state_recovered",
                    expired_attempt_count=len(recovered),
                    job_ids=[str(job_id) for job_id in recovered],
                    reservations_requeued=reservations.requeued,
                    reservations_removed=reservations.removed,
                    malformed_reservations=reservations.malformed,
                    missing_deliveries_restored=len(restored),
                )
            else:
                await wait_for_shutdown(shutdown, settings.recovery_poll_interval_ms / 1000)
    finally:
        await resources.close()


def main() -> None:
    try:
        asyncio.run(run_process(run_recovery))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
