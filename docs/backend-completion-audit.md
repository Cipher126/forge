# Forge backend completion audit

Status: Evidence-based review, updated on 2026-10-25

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

The accepted backend features and the remaining validation tooling are now
implemented. The 100,000-job acceptance run and the separate 5,000-job runtime
regression passed. This audit
distinguishes implementation completion, measured correctness, and unmet
performance hypotheses. The dashboard was subsequently implemented and verified
against the real local stack on 2026-10-25; the original performance targets
and external production validation remain open.

| Area | Status | Evidence or gap |
|---|---|---|
| Durable submission and execution | Complete | PostgreSQL job plus outbox transaction, Redis dispatch, database-owned attempt claim, persisted result |
| At-least-once recovery | Complete and proven at tested scale | Lease recovery, reservation recovery, missing-delivery reconstruction, Redis outage, and a hard worker-container kill |
| Retry and dead-letter handling | Complete | Failure classification, capped jittered retry, durable `dead_lettered` state, linked admin replay |
| Idempotent submission | Complete | Per-key uniqueness, request fingerprint conflict detection, and configurable expiry (24-hour default) |
| Priority and scheduling | Complete | Weighted `8:2:1` claim cycle; PostgreSQL scheduling with concurrent-safe row claims |
| Cancellation | Complete | Immediate queued cancellation and cooperative running cancellation through lease renewal |
| Capacity controls | Complete with measured overload evidence | Concurrency/permit/admission tests, per-key quotas, and a 10x burst with retryable rejection and completion of accepted work |
| API security | Complete | Opaque HMAC-digested keys, scopes, admin separation, body limits, fixed handler registry, schema validation, audit records |
| Handler compatibility | Complete | Exact handler versions are resolved; an unavailable deployed version immediately records a permanent `handler_version_unsupported` failure without retry |
| Blob storage | Complete and proven at tested scale | Oversized payload/result routing, bounded outage response, eventual result retry, and orphan cleanup are implemented |
| Retention and archival | Complete | Configurable expiry and retention, schema 2 gzip archives containing external payload/result data, and orphan collection; archive read/write failure prevents row deletion |
| Operational API | Complete | Summary, queues, workers, performance percentiles, authenticated rate/capacity Prometheus metrics, and SSE transitions |
| Logs and traces | Complete | Correlated JSON and W3C/OTLP traces; production sessions mirror committed transitions, discard rollback buffers, and keep PostgreSQL/SSE authoritative |
| Deployment | Complete local configuration and acceptance | Non-root read-only image, migration gates on every production process, log rotation, drain deadlines, two API replicas; actual hosting/TLS/backup restoration remains environment-specific |
| Automated failure validation | Implemented for tested dependency scenarios | Redis failure tests, repeatable hard worker-kill validation, deployment acceptance, and one-command PostgreSQL/object-store outage recovery checks exist |
| Performance evidence | Full endurance correctness passed | 100,000/100,000 completed with matching results and stable processes; stepped, burst, authentication, priority, latency and queue evidence is published; throughput and enqueue-to-claim targets remain unmet |
| Dashboard | Implemented and locally verified | React/TypeScript UI, generated API types, scoped connection, SSE/polling, job lifecycle controls, results, workers/queues, handlers, key administration and audit history; mocked-browser and real-stack browser checks |

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
- An unavailable exact handler version immediately fails its claimed attempt as
  a permanent `handler_version_unsupported` error. The existing `failed` job
  state stays authoritative while attempt and transition records preserve the
  specific diagnosis.

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
- Prometheus reports five-minute enqueue/completion rates, failure ratio,
  dead-letter volume, and bounded-cardinality job-type permit utilization. The
  local validation overlay provisions Prometheus, Grafana, Tempo, and a Forge
  operations dashboard while keeping the scrape endpoint authenticated.
- Maintenance expires idempotency records, retains failure evidence longer than
  successful work, writes gzip JSON archives before deleting database rows, and
  removes orphaned payload/result objects after a grace period.

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
- A worker exceeded a two-second validation drain deadline: it exited in 2.49
  seconds, its attempt became `abandoned/worker_lost`, and the other worker
  completed attempt 2 successfully.
- Zero violations across admission-slot, attempt-ownership, duplicate-attempt,
  and outbox-lease integrity checks.
- The one-command deployment acceptance run started the complete topology,
  accepted and completed 250/250 jobs at 10 jobs/sec, recorded 82.1 ms p95
  submission latency, drained every queue, kept all 14 services online, passed
  startup and integrity checks, and revoked its temporary credential.
- Automated dependency validation on 2026-10-05 stopped PostgreSQL for five
  seconds: three submissions received retryable 503 responses in 508–516 ms;
  three previously accepted jobs and a retried submission succeeded without
  Forge process restart. Storage loss rejected a blob payload in 3.037 seconds
  without persisting a job. An inline job with one handler attempt allowed
  survived five infrastructure failures and succeeded on attempt six with its
  externally stored result verified. Normal configuration and credentials
  were restored, and every integrity check remained at zero violations.

The 2026-10-05 completion batch additionally submitted 100,000 distinct jobs
through HTTP and verified 100,000 successes with matching report IDs/formats,
zero integrity violations and no Forge process restart. The queue peaked at
28,058 outstanding jobs and returned to zero. HTTP submission p95 was 323.6 ms;
queue-wait p95 was 23.46 minutes. All priority tiers eventually completed.

These results establish correctness at the tested load and the fixed-size
100,000-job experiment. They do not establish the PRD's maximum-throughput
or enqueue-to-claim latency targets. See the
[dated acceptance evidence](benchmarks/2026-10-05-backend-acceptance.md).

## Completion batch

- After-commit structured transition logs, with transaction/savepoint rollback
  and broken-log-sink coverage.
- Database claim and end-to-end timing in the reviewed API contract.
- A real idle authentication/rate-limit benchmark: combined p95 3.09 ms across
  500 serial samples, with HTTP and load-contention limitations stated.
- Stepped probes: 10, 20 and 40 arrivals/sec passed; 80 and 160 did not.
  The 400/sec burst returned retryable 503 responses and every accepted job
  completed. The short 1,000/sec probe did not deliver/accept the planned load.
- A fixed 100,000-job HTTP workload, isolated durable/result verification,
  priority measurements, queue samples and portable graph rendering.
- `scripts/validate_backend.py` runs the entire acceptance sequence.
- Production migration gates, bounded dependency defaults, log rotation,
  operations/restore guidance, Redis-key documentation, architecture diagram,
  accepted-tradeoff clarification and a dashboard API baseline.
- Retention now embeds external data before deleting live rows. Worker traces
  close even on permit denial and acquisition/release failure.
- Raw PostgreSQL handshake/shutdown errors now use the same bounded dependency
  response and in-process retry paths as adapted database errors. A real repeat
  verified process survival after a preceding candidate failed that assertion.
- A failed savepoint flush preserves earlier outer-transaction log records;
  the targeted regression and the expanded 99-test suite passed.
- The corrected runtime also passed deployment acceptance, PostgreSQL and
  object-storage outages, hard worker kill and a separate 5,000-job regression
  with matching stored results, stable processes and zero integrity violations.

The backend feature and local correctness-validation batch is complete. No
missing lifecycle feature is being deferred to the dashboard. The measured
performance gaps remain explicit rather than being signed off as achieved.

## Explicit limits and the next phase

The PRD's 1,000 enqueue/sec hypothesis used five workers; this local benchmark
uses two workers with eight execution slots and a four-permit report-handler cap.
It does not establish that target. Its enqueue-to-worker-claim target of 50 ms
also differs from the newly measured database claim-work timer. Default
dispatcher/worker polling and queueing make that latency target unproven.

Submission idempotency prevents duplicate jobs, not arbitrary repeated external
effects. The accepted architecture corrected that draft PRD wording. Logs are
best-effort mirrors; database transitions and SSE are the durable history.

Actual multi-host failover, public TLS, external backups and production restore
timings depend on the chosen environment and have not been claimed.

Dashboard implementation and local integration checks are complete. Backend
performance optimization remains an explicit engineering gap against the
original targets; it can use the recorded latency, queue and saturation evidence.
