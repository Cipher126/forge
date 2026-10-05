from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import aliased

from forge.persistence.models import IdempotencyKey, Job, JobAttempt, JobTransition
from forge.persistence.object_store import ObjectStore

SUCCESSFUL_TERMINAL_STATUSES = ("succeeded", "cancelled")
FAILED_TERMINAL_STATUSES = ("failed", "dead_lettered")


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, UUID)):
        return str(value) if isinstance(value, UUID) else value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _serialize_model(model: Any) -> dict[str, Any]:
    return {
        attribute.key: _json_value(getattr(model, attribute.key))
        for attribute in inspect(type(model)).column_attrs
    }


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    if value is None:
        raise RuntimeError("PostgreSQL did not return its current time")
    return cast(datetime, value)


async def delete_expired_idempotency_keys(
    session: AsyncSession, *, batch_size: int
) -> int:
    database_now = await _database_now(session)
    ids = list(
        await session.scalars(
            select(IdempotencyKey.id)
            .where(IdempotencyKey.expires_at <= database_now)
            .order_by(IdempotencyKey.expires_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    if ids:
        await session.execute(delete(IdempotencyKey).where(IdempotencyKey.id.in_(ids)))
    return len(ids)


async def archive_terminal_jobs(
    session: AsyncSession,
    object_store: ObjectStore,
    *,
    successful_retention_days: int,
    failed_retention_days: int,
    batch_size: int,
) -> list[UUID]:
    database_now = await _database_now(session)
    successful_cutoff = database_now - timedelta(days=successful_retention_days)
    failed_cutoff = database_now - timedelta(days=failed_retention_days)
    replay = aliased(Job)
    jobs = list(
        await session.scalars(
            select(Job)
            .where(
                or_(
                    and_(
                        Job.status.in_(SUCCESSFUL_TERMINAL_STATUSES),
                        Job.finished_at <= successful_cutoff,
                    ),
                    and_(
                        Job.status.in_(FAILED_TERMINAL_STATUSES),
                        Job.finished_at <= failed_cutoff,
                    ),
                ),
                ~select(replay.id)
                .where(replay.replayed_from_job_id == Job.id)
                .exists(),
            )
            .order_by(Job.finished_at, Job.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    archived: list[UUID] = []
    for job in jobs:
        attempts = list(
            await session.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_id == job.id)
                .order_by(JobAttempt.attempt_number)
            )
        )
        transitions = list(
            await session.scalars(
                select(JobTransition)
                .where(JobTransition.job_id == job.id)
                .order_by(JobTransition.id)
            )
        )
        archive = {
            "schema_version": 1,
            "archived_at": database_now.isoformat(),
            "job": _serialize_model(job),
            "attempts": [_serialize_model(attempt) for attempt in attempts],
            "transitions": [_serialize_model(transition) for transition in transitions],
        }
        await object_store.put_json_gzip_at(f"archives/jobs/{job.id}.json.gz", archive)
        await session.delete(job)
        archived.append(job.id)
    await session.flush()
    return archived


async def delete_orphaned_job_objects(
    session: AsyncSession,
    object_store: ObjectStore,
    *,
    grace_hours: int,
) -> int:
    database_now = await _database_now(session)
    cutoff = database_now - timedelta(hours=grace_hours)
    payload_keys = set(
        await session.scalars(select(Job.payload_blob_key).where(Job.payload_blob_key.is_not(None)))
    )
    result_keys = set(
        await session.scalars(select(Job.result_blob_key).where(Job.result_blob_key.is_not(None)))
    )
    referenced = payload_keys | result_keys
    objects = [
        *await object_store.list_objects("payloads/"),
        *await object_store.list_objects("results/"),
    ]
    orphaned = [
        item for item in objects if item.key not in referenced and item.last_modified <= cutoff
    ]
    for item in orphaned:
        await object_store.delete(item.key)
    return len(orphaned)
