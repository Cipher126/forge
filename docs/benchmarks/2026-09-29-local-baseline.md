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

## Follow-up isolation — 2026-09-30

Matched API-only runs at 10 jobs/s separated durable acceptance from dispatch
and execution. One API worker recorded a 918.5 ms p95. Two workers recorded a
142.1 ms p95 and passed the 500 ms target. Four workers regressed to an 825.2 ms
p95, which is consistent with oversubscribing this four-core development host.
Every completed request in these runs returned HTTP 202 and k6 dropped no
iterations.

| Topology | API workers | Accepted | Median | p90 | p95 | Maximum | Target |
|---|---:|---:|---:|---:|---:|---:|:---:|
| API only | 1 | 200/200 | 50.0 ms | 320.3 ms | 918.5 ms | 1,259.3 ms | Fail |
| API only | 2 | 201/201 | 47.3 ms | 95.2 ms | 142.1 ms | 443.0 ms | Pass |
| API only | 4 | 201/201 | 47.8 ms | 477.6 ms | 825.2 ms | 1,592.4 ms | Fail |
| Full backend | 2 | 201/201 | 60.9 ms | 505.4 ms | 681.2 ms | 1,010.8 ms | Fail |

The full-backend run sampled PostgreSQL 170 times. It observed a peak of 30
connections and seven active sessions. The meaningful contention signal was 63
samples waiting on an advisory lock; WAL-related waits appeared only 27 times.
Forge currently takes one transaction-scoped global advisory lock in
`enforce_backlog_limit` for every submission so that the configured backlog cap
is exact under concurrency. Dispatch and execution increase the time that this
serialized submission path waits for database resources.

The evidence does not justify another query index: the bottleneck is the global
admission lock, and PostgreSQL was not continuously saturated with active work.
Two API workers are the best local setting, but they do not make the full
topology meet the latency target. Replacing the lock requires an explicit design
choice: retain an exact cap with preallocated admission slots, or accept a
bounded/eventually reconciled counter. That reliability tradeoff should be made
before changing production behavior.

One instrumented API-only repeat was excluded from the table because ten HTTP
requests did not complete before k6's 30-second graceful-stop deadline. The
runner reported the interruption, and treating that partial run as a successful
capacity measurement would be misleading.
