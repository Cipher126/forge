"""Read-only, database-side aggregation for large validation runs."""

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import text

from forge.config import get_settings
from forge.resources import RuntimeResources


async def report(key_id: UUID) -> dict[str, object]:
    resources = RuntimeResources.create(get_settings())
    try:
        async with resources.sessions() as session:
            rows = await session.execute(
                text("SELECT status, count(*) FROM jobs WHERE api_key_id=:key GROUP BY status"),
                {"key": key_id},
            )
            statuses = {status: count for status, count in rows}
            timings = {}
            expressions = {
                "queue_wait": (
                    "CASE WHEN started_at>=queued_at "
                    "THEN extract(epoch from started_at-queued_at) END"
                ),
                "execution": "extract(epoch from finished_at-started_at)",
                "end_to_end": "extract(epoch from finished_at-created_at)",
            }
            for name, expression in expressions.items():
                row = (
                    (
                        await session.execute(
                            text(f"""
                        SELECT count(duration) AS samples,
                          percentile_cont(0.5) WITHIN GROUP (ORDER BY duration) AS p50_seconds,
                          percentile_cont(0.95) WITHIN GROUP (ORDER BY duration) AS p95_seconds
                        FROM (SELECT {expression} AS duration FROM jobs
                              WHERE api_key_id=:key) durations
                    """),
                            {"key": key_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                timings[name] = dict(row)
            row = (
                (
                    await session.execute(
                        text("""
                SELECT count(*) AS samples,
                  percentile_cont(0.5) WITHIN GROUP
                    (ORDER BY (t.metadata->>'claim_seconds')::double precision) AS p50_seconds,
                  percentile_cont(0.95) WITHIN GROUP
                    (ORDER BY (t.metadata->>'claim_seconds')::double precision) AS p95_seconds
                FROM job_transitions t JOIN jobs j ON j.id=t.job_id
                WHERE j.api_key_id=:key AND t.reason='attempt_claimed'
                  AND t.metadata->>'claim_seconds' IS NOT NULL
            """),
                        {"key": key_id},
                    )
                )
                .mappings()
                .one()
            )
            timings["claim"] = dict(row)
            priorities = await session.execute(
                text("""
                SELECT priority, count(*) AS jobs,
                  percentile_cont(0.95) WITHIN GROUP
                    (ORDER BY extract(epoch from started_at-queued_at)) AS queue_wait_p95_seconds
                FROM jobs WHERE api_key_id=:key AND started_at>=queued_at GROUP BY priority
            """),
                {"key": key_id},
            )
            invalid_results = await session.scalar(
                text("""
                SELECT count(*) FROM jobs WHERE api_key_id=:key AND status='succeeded' AND (
                  (result IS NULL AND result_blob_key IS NULL) OR
                  (job_type='demo.generate_report'
                   AND payload IS NOT NULL AND result IS NOT NULL AND
                    (result->>'report_id' IS DISTINCT FROM payload->>'report_id' OR
                     result->>'format' IS DISTINCT FROM payload->>'format'))
                )
            """),
                {"key": key_id},
            )
            return {
                "jobs_by_status": statuses,
                "timings": timings,
                "total_jobs": sum(statuses.values()),
                "priorities": [dict(row) for row in priorities.mappings()],
                "invalid_report_results": int(invalid_results or 0),
            }
    finally:
        await resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key_id", type=UUID)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(report(args.key_id)), sort_keys=True))


if __name__ == "__main__":
    main()
