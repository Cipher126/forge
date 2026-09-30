from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forge.errors.capacity import SystemOverloadedError
from forge.persistence.models import AdmissionSlot


async def claim_admission_slot(
    session: AsyncSession,
    *,
    job_id: UUID,
    limit: int,
    retry_after_seconds: int,
) -> None:
    free_slot = (
        select(AdmissionSlot.slot_number)
        .where(AdmissionSlot.slot_number <= limit, AdmissionSlot.job_id.is_(None))
        .order_by(AdmissionSlot.slot_number)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    claimed = await session.execute(
        update(AdmissionSlot)
        .where(AdmissionSlot.slot_number == free_slot)
        .values(job_id=job_id)
        .returning(AdmissionSlot.slot_number)
    )
    if claimed.scalar_one_or_none() is None:
        raise SystemOverloadedError(
            backlog=limit,
            limit=limit,
            retry_after_seconds=retry_after_seconds,
        )


async def release_admission_slot(session: AsyncSession, job_id: UUID) -> None:
    await session.execute(
        update(AdmissionSlot).where(AdmissionSlot.job_id == job_id).values(job_id=None)
    )
