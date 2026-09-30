# Load testing Forge

The initial k6 scenario measures sustained job-submission capacity at a fixed
arrival rate. It records HTTP latency and errors separately from the number of
jobs durably accepted. Run workers, the dispatcher, and the API before starting
the test.

Use a dedicated `jobs:write` API key with a rate limit above the planned k6
arrival rate. This prevents per-client throttling from being mistaken for
platform saturation.

```powershell
$env:API_KEY = "forge_live_..."
$env:BASE_URL = "http://127.0.0.1:8000"
$env:RATE = "20"
$env:DURATION = "1m"
k6 run load/k6/submit-jobs.js
```

The run writes a normalized report to `load/results/k6-summary.json`. Override
that location with `SUMMARY_PATH`. The report contains workload parameters and
only the metrics used for Forge's benchmark claims; it never includes the API
credential.

Run the controlled resilience suite separately:

```powershell
uv run python scripts/run_failure_suite.py
```

It selects tests marked `failure` and writes JUnit plus a compact JSON summary
under `tests/reports/`.

Record the machine specification, Forge image or commit, process counts,
worker concurrency, pool configuration, payload, arrival rate, duration, and
k6 output for every published result. Increase `RATE` between runs rather than
during a run so each result has one interpretable workload.

The default thresholds require more than 99% successful checks, less than 1%
HTTP failures, p95 submission latency below 500 ms, and no dropped iterations.
An intentional `429` or `503` still fails the submission check; report those
responses separately because they show client throttling or admission
backpressure rather than silent data loss.

Post-run verification should compare `forge_jobs_accepted` with PostgreSQL job
counts and inspect `/v1/admin/operations/performance` for throughput, queue wait,
execution duration, retries, and scheduling lag.

When k6 is unavailable locally but Docker Desktop is running, the repository
runner creates a temporary high-rate key, starts the Forge processes, executes
the digest-pinned k6 1.8.1 container, waits for accepted work, and removes its
database records:

```powershell
uv run python scripts/run_local_benchmark.py --rate 10 --duration 20s --port 18080
```

The credential is passed through the child-process environment and is never
printed or written to the result file.

Use `--processes api-only` to measure durable acceptance without dispatcher and
worker traffic. `--api-workers` controls the Uvicorn process count. The runner
also samples `pg_stat_activity` during the load window and adds peak connection,
peak active-session, and observed wait-event counts to the local JSON summary.

```powershell
uv run python scripts/run_local_benchmark.py --processes api-only --api-workers 2 --rate 10 --duration 20s --port 18082 --summary-name api-only-2w-10rps.json
uv run python scripts/run_local_benchmark.py --processes all --api-workers 2 --rate 10 --duration 20s --port 18082 --summary-name full-2w-10rps.json
```
