# Backend acceptance

Run the complete backend checks from the repository root:

```powershell
uv run python scripts/validate_backend.py
```

This builds the Linux validation image, starts local dependencies, applies the
development migrations, runs every backend test, checks deployment acceptance,
interrupts PostgreSQL and object storage, kills an owning worker, probes
capacity and overload, and submits 100,000 jobs through the real HTTP API.
Accepted jobs must finish, temporary test credentials must be revoked, and
the database integrity checks must report zero violations.

Allow time for the endurance run. The command does not wait for a commit
schedule and never makes Git commits. Do not run another fault-injection or
load command against the validation stack at the same time.

With an already running stack built from the current source:

```powershell
uv run python scripts/validate_backend.py --skip-start
```

For a shorter harness check, explicitly reduce the workload:

```powershell
uv run python scripts/validate_backend.py --skip-start --endurance-jobs 1000
```

The report records that this is **not** the full 100,000-job endurance test.
Its full-endurance flag requires the 100,000-job stage to succeed; requesting
that workload alone is not enough. An interrupted command is not a passing result.

To check the worker/runtime after a small change without overwriting the full
benchmark, use a separate report name:

```powershell
uv run python scripts/validate_capacity.py --skip-capacity `
  --endurance-jobs 5000 --report-name final-regression
```

The report records the smaller workload. It is regression evidence, not another
100,000-job claim.

## Reports

- `tests/reports/backend-acceptance.json`: each stage and its exit status.
- `tests/reports/deployment-validation.json`: startup, load, drain and health.
- `tests/reports/dependency-outages.json`: response deadlines and recovery.
- `tests/reports/worker-kill-validation.json`: abandoned and recovered attempts.
- `load/results/backend-capacity.json`: steps, burst, authentication, endurance,
  persisted job counts, duration distributions and queue samples.
- `load/results/*-k6.json`: independent load-generator summaries.
- `load/results/backend-queue.svg`: queue growth and drain.

Generated reports do not contain credentials. The reviewed, dated benchmark
write-up in `docs/benchmarks/` is the portable evidence to share.

## Reading the result

Correctness and maximum throughput are separate claims. A step passes its
submission service-level check when acceptance exceeds 99%, failures stay below
1%, HTTP p95 stays below 500 ms, and the generator drops no scheduled arrivals.
Short capacity steps identify the next experiment; they do not prove sustained
production capacity.

The 10x burst uses ten times the highest passing stepped rate, for three seconds.
A separate three-second 1,000-arrival/sec probe tests the draft PRD target.
It uses the current two-worker validation topology; it is not a reproduction
of the PRD's proposed five-worker configuration.
Both are limited to 128 virtual users, 512 MB of generator memory, and two
generator CPU cores. Dropped arrivals are visible and mean the full offered
load was not delivered. A short burst cannot prove a sustained throughput claim.

The endurance test uses eight concurrent submitting clients by default, equal
high/normal/low priority, and 100,000 distinct idempotency keys and report IDs.
It retries transport failures, 429 and 503 with the identical request and key.
An idempotent 200 response also counts as accepted: the initial response may
have been lost after a successful commit. Each logical job is counted once.
This is a closed workload, measuring completion of a fixed amount of work;
its request rate must not be compared directly with an open arrival-rate test.
PostgreSQL must contain exactly the accepted number under the temporary key.
No direct database insertion substitutes for submission or execution.
The report handler returns a small deterministic JSON result and a suggested
artifact path. It does not render a PDF, write that artifact or call an external
service. This workload measures platform coordination with cheap handlers;
real handler execution costs will change throughput. Large objects are covered
by the separate storage and outage tests.

Authentication measurements exercise the real database lookup, HMAC comparison,
and Redis rate-limit script inside an API container. They use 10 warmups and
500 serial samples while idle. They exclude HTTP handling, session teardown
and contention under load; they are not an HTTP latency subtraction experiment.

Claim time starts before the database row lock and ends before the transition
insert/final flush and transaction commit. It includes database ownership work;
it excludes Redis reservation, permit acquisition and time waiting in the queue.
Only new attempt transitions carry this timing. Older rows are not counted.
The draft PRD's "job claim latency" means enqueue-to-worker-claim latency,
which corresponds more closely to queue wait, not this database-work timer.
Do not claim its 50 ms target from a small `claim_time` value. The default
dispatcher polls every 500 ms and workers can poll every 100 ms, so that target
is not guaranteed by the current configuration.

Queue wait is first start minus latest queued time; execution is terminal time
minus first start and can include retry/recovery delays. End-to-end time is
terminal time minus creation, including queueing, scheduling and retries.
Queue samples whose latest enqueue follows their first start are excluded;
retrying jobs can otherwise produce a misleading negative wait.
Historical performance windows can contain old runs. The per-key endurance
report isolates its own jobs and gives cleaner evidence.

## Scope

These checks operate on the local `forge` and `forge-validation` Compose
projects. Development and validation databases are separate. They do not prove
multi-host high availability, managed-service failover, TLS termination, external
backup recovery or an actual public production deployment. Those depend on the
chosen hosting environment.
