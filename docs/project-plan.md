# Forge Project Plan

Status: Backend and dashboard implemented and locally verified;
performance targets and external production validation remain open — 2026-10-25

Forge will be built as a sequence of working, testable slices. Every milestone
has a behavior to demonstrate and evidence to collect. Completing an early
milestone does not reduce the final project scope.

The accepted implementation stack and repository shape are recorded in
`docs/technology-stack.md`. Technology choices support these milestones without
changing the product's definition of done.

The accepted initial lifecycle and logical PostgreSQL schema are recorded in
`docs/data-model.md`.

## Current milestone position

| Milestone | Current position |
|---|---|
| 1. Durable core | Implemented and verified through real HTTP submissions, outbox delivery, claims and stored results |
| 2. Failure handling | Implemented; dependency outages, hard worker kill, retry/recovery, idempotency and replay are covered |
| 3. Scheduling, priority and cancellation | Implemented and tested; mixed-priority endurance measurements are published |
| 4. Capacity and security | Implemented; scoped keys, quotas, concurrency, admission and explicit overload responses are verified |
| 5. Data lifecycle and compatibility | Implemented; blob routing, retention, materialized archives, active-idempotency retention and version behavior are tested |
| 6. Operational dashboard | Implemented; real API data, submission/scheduling, job and attempt timelines, results, cancellation/replay, workers/queues, handlers, scoped keys and audit history; component, mocked-browser and real-container browser checks passed |
| 7. Observability and validation | Backend logs, traces, metrics, repeatable acceptance command, 100,000-job evidence and runtime regression passed; dashboard tests, same-origin container deployment and GitHub Actions checks are implemented |

The expanded source suite passed 99 tests. The full HTTP endurance run completed
100,000/100,000 jobs with matching results, stable Forge processes and zero
integrity violations. The original 1,000-enqueue/sec and 50 ms enqueue-to-claim
targets **remain unmet**; their failed probes and queue latency are published,
not counted as passing performance acceptance. The completion batch also fixed
archive data loss, long idempotency-window retention, worker trace cleanup,
savepoint logging and raw PostgreSQL shutdown recovery.

All planned product areas now have implementations and local verification.
Backend performance optimization remains an explicit engineering gap: validate a named deployment and the intended
five-worker topology, identify its limiting resources, and measure each change.
Public hosting, TLS and external backup restoration depend on that deployment
choice. The project is feature-complete for the agreed local portfolio scope;
it does not meet the original performance targets or claim public production
deployment. See [the dashboard guide](dashboard.md) and
[completion verification](benchmarks/2026-10-25-project-verification.md).

See the [completion audit](backend-completion-audit.md),
[acceptance evidence](benchmarks/2026-10-05-backend-acceptance.md),
[repeatable validation guide](backend-acceptance.md), and
[dashboard API contract](backend-api-contract.md).

## 1. Durable core

Build the complete path from client submission through the API, PostgreSQL
transactional outbox, dispatcher, Redis, worker, and persisted result.

Demonstrate that accepted work survives dispatcher and Redis restarts and that
duplicate queue deliveries do not create two valid owners.

## 2. Failure handling

Add failure classification, bounded retry with backoff and jitter, attempt lease
renewal, abandoned-attempt recovery, dead-lettered jobs, linked replay, and
submission idempotency.

Kill workers during controlled jobs and record the resulting attempt history.
Demonstrate both recovery and the boundary of side-effect idempotency.

## 3. Scheduling, priority, and cancellation

Add future scheduling through PostgreSQL, concurrent scheduler instances,
weighted Redis priority selection, queued cancellation, and cooperative running
cancellation.

Measure schedule lag and priority distribution during sustained mixed traffic.

## 4. Capacity and security

Add worker concurrency, leased job-type permits, admission backpressure, per-key
rate limiting, scoped API keys, schema and size validation, and audit history.

Test permit recovery after worker termination, enforce configured concurrency,
and distinguish client throttling from system overload.

## 5. Data lifecycle and compatibility

Add versioned handler registration, unsupported-version behavior, S3-compatible
storage for large payloads and results, Redis reconciliation from PostgreSQL,
and retention and archival processes.

Verify rolling-version behavior, Redis-loss recovery, object references, and
retention policies.

## 6. Operational dashboard

Build an API-driven dashboard for overview metrics, queues, filterable jobs, job
and attempt timelines, worker health, scheduling lag, cancellation, and replay.
Use it to make failure-injection demonstrations observable.

## 7. Observability and validation

Complete Prometheus metrics, structured transition logs, query endpoints,
failure-injection tests, and a reproducible load-test harness.

Run the full benchmark and publish measured throughput, latency percentiles,
queue behavior, recovery timing, and system limits. Complete the architecture,
operations, tradeoff, and portfolio documentation from measured evidence.

## Definition of done

Forge is complete when all seven milestones are implemented, their stated
experiments are reproducible, the observed results are documented, and every
public guarantee matches the evidence.
