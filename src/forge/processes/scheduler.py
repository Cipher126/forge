import asyncio

import structlog
from sqlalchemy.exc import DBAPIError

from forge.application.scheduling import promote_due_jobs
from forge.config import get_settings
from forge.observability.logging import configure_logging
from forge.processes.lifecycle import retry_delay_seconds, run_process, wait_for_shutdown
from forge.resources import RuntimeResources

logger = structlog.get_logger()


async def run_scheduler(shutdown: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service="scheduler")
    resources = RuntimeResources.create(settings)
    database_retry_attempt = 0
    try:
        while not shutdown.is_set():
            try:
                async with resources.sessions.begin() as session:
                    promoted = await promote_due_jobs(
                        session,
                        batch_size=settings.scheduler_batch_size,
                    )
            except (DBAPIError, OSError) as error:
                delay = retry_delay_seconds(
                    database_retry_attempt,
                    base_ms=settings.postgres_retry_base_ms,
                    cap_ms=settings.postgres_retry_cap_ms,
                )
                database_retry_attempt += 1
                await logger.awarning(
                    "database_reconnect_wait",
                    operation="promote_scheduled_jobs",
                    delay_seconds=round(delay, 3),
                    error=str(error),
                )
                await wait_for_shutdown(shutdown, delay)
                continue
            database_retry_attempt = 0
            if promoted:
                await logger.ainfo(
                    "scheduled_jobs_promoted",
                    count=len(promoted),
                    job_ids=[str(job_id) for job_id in promoted],
                )
            else:
                await wait_for_shutdown(shutdown, settings.scheduler_poll_interval_ms / 1000)
    finally:
        await resources.close()


def main() -> None:
    try:
        asyncio.run(run_process(run_scheduler))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
