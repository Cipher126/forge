# Linux deployment validation — 2026-09-30

This experiment validates Forge's container topology, horizontal API routing,
graceful process shutdown, and recovery from a temporary Redis outage. It is a
single-machine Docker Desktop validation rather than a production capacity
claim.

## Environment

- Source: commit `b8dd975` plus the uncommitted validation topology
- Host: Windows 11 with Docker Desktop's Linux container engine
- Forge image digest:
  `sha256:13b62f898cecb7840947321c66acdc48a057dc7481779c518f735cadb92beb60`
- Python 3.14.7 in the Forge image
- PostgreSQL 18 Alpine, Redis 8.10 Alpine, and SeaweedFS 4.48
- Two API replicas behind nginx, one dispatcher, two workers, one scheduler,
  and one recovery process
- Worker concurrency: 4 per worker
- `demo.generate_report` with an inline JSON result
- Fresh named PostgreSQL, Redis, and object-storage volumes

The deployment image passed the startup compatibility check at Alembic revision
`f2c6d7e8a901`. Twenty initial health requests were split evenly between the two
API replicas. The Forge containers ran with a read-only root filesystem, a
bounded temporary filesystem, and `no-new-privileges`.

## Sustained submission results

| Arrival rate | Duration | Accepted | HTTP failures | Dropped | Median | p90 | p95 | Maximum |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 jobs/s | 60 s | 601/601 | 0 | 0 | 19.4 ms | 37.1 ms | 55.4 ms | 169.8 ms |
| 20 jobs/s | 60 s | 1,200/1,200 | 0 | 0 | 19.6 ms | 31.0 ms | 35.8 ms | 99.7 ms |

Both rates passed the provisional p95 target of 500 ms. The shorter latency in
the second run is normal warm-state variation and does not prove that higher
load improves performance. A longer test and multiple repetitions are needed
before publishing a capacity limit.

## Graceful worker shutdown

During a 20 jobs/s, 30-second run, `worker-1` received a graceful stop after ten
seconds. `worker-2` continued processing, and `worker-1` was restarted five
seconds later.

- 601/601 submissions accepted
- zero HTTP failures and zero dropped arrivals
- 24.9 ms median and 87.2 ms p95 submission latency
- every accepted job reached `succeeded`
- all post-run integrity checks returned zero

This shows that worker capacity can be reduced and restored without losing
accepted work. It does not prove uninterrupted execution for non-idempotent
external side effects; handlers remain responsible for their side-effect
idempotency contract.

## Graceful API replica shutdown

During a 10 jobs/s, 30-second run, `api-1` was gracefully stopped while nginx
continued routing to `api-2`. The first replica was started again ten seconds
later.

- 300/300 submissions accepted
- zero HTTP failures and zero dropped arrivals
- 24.5 ms median and 49.1 ms p95 submission latency
- all 300 jobs reached `succeeded`
- all post-run integrity checks returned zero

At this workload the two-replica topology supports a rolling API restart
without client-visible submission failure.

## Redis outage and recovery

Redis was stopped for five seconds during a 10 jobs/s, 30-second run, then
started again. The dedicated test key allowed 10,000 requests per minute so API
rate limiting could not distort the result.

- 258 submissions accepted and 20 requests failed during the outage
- 22 arrivals were dropped because Redis connection attempts occupied the
  available k6 virtual users
- 27.9 ms median, 5,019.5 ms p95, and 6,646.5 ms maximum request latency
- all 258 accepted jobs reached `succeeded` after Redis returned
- no duplicate attempt numbers, stale leases, missing admission slots, or
  terminal jobs retaining admission slots

The dispatcher stayed alive and resumed work. Both workers and the recovery
process exited on Redis connection errors and were restored by the container
restart policy; across the outage experiments the workers restarted three to
four times and recovery restarted four times. The API replicas remained alive
and healthy.

The durability boundary held: Forge lost no job after returning `202 Accepted`.
Availability during the dependency outage did not hold, which is expected with
a single Redis instance. The long client waits motivated the timeout and
in-process reconnect follow-up recorded below.

An earlier Redis run used the CLI's default 100-requests-per-minute key and was
excluded because its later `429` responses measured client throttling rather
than dependency recovery.

## Integrity verification

The post-run validator checked these database invariants after every disruption:

- outstanding jobs have an admission slot
- terminal jobs do not retain admission slots
- active attempts agree with their owning jobs
- attempt numbers are unique per job
- no outbox lease remains expired

Every check returned zero violations. Raw k6 summaries remain under
`load/results/` and are ignored by Git.

## Reproduction

The isolated topology is defined in `deploy/compose.validation.yaml`:

```powershell
docker compose -f deploy/compose.validation.yaml up -d --build
docker compose -f deploy/compose.validation.yaml ps
```

Run the integrity validator inside a Forge container after a test:

```powershell
docker compose -f deploy/compose.validation.yaml run --rm --no-deps api-1 `
  python scripts/validate_integrity.py
```

Use a dedicated high-rate `jobs:write` credential with
`load/k6/submit-jobs.js`. Do not reuse ordinary client credentials because an
intentional rate limit makes a capacity or outage result invalid.

## Redis reliability follow-up

Forge was changed to use configurable Redis connect and operation timeouts.
Workers and recovery now reconnect in-process with exponential backoff and
jitter. A worker also retains responsibility for an interrupted reservation and
requeues it after Redis returns, preventing a message from remaining stranded
in a live worker's processing list.

The same five-second Redis interruption was then repeated at 10 jobs/s for 30
seconds:

| Measurement | Before | After |
|---|---:|---:|
| Completed requests | 278 | 301 |
| Accepted jobs | 258 | 233 |
| Failed submissions during outage | 20 | 68 |
| Dropped arrivals | 22 | 0 |
| Median latency | 27.9 ms | 19.6 ms |
| p95 latency | 5,019.5 ms | 513.0 ms |
| Maximum latency | 6,646.5 ms | 521.3 ms |
| Worker/recovery restarts | 3–4 each | 0 |

The higher failure count is the intended result of failing promptly rather than
holding requests until Redis returns. All 233 accepted jobs reached
`succeeded`, every integrity check returned zero, and no worker, recovery,
dispatcher, or API container restarted. Clients can retry the prompt `503`
responses using the same idempotency key.

An intermediate run revealed that keeping a worker alive could strand a
reservation interrupted during Redis failure because recovery correctly avoids
stealing from a worker with a live heartbeat. The worker-owned reconciliation
fix recovered those reservations, and the final run had no queued jobs after
the 40-second convergence window.

## Hard worker-kill recovery

A deterministic `demo.wait` job was configured to run for 45 seconds. The
validator waited until PostgreSQL recorded its active attempt and then sent
`SIGKILL` to the owning worker container. The killed container remained down
while the surviving worker handled recovery.

- Attempt 1 became `abandoned` with failure kind `worker_lost` after its
  ownership lease expired.
- Recovery scheduled a retry and the scheduler returned the job to `queued`.
- Attempt 2 was owned by a different worker and reached `succeeded`.
- The job had one terminal success after 77.8 seconds.
- The validator restored the killed worker container afterward.

This proves job-state recovery and exclusive terminal ownership at the tested
scale. The demonstration handler has no external side effect, so this result
does not claim exactly-once behavior for email, payment, or third-party API
handlers. Those handlers must still use a stable destination idempotency key.

The experiment is reproducible with:

```powershell
uv run python scripts/run_worker_kill_validation.py
```

## PostgreSQL outage and recovery

PostgreSQL was stopped for five seconds while the API accepted a constant 10
requests per second. The first run proved durability but exposed slow connection
failure and container restarts: p95 reached 7.58 seconds, 32 arrivals were
dropped by the load generator, dispatcher restarted twice, and scheduler and
recovery restarted once.

Forge was changed to use a 500 ms PostgreSQL connection timeout, a one-second
pool wait, and jittered in-process reconnect loops. Workers also return a Redis
reservation if the database disappears before durable attempt ownership is
acquired. The final 20-second verification produced:

- 201/201 planned requests executed with zero dropped arrivals;
- 116 jobs accepted and all 116 reached `succeeded`;
- 85 prompt failures while PostgreSQL was unavailable;
- 430.1 ms median and 1,221.2 ms p95 latency;
- zero API, dispatcher, worker, scheduler, or recovery restarts; and
- zero integrity violations.

The failed requests were never acknowledged as accepted and are safe for the
client to retry with the same idempotency key.

## Object-store outage and recovery

`deploy/compose.blob-validation.yaml` lowers the inline threshold only for this
validation profile, allowing the ordinary demo handler to exercise blob paths.
With the object store stopped, an externally stored payload submission returned
`503 dependency_unavailable` in 3.15 seconds. PostgreSQL contained zero job rows
for that request, so Forge did not acknowledge work whose payload was missing.

The result-side test accepted an inline payload whose result exceeded the test
threshold. The object store was removed for eight seconds while workers ran:

- the job recorded failed infrastructure attempts rather than losing work;
- infrastructure failures extended the effective attempt allowance so they did
  not exhaust the submitter's handler retry budget;
- the job reached `succeeded` after storage returned;
- the result was stored externally;
- neither worker restarted; and
- every integrity check returned zero.

Object-store calls now have bounded SDK network settings plus a three-second
application deadline, covering DNS stalls that socket timeouts alone cannot
bound.

## Worker drain deadline recovery

A validation override reduced the worker drain deadline to two seconds and the
attempt lease to six seconds. An eight-second `demo.wait` job was claimed, then
the owning worker received `SIGTERM`:

- the worker stopped accepting reservations immediately;
- it kept its heartbeat and attempt renewal active during the drain window;
- it logged `worker_drain_deadline_exceeded` and exited after 2.49 seconds;
- attempt 1 became `abandoned` with failure kind `worker_lost` after lease expiry;
- attempt 2 ran on the other worker and reached `succeeded`;
- the stopped worker was restored; and
- every integrity check returned zero.

The normal profile uses a 90-second drain deadline and a two-minute container
grace period. `deploy/compose.drain-validation.yaml` shortens the timings only
for this recovery experiment.
