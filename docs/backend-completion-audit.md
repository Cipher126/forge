# Forge backend completion audit

Status: Evidence-based review on 2026-09-30

This audit compares the original product requirements with the accepted
architecture decisions and the current repository. Later decisions intentionally
replace some implementation details from the draft PRD: PostgreSQL is the source
of truth, scheduling is coordinated with `FOR UPDATE SKIP LOCKED`, the durable
dead-letter queue is a job state, and the dashboard was subsequently added to
scope. Those are recorded design choices rather than defects.

## Summary

The backend's main execution path is implemented. Forge can durably accept,
dispatch, execute, retry, schedule, cancel, replay, rate-limit, archive, and
observe jobs. Linux validation has demonstrated load-balanced API operation,
worker draining, Redis, PostgreSQL, and object-storage interruption, and recovery
without accepted-job loss.

The backend is not yet complete by the project's definition of done. The main
remaining work is failure evidence, scale evidence, a few lifecycle policy gaps,
and production operations material.

| Area | Status | Evidence or gap |
|---|---|---|
| Durable submission and execution | Complete | PostgreSQL job plus outbox transaction, Redis dispatch, database-owned attempt claim, persisted result |
| At-least-once recovery | Complete and proven at tested scale | Lease recovery, reservation recovery, missing-delivery reconstruction, Redis outage, and a hard worker-container kill |
| Retry and dead-letter handling | Complete | Failure classification, capped jittered retry, durable `dead_lettered` state, linked admin replay |
| Idempotent submission | Complete with one policy gap | Per-key uniqueness and request fingerprint conflict detection; TTL is fixed at 24 hours rather than configurable |
| Priority and scheduling | Complete | Weighted `8:2:1` claim cycle; PostgreSQL scheduling with concurrent-safe row claims |
| Cancellation | Complete | Immediate queued cancellation and cooperative running cancellation through lease renewal |
| Capacity controls | Complete, partially proven | Worker concurrency, leased job-type permits, exact PostgreSQL admission slots, Redis API-key rate limits; 10x overload experiment missing |
| API security | Complete | Opaque HMAC-digested keys, scopes, admin separation, body limits, fixed handler registry, schema validation, audit records |
| Handler compatibility | Partial | Exact handler version is stored and resolved; unsupported deployed versions fail cleanly, but there is no distinct durable `version_unsupported` state |
| Blob storage | Complete and proven at tested scale | Oversized payload/result routing, bounded outage response, eventual result retry, and orphan cleanup are implemented |
| Retention and archival | Partial | Archival and purge exist; one retention period applies to all terminal states, archives are plain JSON, and failed/DLQ 90-day retention plus compression are missing |
| Operational API | Complete | Summary, queues, workers, performance percentiles, authenticated Prometheus text, and SSE transitions |
| Logs and traces | Partial | Correlated structured logs, redaction, W3C trace propagation, and OTLP export exist; not every durable state transition emits its own structured log |
| Deployment | Substantially complete | Non-root read-only image, migration gate, production Compose, two-replica validation topology, startup and integrity checks |
| Automated failure validation | Partial | Redis failure tests, integrity validation, and a repeatable hard worker-kill scenario exist; PostgreSQL and object-store experiments are documented but still need one-command automation |
| Performance evidence | Partial | Reproducible k6 harness and 10/20 jobs-per-second results exist; 1,000 jobs/sec, 100,000 jobs, claim latency, auth overhead, and 10x burst targets are unproven |
| Dashboard | Missing | Accepted later into scope; backend API support exists, React application has not started |

## Implemented backend capabilities

### Durable core

- `POST /v1/jobs` validates a registered payload before storage.
- Immediate jobs enter `queued`; future jobs enter `scheduled`.
- Job creation, admission-slot ownership, idempotency, transition history, and
  transactional outbox creation commit together.
- The dispatcher leases outbox events, publishes to Redis, and safely tolerates
  duplicate publication.
- Workers reserve Redis messages but PostgreSQL grants attempt ownership.
- Results are stored inline or in S3-compatible storage according to size.

### Failure handling

- Attempts have renewable ownership leases and immutable ownership tokens.
- Temporary, permanent, timeout, cancellation, and worker-loss outcomes are
  distinguished.
- Retries use capped exponential backoff with jitter.
- Attempt exhaustion produces durable dead-letter state.
- Replay creates a new job linked to the original dead-lettered job.
- Recovery handles expired attempts, abandoned Redis reservations, and missing
  Redis deliveries reconstructed from PostgreSQL.
- Redis clients now use bounded network timeouts. Workers and recovery reconnect
  with jittered backoff and reconcile interrupted reservations without process
  restart.
- PostgreSQL clients fail promptly; dispatcher, scheduler, and recovery reconnect
  in-process, and workers requeue reservations interrupted before a database claim.
- Object-store operations have bounded network and application deadlines.
  Storage outages are audited as infrastructure attempts without exhausting the
  submitter's handler retry allowance.

### Scheduling, capacity, and security

- Three priority tiers use weighted claim opportunities with a starvation guard.
- Concurrent schedulers promote due work with PostgreSQL row locking.
- Local worker concurrency and global leased job-type permits are enforced.
- A fixed pool of 50,000 PostgreSQL admission slots provides an exact durable
  backlog limit without one global submission lock.
- API keys are opaque bearer credentials. Forge stores only an HMAC digest and
  supports `jobs:read`, `jobs:write`, and `admin` scopes.
- Redis-backed per-key request limits distinguish client throttling (`429`) from
  system admission backpressure (`503`).
- Request size, payload schema, handler registry, and audit-history controls are
  implemented.

### Operations and data lifecycle

- Public liveness and dependency-aware readiness endpoints exist.
- Admin endpoints expose status totals, queues, workers, performance percentiles,
  low-cardinality Prometheus text, and resumable server-sent events.
- JSON logs redact credentials and correlate request, trace, job, and attempt
  activity.
- Trace context crosses the durable outbox and queue boundary; OTLP export is
  configurable.
- Maintenance expires idempotency records, archives retained jobs, and removes
  orphaned payload/result objects after a grace period.

## Proven behavior

The Linux validation environment has demonstrated:

- 601/601 accepted at 10 jobs/sec for 60 seconds, with a 55.4 ms submission p95.
- 1,200/1,200 accepted at 20 jobs/sec for 60 seconds, with a 35.8 ms p95.
- 601/601 accepted while one of two workers drained and restarted.
- 300/300 accepted while one of two API replicas was stopped and restored.
- A five-second Redis outage with bounded client latency, zero process restart,
  zero dropped load-generator arrivals, and every accepted job succeeding.
- A worker killed with `SIGKILL` during a 45-second job: attempt 1 became
  `abandoned` with `worker_lost`, attempt 2 ran on a different worker and
  succeeded, and the job reached one terminal success in 77.8 seconds.
- A five-second PostgreSQL outage under constant submissions: zero dropped
  arrivals, zero process restarts, all 116 accepted jobs succeeded, and failed
  submissions received prompt dependency errors.
- Object storage unavailable during a blob-backed submission: `503` in 3.15
  seconds and no job persisted; unavailable during result storage: the accepted
  job retried and succeeded after storage returned.
- Zero violations across admission-slot, attempt-ownership, duplicate-attempt,
  and outbox-lease integrity checks.

These results establish correctness at the tested load. They do not establish
the PRD's maximum-throughput or 100,000-job claims.

## Remaining backend work

### Priority 1: lifecycle policy completion

1. Add configurable idempotency retention instead of a fixed 24-hour constant.
2. Split successful retention (30 days) from failed/dead-letter retention
   (90 days).
3. Decide whether archive compression is worth the added operational complexity;
   if retained as a requirement, write compressed JSON and record content type.
4. Add a configurable worker drain deadline. On expiry, terminate local handler
   execution and leave lease-based recovery to another worker.
5. Decide whether unsupported handler versions need a distinct durable state or
   whether the accepted reduced lifecycle should remain authoritative.

### Priority 2: measurable observability and scale

1. Add counters/rates for enqueued and completed jobs, failure rate, DLQ size,
   and per-job-type permit utilization to the metrics contract.
2. Emit one structured log for every durable transition or explicitly document
   the transition table plus SSE stream as the authoritative transition record.
3. Add Prometheus, Grafana, and Tempo to a local observability Compose profile.
4. Automate the Linux failure scenarios so results are reproducible without a
   manual sequence of Docker commands.
5. Measure claim latency and authentication overhead separately.
6. Run stepped capacity tests, then a 10x admission burst and a 100,000-job run.
   Publish saturation, queue-depth, recovery-time, and latency graphs without
   claiming untested production capacity.

## Recommended sequence before dashboard work

1. Complete retention and graceful-shutdown policies.
2. Expand metrics and add the local observability profile.
3. Automate the PostgreSQL and object-storage outage experiments.
4. Run the large benchmark and finish the operations and tradeoff write-up.
5. Freeze the backend API contract, then build the dashboard against it.

This order makes the dashboard a view over proven backend behavior and gives the
portfolio project defensible failure and performance evidence rather than only
feature coverage.
