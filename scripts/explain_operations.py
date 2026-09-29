import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text

from forge.config import get_settings
from forge.persistence.database import create_database_engine

QUERIES = {
    "recent_submissions": "SELECT id FROM jobs WHERE created_at >= now() - interval '1 hour'",
    "due_scheduled_jobs": (
        "SELECT id FROM jobs WHERE status IN ('scheduled', 'retry_scheduled') "
        "AND run_at <= now() ORDER BY run_at LIMIT 100"
    ),
    "terminal_retention": (
        "SELECT id FROM jobs WHERE status IN "
        "('succeeded', 'failed', 'dead_lettered', 'cancelled') "
        "AND finished_at <= now() - interval '30 days' ORDER BY finished_at, id LIMIT 100"
    ),
    "retry_metrics": (
        "SELECT count(*) FROM job_transitions WHERE to_status = 'retry_scheduled' "
        "AND created_at >= now() - interval '1 hour'"
    ),
    "unpublished_outbox": "SELECT count(*) FROM outbox_events WHERE published_at IS NULL",
}


async def explain(destination: Path) -> None:
    engine = create_database_engine(get_settings().postgres_url)
    try:
        plans: dict[str, object] = {}
        async with engine.connect() as connection:
            for name, query in QUERIES.items():
                result = await connection.scalar(
                    text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}")
                )
                plans[name] = result
        await asyncio.to_thread(
            write_report,
            destination,
            json.dumps(plans, indent=2, sort_keys=True) + "\n",
        )
        print(f"wrote {destination}")
    finally:
        await engine.dispose()


def write_report(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/reports/query-plans.json"),
    )
    arguments = parser.parse_args()
    asyncio.run(explain(arguments.output))


if __name__ == "__main__":
    main()
