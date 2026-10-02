import asyncio
import json

from sqlalchemy import text

from forge.config import get_settings
from forge.resources import RuntimeResources

CHECKS = {
    "outstanding_jobs_without_slots": """
        SELECT count(*)
        FROM jobs AS jobs
        LEFT JOIN admission_slots AS slots ON slots.job_id = jobs.id
        WHERE jobs.status IN (
            'scheduled', 'queued', 'running', 'retry_scheduled', 'cancellation_requested'
        ) AND slots.job_id IS NULL
    """,
    "terminal_jobs_with_slots": """
        SELECT count(*)
        FROM admission_slots AS slots
        JOIN jobs AS jobs ON jobs.id = slots.job_id
        WHERE jobs.status IN ('succeeded', 'failed', 'dead_lettered', 'cancelled')
    """,
    "active_attempt_mismatches": """
        SELECT count(*)
        FROM jobs AS jobs
        LEFT JOIN job_attempts AS attempts ON attempts.id = jobs.active_attempt_id
        WHERE
            (jobs.status IN ('running', 'cancellation_requested') AND (
                attempts.id IS NULL OR attempts.status != 'running'
            )) OR
            (jobs.status NOT IN ('running', 'cancellation_requested') AND attempts.id IS NOT NULL)
    """,
    "duplicate_attempt_numbers": """
        SELECT count(*) FROM (
            SELECT job_id, attempt_number
            FROM job_attempts
            GROUP BY job_id, attempt_number
            HAVING count(*) > 1
        ) AS duplicates
    """,
    "expired_outbox_leases": """
        SELECT count(*)
        FROM outbox_events
        WHERE published_at IS NULL
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at < clock_timestamp()
    """,
}


async def main() -> None:
    resources = RuntimeResources.create(get_settings())
    try:
        async with resources.database.connect() as connection:
            results = {
                name: int(await connection.scalar(text(statement)) or 0)
                for name, statement in CHECKS.items()
            }
        print(json.dumps(results, indent=2, sort_keys=True))
        failed = [name for name, count in results.items() if count]
        if failed:
            raise SystemExit(f"integrity checks failed: {', '.join(failed)}")
    finally:
        await resources.close()


if __name__ == "__main__":
    asyncio.run(main())
