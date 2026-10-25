"""Measure actual database authentication and Redis rate checks inside the API container.

Reads its temporary credential from stdin; never writes it to a report.
"""

import asyncio
import json
import sys
from time import perf_counter

from forge.config import get_settings
from forge.resources import RuntimeResources
from forge.security.api_keys import authenticate_api_key
from forge.security.rate_limits import check_rate_limit


def distribution(values: list[float]) -> dict[str, float | int]:
    values = sorted(values)
    return {
        "samples": len(values),
        "p50_ms": values[int((len(values) - 1) * 0.5)] * 1000,
        "p95_ms": values[int((len(values) - 1) * 0.95)] * 1000,
        "mean_ms": sum(values) / len(values) * 1000,
    }


async def benchmark(credential: str, samples: int) -> dict[str, object]:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    authentication, rate_check, total = [], [], []
    try:
        for index in range(samples + 10):
            # A new session per request prevents ORM identity-map reuse from hiding cost.
            async with resources.sessions() as session:
                started = perf_counter()
                record = await authenticate_api_key(
                    session,
                    credential=credential,
                    pepper=settings.api_key_pepper.get_secret_value(),
                )
                authenticated = perf_counter()
                if record is None:
                    raise RuntimeError("temporary benchmark credential is invalid")
                decision = await check_rate_limit(
                    resources.redis,
                    public_id=record.public_id,
                    limit=record.rate_limit_requests,
                    window_seconds=record.rate_limit_window_s,
                )
                limited = perf_counter()
                if not decision.allowed:
                    raise RuntimeError("benchmark credential was rate limited")
                if index >= 10:
                    authentication.append(authenticated - started)
                    rate_check.append(limited - authenticated)
                    total.append(limited - started)
        return {
            "method": "serial, idle, 10 warmups, pooled connection, fresh session per request",
            "authentication": distribution(authentication),
            "rate_limit": distribution(rate_check),
            "combined": distribution(total),
            "excludes": "HTTP handling, submission, session close/rollback, concurrent contention",
        }
    finally:
        await resources.close()


def main() -> None:
    request = json.load(sys.stdin)
    samples = int(request.get("samples", 500))
    if not 1 <= samples <= 10000:
        raise ValueError("samples must be between 1 and 10000")
    print(json.dumps(asyncio.run(benchmark(request["credential"], samples)), sort_keys=True))


if __name__ == "__main__":
    main()
