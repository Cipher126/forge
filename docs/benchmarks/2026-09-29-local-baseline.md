# Local submission baseline — 2026-09-29

This experiment establishes an initial latency boundary for durable job
submission. It is a short single-machine development measurement, not a
production capacity claim.

## Environment

- Commit under test: `955b14c` plus the benchmark-runner working change
- Windows 11 Home 10.0.26200
- Intel Core i7-1185G7, 4 cores / 8 logical processors
- 15.7 GB RAM
- Python 3.14.2
- PostgreSQL 18 and Redis 8.10 in local Docker Desktop
- k6 1.8.1 image digest:
  `sha256:23f2279054c3e01535455c4d92914a625df12aeb631ea0e3ea7a43f96bcbc843`
- One API, dispatcher, scheduler, recovery process, and worker
- Worker concurrency: 4
- Inline `demo.generate_report` JSON payload
- Constant arrival rate for 20 seconds

## Results

| Arrival rate | Accepted | HTTP failures | Dropped | Median | p90 | p95 | Maximum | 500 ms target |
|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 5 jobs/s | 101/101 | 0 | 0 | 55.0 ms | 127.0 ms | 386.8 ms | 924.7 ms | Pass |
| 10 jobs/s | 201/201 | 0 | 0 | 64.5 ms | 638.9 ms | 1,061.5 ms | 1,457.5 ms | Fail |

Both runs preserved the primary durability behavior: every request was accepted
with HTTP 202 and no iteration was dropped. The 10 jobs/s run crossed the
provisional p95 latency threshold, with 23 API requests taking more than 500 ms.

The sharp rise between 5 and 10 jobs/s indicates saturation or periodic
contention in the single-machine process/database path. These runs do not prove
which component is responsible. Adding speculative indexes would not be
justified because submission uses indexed API-key lookup and direct inserts;
the next experiments should isolate API-only acceptance, vary API process count,
and measure PostgreSQL wait events before changing the schema.

## Reproduction

```powershell
uv run python scripts/run_local_benchmark.py --rate 5 --duration 20s --port 18080 --summary-name baseline-5rps.json
uv run python scripts/run_local_benchmark.py --rate 10 --duration 20s --port 18080 --summary-name baseline-10rps.json
```

Raw JSON summaries remain locally under `load/results/` and are ignored by Git.
