import asyncio
import json
import os
import socket
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import AbstractContextManager
from typing import Any
from uuid import uuid7

import structlog
from opentelemetry.trace import Span, SpanKind

from forge.application.execution import (
    claim_job_attempt,
    complete_attempt_failure,
    complete_attempt_success,
    execute_handler,
    parse_queue_message,
    renew_attempt_lease,
)
from forge.config import Settings, get_settings
from forge.domain.handlers import ExecutionMode
from forge.errors.execution import (
    AttemptOwnershipLostError,
    HandlerCancelledError,
    InvalidQueueMessageError,
    JobTypePermitLostError,
)
from forge.observability.logging import configure_logging
from forge.observability.tracing import configure_tracing, start_span
from forge.processes.lifecycle import run_process, wait_for_shutdown
from forge.queue.client import (
    acknowledge_message,
    acquire_job_type_permit,
    clear_worker_heartbeat,
    refresh_worker_heartbeat,
    release_job_type_permit,
    renew_job_type_permit,
    requeue_message,
    reserve_next_message,
)
from forge.resources import RuntimeResources
from forge.schemas.execution import (
    ClaimedJob,
    JobTypePermit,
    LeaseRenewalOutcome,
    ReservedQueueMessage,
)

logger = structlog.get_logger()


def create_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid7().hex[-8:]}"


async def maintain_worker_heartbeat(
    resources: RuntimeResources,
    settings: Settings,
    worker_id: str,
) -> None:
    while True:
        await refresh_worker_heartbeat(
            resources.redis,
            worker_id,
            settings.worker_heartbeat_ttl_seconds,
        )
        await asyncio.sleep(settings.worker_heartbeat_interval_seconds)


async def renew_while_running(
    resources: RuntimeResources,
    settings: Settings,
    claimed: ClaimedJob,
    stopped: asyncio.Event,
) -> LeaseRenewalOutcome:
    while True:
        try:
            await asyncio.wait_for(
                stopped.wait(),
                timeout=settings.worker_lease_renew_interval_seconds,
            )
            return LeaseRenewalOutcome.RENEWED
        except TimeoutError:
            pass
        async with resources.sessions.begin() as session:
            outcome = await renew_attempt_lease(
                session,
                claimed=claimed,
                lease_seconds=settings.worker_attempt_lease_seconds,
            )
        if outcome is not LeaseRenewalOutcome.RENEWED:
            if outcome is LeaseRenewalOutcome.CANCELLATION_REQUESTED:
                return outcome
            raise AttemptOwnershipLostError(
                f"ownership of attempt {claimed.attempt_id} was lost during renewal"
            )


async def renew_permit_while_running(
    resources: RuntimeResources,
    settings: Settings,
    permit: JobTypePermit,
    stopped: asyncio.Event,
) -> None:
    while True:
        try:
            await asyncio.wait_for(
                stopped.wait(),
                timeout=settings.job_type_permit_renew_interval_seconds,
            )
            return
        except TimeoutError:
            pass
        renewed = await renew_job_type_permit(
            resources.redis,
            permit=permit,
            lease_seconds=settings.job_type_permit_lease_seconds,
        )
        if not renewed:
            raise JobTypePermitLostError(
                f"capacity permit for job type {permit.job_type!r} was lost"
            )


async def execute_claimed_job(
    resources: RuntimeResources,
    settings: Settings,
    claimed: ClaimedJob,
    *,
    permit: JobTypePermit | None,
    thread_pool: Executor,
    process_pool: Executor,
) -> None:
    definition = resources.handlers.resolve(claimed.job_type, claimed.handler_version)
    if claimed.payload is not None:
        payload = claimed.payload
    elif claimed.payload_blob_key is not None:
        payload = await resources.object_store.get_json(claimed.payload_blob_key)
    else:
        raise RuntimeError(f"claimed job {claimed.job_id} has no payload location")
    executor: Executor | None = None
    if definition.execution_mode is ExecutionMode.THREAD:
        executor = thread_pool
    elif definition.execution_mode is ExecutionMode.PROCESS:
        executor = process_pool

    stopped = asyncio.Event()
    execution = asyncio.create_task(execute_handler(definition, payload, executor=executor))
    renewal = asyncio.create_task(renew_while_running(resources, settings, claimed, stopped))
    permit_renewal = (
        asyncio.create_task(renew_permit_while_running(resources, settings, permit, stopped))
        if permit is not None
        else None
    )
    try:
        watched: set[asyncio.Future[Any]] = {execution, renewal}
        if permit_renewal is not None:
            watched.add(permit_renewal)
        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        if permit_renewal is not None and permit_renewal in done:
            await permit_renewal
        if renewal in done:
            renewal_outcome = await renewal
            if renewal_outcome is LeaseRenewalOutcome.CANCELLATION_REQUESTED:
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                raise HandlerCancelledError("job cancellation was requested")
        result = await execution
        stopped.set()
        renewal_outcome = await renewal
        if permit_renewal is not None:
            await permit_renewal
        if renewal_outcome is LeaseRenewalOutcome.CANCELLATION_REQUESTED:
            raise HandlerCancelledError("job cancellation was requested")
        encoded_result = json.dumps(
            result,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        inline_result: dict[str, object] | None = result
        result_blob_key: str | None = None
        if len(encoded_result) > settings.max_inline_payload_bytes:
            result_blob_key = await resources.object_store.put_json("results", result)
            inline_result = None
        async with resources.sessions.begin() as session:
            await complete_attempt_success(
                session,
                claimed=claimed,
                result=inline_result,
                result_blob_key=result_blob_key,
            )
    except HandlerCancelledError as error:
        stopped.set()
        try:
            async with resources.sessions.begin() as session:
                await complete_attempt_failure(
                    session,
                    claimed=claimed,
                    error=error,
                    retry_base_ms=settings.worker_retry_base_ms,
                    retry_cap_ms=settings.worker_retry_cap_ms,
                )
            await logger.ainfo("job_attempt_cancelled", job_id=str(claimed.job_id))
        except AttemptOwnershipLostError:
            await logger.awarning("job_cancellation_lost_ownership", job_id=str(claimed.job_id))
    except AttemptOwnershipLostError:
        stopped.set()
        execution.cancel()
        await asyncio.gather(execution, renewal, return_exceptions=True)
        await logger.awarning(
            "job_result_discarded_after_ownership_loss",
            job_id=str(claimed.job_id),
            attempt_id=str(claimed.attempt_id),
        )
    except Exception as error:
        stopped.set()
        if not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        await asyncio.gather(renewal, return_exceptions=True)
        try:
            async with resources.sessions.begin() as session:
                next_status = await complete_attempt_failure(
                    session,
                    claimed=claimed,
                    error=error,
                    retry_base_ms=settings.worker_retry_base_ms,
                    retry_cap_ms=settings.worker_retry_cap_ms,
                )
            await logger.awarning(
                "job_attempt_failed",
                job_id=str(claimed.job_id),
                next_status=next_status.value,
            )
        except AttemptOwnershipLostError:
            await logger.awarning("job_attempt_lost_ownership", job_id=str(claimed.job_id))
    else:
        await logger.ainfo(
            "job_attempt_succeeded",
            job_id=str(claimed.job_id),
            attempt_id=str(claimed.attempt_id),
        )
    finally:
        stopped.set()
        background: list[asyncio.Future[Any]] = [renewal]
        if permit_renewal is not None:
            background.append(permit_renewal)
        await asyncio.gather(*background, return_exceptions=True)


async def handle_reservation(
    resources: RuntimeResources,
    settings: Settings,
    reservation: ReservedQueueMessage,
    worker_id: str,
    thread_pool: Executor,
    process_pool: Executor,
) -> None:
    try:
        message = parse_queue_message(reservation)
    except InvalidQueueMessageError:
        await acknowledge_message(resources.redis, reservation)
        await logger.aerror("invalid_queue_message", queue=reservation.source_queue)
        return

    span_manager: AbstractContextManager[Span] | None = None
    trace_bound = message.trace_id is not None
    if message.trace_id is not None:
        span_manager, span_id = start_span(
            "forge job execute",
            trace_id=message.trace_id,
            parent_span_id=message.parent_span_id,
            kind=SpanKind.CONSUMER,
        )
        structlog.contextvars.bind_contextvars(trace_id=message.trace_id, span_id=span_id)

    permit: JobTypePermit | None = None
    limit = settings.job_type_concurrency_limits.get(message.job_type)
    if limit is not None:
        permit = JobTypePermit(job_type=message.job_type, token=uuid7())
        acquired = await acquire_job_type_permit(
            resources.redis,
            permit=permit,
            limit=limit,
            lease_seconds=settings.job_type_permit_lease_seconds,
        )
        if not acquired:
            await requeue_message(resources.redis, reservation)
            await asyncio.sleep(settings.worker_poll_interval_ms / 1000)
            return

    try:
        async with resources.sessions.begin() as session:
            claimed = await claim_job_attempt(
                session,
                message=message,
                worker_id=worker_id,
                lease_seconds=settings.worker_attempt_lease_seconds,
            )
        await acknowledge_message(resources.redis, reservation)
        if claimed is None:
            await logger.ainfo("duplicate_or_stale_queue_message", job_id=str(message.job_id))
            return
        await execute_claimed_job(
            resources,
            settings,
            claimed,
            permit=permit,
            thread_pool=thread_pool,
            process_pool=process_pool,
        )
    finally:
        if permit is not None:
            await release_job_type_permit(resources.redis, permit)
        if trace_bound:
            structlog.contextvars.unbind_contextvars("trace_id", "span_id")
        if span_manager is not None:
            span_manager.__exit__(None, None, None)


async def run_worker(shutdown: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service="worker")
    tracer_provider = configure_tracing(
        service="worker",
        enabled=settings.otlp_tracing_enabled,
        endpoint=settings.otlp_traces_endpoint,
        sample_ratio=settings.trace_sample_ratio,
    )
    resources = RuntimeResources.create(settings)
    worker_id = create_worker_id()
    thread_pool = ThreadPoolExecutor(max_workers=settings.worker_concurrency)
    process_pool = ProcessPoolExecutor(max_workers=settings.worker_concurrency)
    tasks: set[asyncio.Task[None]] = set()
    cycle_position = 0
    await refresh_worker_heartbeat(
        resources.redis,
        worker_id,
        settings.worker_heartbeat_ttl_seconds,
    )
    heartbeat = asyncio.create_task(maintain_worker_heartbeat(resources, settings, worker_id))
    try:
        while not shutdown.is_set():
            if heartbeat.done():
                await heartbeat
            finished = {task for task in tasks if task.done()}
            if finished:
                outcomes = await asyncio.gather(*finished, return_exceptions=True)
                for outcome in outcomes:
                    if isinstance(outcome, BaseException):
                        await logger.aerror("worker_task_failed", error=str(outcome))
                tasks.difference_update(finished)
            if len(tasks) >= settings.worker_concurrency:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                continue
            reservation, cycle_position = await reserve_next_message(
                resources.redis,
                worker_id=worker_id,
                cycle_position=cycle_position,
            )
            if reservation is None:
                await wait_for_shutdown(shutdown, settings.worker_poll_interval_ms / 1000)
                continue
            tasks.add(
                asyncio.create_task(
                    handle_reservation(
                        resources,
                        settings,
                        reservation,
                        worker_id,
                        thread_pool,
                        process_pool,
                    )
                )
            )
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await clear_worker_heartbeat(resources.redis, worker_id)
        thread_pool.shutdown(wait=True, cancel_futures=True)
        process_pool.shutdown(wait=True, cancel_futures=True)
        await resources.close()
        if tracer_provider is not None:
            tracer_provider.shutdown()


def main() -> None:
    try:
        asyncio.run(run_process(run_worker))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
