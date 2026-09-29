# Forge Project Plan

Status: Working draft for review

Forge will be built as a sequence of working, testable slices. Every milestone
has a behavior to demonstrate and evidence to collect. Completing an early
milestone does not reduce the final project scope.

The accepted implementation stack and repository shape are recorded in
`docs/technology-stack.md`. Technology choices support these milestones without
changing the product's definition of done.

The accepted initial lifecycle and logical PostgreSQL schema are recorded in
`docs/data-model.md`.

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
