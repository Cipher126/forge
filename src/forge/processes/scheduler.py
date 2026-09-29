import asyncio

import structlog

from forge.application.scheduling import promote_due_jobs
from forge.config import get_settings
from forge.observability.logging import configure_logging
from forge.processes.lifecycle import run_process, wait_for_shutdown
from forge.resources import RuntimeResources

logger = structlog.get_logger()


async def run_scheduler(shutdown: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service="scheduler")
    resources = RuntimeResources.create(settings)
    try:
        while not shutdown.is_set():
            async with resources.sessions.begin() as session:
                promoted = await promote_due_jobs(
                    session,
                    batch_size=settings.scheduler_batch_size,
                )
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
