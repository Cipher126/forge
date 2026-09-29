# Forge Architecture Decisions

Status: Working draft for review
Purpose: Record why Forge is designed this way before implementation begins.

## ADR-001: At-least-once execution

Forge prioritizes recovering accepted work after failure. A job may therefore be
attempted more than once.

Forge deduplicates repeated submissions and supplies stable identifiers to job
handlers. Preventing repeated external side effects requires an idempotent
handler or a destination that supports idempotency keys. Forge does not claim
exactly-once execution across arbitrary external systems.

## ADR-002: PostgreSQL is the source of truth

An accepted job, its state, execution attempts, and audit history are durable in
PostgreSQL. Redis is used for fast delivery and temporary coordination.

The API writes the job and an enqueue event to a transactional outbox in the
same PostgreSQL transaction. It returns `202 Accepted` only after that transaction
commits. A dispatcher publishes outbox events to Redis and may publish an event
more than once. Workers handle duplicate deliveries safely.

## ADR-003: Jobs and attempts are separate

A job records the outcome requested by a client. An attempt records one worker's
effort to produce that outcome. Each attempt retains its worker, timestamps,
result or error, and termination reason.

Forge uses `max_attempts` to mean the total number of executions, including the
initial execution. An abandoned attempt consumes the attempt budget.

Expected job states are:

- `scheduled`
- `queued`
- `running`
- `retry_scheduled`
- `cancellation_requested`
- `succeeded`
- `failed`
- `dead_lettered`
- `cancelled`

Attempts have their own terminal outcomes, including `succeeded`, `failed`, and
`abandoned`.

## ADR-004: PostgreSQL grants attempt ownership

A Redis queue entry tells a worker which job to consider. PostgreSQL determines
whether the job is still claimable.

Claiming a job creates an attempt with a unique ownership token and renewable
lease. State-changing attempt updates must present the current token. Once the
lease expires and a new attempt owns the job, updates from the stale worker are
rejected.

Initial timing values are a 30-second lease, renewal every 10 seconds, and a
recovery scan every 10 seconds. These are hypotheses to validate through tests.

## ADR-005: Weighted priority scheduling

Forge has high, normal, and low priority tiers. It uses a globally coordinated
weighted round-robin policy with initial weights of `8:2:1`. Jobs are FIFO within
each tier.

The guarantee is bounded claim opportunities for lower tiers under sustained
high-priority traffic. It is not a fixed maximum start time.

## ADR-006: PostgreSQL owns scheduled jobs

Future jobs remain in PostgreSQL with a `run_at` value. Concurrent scheduler
instances select due rows with `FOR UPDATE SKIP LOCKED`, change them to `queued`,
and create outbox events in the same transaction.

This removes the need for scheduler leader election. Schedule lag is measured as
the difference between `run_at` and the first time the job becomes queued.

## ADR-007: Bounded retry and linked replay

Forge distinguishes temporary failures, permanent failures, and abandoned
attempts. Temporary failures retry with capped exponential backoff and jitter.
Permanent failures stop immediately. Exhausting the attempt budget moves a job
to `dead_lettered`.

The dead-letter queue is represented by durable PostgreSQL state. Admin replay
creates a new job linked to the original, preserving the original history and
requiring an explicit decision about reuse of the side-effect idempotency key.

## ADR-008: Three layers of capacity control

Forge combines:

- a local concurrency limit for each worker;
- Redis-backed, expiring permits for global per-job-type concurrency; and
- admission backpressure based on the complete outstanding backlog.

Waiting for a job-type permit does not consume an attempt. A client that exceeds
its own rate limit receives `429 Too Many Requests`; system overload produces
`503 Service Unavailable` with `Retry-After`.

Initial defaults are four concurrent attempts per worker, a backlog threshold of
50,000 jobs, a one-second backlog refresh, 30-second permit leases, and permit
renewal every 10 seconds. Testing will determine final values.

## ADR-009: Scoped opaque API keys

Forge v1 authentication uses opaque API keys sent as:

```http
Authorization: Bearer forge_live_<public-id>_<secret>
```

The documentation must state that these are API keys rather than JWTs. The
public ID supports an indexed lookup; the random secret proves possession.
Forge stores only an HMAC-SHA-256 digest of the secret, keyed with a server-side
pepper. Initial scopes are `jobs:write`, `jobs:read`, and `admin`. The first
administrator key is created on the trusted host through the Forge CLI; that
administrator can issue later keys through the API. `/health` is public;
`/metrics` is authenticated by default.

Idempotency keys are unique per API key in PostgreSQL. Forge stores a fingerprint
of the normalized submission. Reuse with the same request returns the existing
job; reuse with different content returns `409 Conflict`.

Job handlers and payload schemas are registered at deployment. Payload data
cannot select code, modules, or import paths. The initial maximum request body is
10 MB, while payloads and results above 256 KB use blob storage.

Each registration binds a public job type and immutable version to a Pydantic
payload model, callable, execution mode, timeout, and default attempt limit. One
registered version is active for new submissions. Submission records that
version on the job, while workers resolve the exact recorded version. Activating
a newer version therefore does not change already accepted work. A process
fails during startup when registrations are duplicated or an execution mode is
incompatible with its callable.

## ADR-010: Incremental delivery of the complete scope

Milestones are an implementation and learning sequence, not reduced product
editions. Forge is complete only when the agreed full scope and its evidence are
delivered.

Performance requirements begin as hypotheses. Benchmark claims must identify
hardware, payload, handler workload, concurrency, duration, throughput, and
latency percentiles.

## ADR-011: Operational web dashboard

Forge includes a web dashboard that consumes the same authenticated API exposed
to other clients. It must not read Redis or PostgreSQL directly.

The dashboard covers system health, queues, jobs, attempts, workers, scheduling
lag, dead-lettered jobs, cancellation, replay, and live operational updates. It
does not include a workflow builder, arbitrary payload editing, full identity
management, or a replacement for Prometheus/Grafana.

## ADR-012: Python backend foundation

Forge uses Python 3.14, uv, FastAPI, Pydantic, SQLAlchemy 2 async, asyncpg, and
redis-py through its asyncio API.

SQLAlchemy is used for routine persistence and mapping. Concurrency-critical
operations may use explicit SQL when transaction boundaries, row locks,
conditional updates, or PostgreSQL-specific behavior need to remain visible.

## ADR-013: Alembic manages schema changes

Alembic records and applies PostgreSQL schema changes because it integrates with
the selected SQLAlchemy metadata while still allowing explicit SQL.

Autogenerated revisions are candidates, not trusted output. Every migration is
reviewed for constraints, indexes, locking behavior, data impact, and agreement
with the application models. CI creates a database from the migration history and
checks the resulting schema. Production recovery favors a new forward migration
over assuming every downgrade is operationally safe.

## ADR-014: Asyncio-driven worker processes

Each worker is an independent Python process with one asyncio event loop. Queue
claims, database operations, heartbeat updates, and lease renewal remain
responsive while handlers execute under a bounded local concurrency limit.

Handler registration declares one execution mode:

- async I/O handlers run as asyncio tasks;
- blocking I/O handlers run in a bounded thread pool; and
- CPU-intensive handlers run in a bounded process pool.

Thread cancellation is cooperative. Process termination can stop local
computation but cannot reverse an external side effect that already occurred.

Forge uses redis-py directly and implements its own atomic Redis protocols. It
does not build on an existing job framework such as Celery, RQ, Dramatiq, or ARQ.

## ADR-015: React operational dashboard

The dashboard is a React and TypeScript single-page application built with Vite.
It uses React Router for routes, TanStack Query for server state, Tailwind CSS for
styling, Recharts for operational graphs, and server-sent events for live
updates. Polling remains available as a fallback.

The browser communicates directly with Forge's documented API. The project does
not add a second application server or server-side rendering layer because the
authenticated operational dashboard has no SEO or initial-render requirement.

## ADR-016: Layered validation and observability

Forge uses different forms of validation for different claims:

- pytest and pytest-asyncio for unit and application tests;
- Hypothesis state machines for lifecycle invariants;
- Testcontainers with real PostgreSQL and Redis for integration tests;
- explicit process and container termination for failure tests;
- HTTPX for API contracts;
- Vitest and Testing Library for dashboard components;
- Playwright for browser workflows; and
- k6 for load scenarios and performance thresholds.

Observability uses structlog JSON logs, the Prometheus Python client, and
OpenTelemetry traces exported through OTLP. Local infrastructure includes
Prometheus, Grafana, and Tempo. High-cardinality identifiers such as job IDs,
attempt IDs, and API key IDs belong in logs and traces, not metric labels.

## ADR-017: Modular monorepo and separate runtime processes

Forge keeps one shared Python package and one React dashboard in a single
repository. The API, worker, outbox dispatcher, scheduler, and recovery loop have
independent process entry points and can be deployed or scaled independently.

The domain layer contains state and policy rules without importing FastAPI,
SQLAlchemy, or Redis. Application services coordinate those rules through
persistence and queue boundaries. PostgreSQL and Redis adapters implement the
infrastructure-specific behavior. Boundaries are introduced where they expose
correctness or improve testing; trivial functions do not receive ceremonial
interfaces.

uv manages Python dependencies, npm manages the single dashboard package, and
Docker Compose supplies PostgreSQL, Redis, MinIO, Prometheus, Grafana, and Tempo
for local development and demonstrations.

## ADR-018: Identifier policy follows record scope

Domain entities and records that cross process boundaries use UUIDv7 values. This
includes API keys, jobs, attempts, and outbox events. High-volume internal rows
that PostgreSQL alone creates use `bigint GENERATED ALWAYS AS IDENTITY`. This
includes job transitions and idempotency records.

Identity values are unique row identifiers, may contain harmless gaps, and are
not exposed as authorization credentials. PostgreSQL primary-key and unique
constraints remain the final collision safeguard for both identifier types.
Python 3.14 supplies UUIDv7 generation in the standard library. UUIDs identify
records and are never treated as authentication secrets.

## ADR-019: Reduced durable job lifecycle

An immediately executable job is created as `queued`; a future job is created as
`scheduled`. Forge has no durable `pending` state because validation happens
before the submission transaction and the committed outbox event already makes
the job eligible for eventual delivery.

Terminal states are immutable. Dead-letter replay creates a new linked job.
Only the current attempt token may finish a running job. If an attempt lease
expires while cancellation is requested, recovery marks the attempt abandoned
and the job cancelled instead of retrying it. Every job-state update and its
append-only transition record occur in the same PostgreSQL transaction.
