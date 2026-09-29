import asyncio

import structlog

from forge.application.outbox import (
    claim_outbox_events,
    mark_outbox_published,
    publish_outbox_event,
    release_outbox_event,
)
from forge.config import Settings, get_settings
from forge.errors.outbox import OutboxClaimLostError, OutboxPublishError
from forge.observability.logging import configure_logging
from forge.processes.lifecycle import run_process, wait_for_shutdown
from forge.resources import RuntimeResources

logger = structlog.get_logger()


async def dispatch_once(resources: RuntimeResources, settings: Settings) -> int:
    async with resources.sessions.begin() as session:
        events = await claim_outbox_events(
            session,
            batch_size=settings.dispatcher_batch_size,
            lease_seconds=settings.dispatcher_lease_seconds,
        )

    for event in events:
        try:
            await publish_outbox_event(resources.redis, event)
        except OutboxPublishError as error:
            try:
                async with resources.sessions.begin() as session:
                    await release_outbox_event(
                        session,
                        event,
                        error=error,
                        retry_base_ms=settings.dispatcher_retry_base_ms,
                        retry_cap_ms=settings.dispatcher_retry_cap_ms,
                    )
            except OutboxClaimLostError:
                await logger.awarning("outbox_release_lost_lease", event_id=str(event.id))
            else:
                await logger.awarning(
                    "outbox_publish_failed",
                    event_id=str(event.id),
                    dispatch_attempt=event.dispatch_attempt,
                )
            continue

        try:
            async with resources.sessions.begin() as session:
                await mark_outbox_published(session, event)
        except OutboxClaimLostError:
            await logger.awarning("outbox_finalize_lost_lease", event_id=str(event.id))
        else:
            await logger.ainfo(
                "outbox_event_published",
                event_id=str(event.id),
                job_id=str(event.job_id),
                dispatch_attempt=event.dispatch_attempt,
            )
    return len(events)


async def run_dispatcher(shutdown: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service="dispatcher")
    resources = RuntimeResources.create(settings)
    try:
        while not shutdown.is_set():
            dispatched = await dispatch_once(resources, settings)
            if dispatched == 0:
                await wait_for_shutdown(shutdown, settings.dispatcher_poll_interval_ms / 1000)
    finally:
        await resources.close()


def main() -> None:
    try:
        asyncio.run(run_process(run_dispatcher))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
