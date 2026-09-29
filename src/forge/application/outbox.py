import random
from datetime import timedelta
from uuid import uuid7

from redis.asyncio import Redis
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forge.errors.outbox import OutboxClaimLostError, OutboxPublishError
from forge.persistence.models import OutboxEvent
from forge.queue.names import queue_key
from forge.schemas.outbox import ClaimedOutboxEvent, QueueMessage


async def claim_outbox_events(
    session: AsyncSession,
    *,
    batch_size: int,
    lease_seconds: int,
) -> list[ClaimedOutboxEvent]:
    database_now = await session.scalar(select(func.clock_timestamp()))
    if database_now is None:
        raise RuntimeError("PostgreSQL did not return its current time")

    events = list(
        await session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.available_at <= database_now,
                or_(
                    OutboxEvent.lease_token.is_(None),
                    OutboxEvent.lease_expires_at <= database_now,
                ),
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    claimed: list[ClaimedOutboxEvent] = []
    for event in events:
        token = uuid7()
        event.lease_token = token
        event.lease_expires_at = database_now + timedelta(seconds=lease_seconds)
        event.dispatch_attempts += 1
        claimed.append(
            ClaimedOutboxEvent(
                id=event.id,
                job_id=event.job_id,
                event_type=event.event_type,
                payload=dict(event.payload),
                dispatch_attempt=event.dispatch_attempts,
                lease_token=token,
            )
        )
    await session.flush()
    return claimed


def queue_message(event: ClaimedOutboxEvent) -> QueueMessage:
    try:
        return QueueMessage.model_validate(
            {
                "event_id": event.id,
                "job_id": event.job_id,
                "job_type": event.payload["job_type"],
                "handler_version": event.payload["handler_version"],
                "priority": event.payload["priority"],
                "trace_id": event.payload.get("trace_id"),
                "parent_span_id": event.payload.get("parent_span_id"),
            }
        )
    except (KeyError, ValueError) as error:
        raise OutboxPublishError(f"outbox event {event.id} has an invalid payload") from error


async def publish_outbox_event(redis: Redis, event: ClaimedOutboxEvent) -> None:
    message = queue_message(event)
    try:
        await redis.rpush(
            queue_key(message.priority),
            message.model_dump_json(),
        )
    except Exception as error:
        raise OutboxPublishError(f"could not publish outbox event {event.id}") from error


async def mark_outbox_published(session: AsyncSession, event: ClaimedOutboxEvent) -> None:
    result = await session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event.id,
            OutboxEvent.lease_token == event.lease_token,
            OutboxEvent.published_at.is_(None),
        )
        .values(
            published_at=func.clock_timestamp(),
            lease_token=None,
            lease_expires_at=None,
            last_error=None,
        )
        .returning(OutboxEvent.id)
    )
    if result.scalar_one_or_none() is None:
        raise OutboxClaimLostError(f"lease for outbox event {event.id} is no longer valid")


def retry_delay_ms(attempt: int, *, base_ms: int, cap_ms: int) -> int:
    exponential = min(cap_ms, base_ms * (2 ** max(0, attempt - 1)))
    return random.randint(0, exponential)


async def release_outbox_event(
    session: AsyncSession,
    event: ClaimedOutboxEvent,
    *,
    error: Exception,
    retry_base_ms: int,
    retry_cap_ms: int,
) -> None:
    delay_ms = retry_delay_ms(
        event.dispatch_attempt,
        base_ms=retry_base_ms,
        cap_ms=retry_cap_ms,
    )
    result = await session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event.id,
            OutboxEvent.lease_token == event.lease_token,
            OutboxEvent.published_at.is_(None),
        )
        .values(
            available_at=func.clock_timestamp() + timedelta(milliseconds=delay_ms),
            lease_token=None,
            lease_expires_at=None,
            last_error=str(error)[:4096],
        )
        .returning(OutboxEvent.id)
    )
    if result.scalar_one_or_none() is None:
        raise OutboxClaimLostError(f"lease for outbox event {event.id} is no longer valid")
