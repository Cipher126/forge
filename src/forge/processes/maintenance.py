import asyncio

import structlog

from forge.application.maintenance import (
    archive_terminal_jobs,
    delete_expired_idempotency_keys,
    delete_orphaned_job_objects,
)
from forge.config import get_settings
from forge.observability.logging import configure_logging
from forge.processes.lifecycle import run_process, wait_for_shutdown
from forge.resources import RuntimeResources

logger = structlog.get_logger()


async def run_maintenance(shutdown: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service="maintenance")
    resources = RuntimeResources.create(settings)
    try:
        while not shutdown.is_set():
            async with resources.sessions.begin() as session:
                expired_keys = await delete_expired_idempotency_keys(
                    session, batch_size=settings.maintenance_batch_size
                )
                archived_jobs = await archive_terminal_jobs(
                    session,
                    resources.object_store,
                    successful_retention_days=settings.successful_job_retention_days,
                    failed_retention_days=settings.failed_job_retention_days,
                    batch_size=settings.maintenance_batch_size,
                )
            async with resources.sessions() as session:
                orphaned_objects = await delete_orphaned_job_objects(
                    session,
                    resources.object_store,
                    grace_hours=settings.orphan_object_grace_hours,
                )
            await logger.ainfo(
                "maintenance_completed",
                expired_idempotency_keys=expired_keys,
                archived_jobs=len(archived_jobs),
                orphaned_objects_deleted=orphaned_objects,
            )
            await wait_for_shutdown(shutdown, settings.maintenance_interval_seconds)
    finally:
        await resources.close()


def main() -> None:
    try:
        asyncio.run(run_process(run_maintenance))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
