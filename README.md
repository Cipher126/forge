# Forge

Forge is an API-driven distributed job execution platform built from first
principles. It is designed to demonstrate durable acceptance, at-least-once
execution, retry and recovery behavior, scheduling, concurrency control, and
observable failure handling.

The durable execution path, retries, scheduling, cancellation, dead-letter replay,
and worker recovery are implemented. Design decisions live in
[`docs/architecture-decisions.md`](docs/architecture-decisions.md).

## Development prerequisites

- Python 3.14
- uv
- Docker with Docker Compose
- Node.js 24 and npm for the future dashboard

## Production containers

[`deploy/Dockerfile`](deploy/Dockerfile) builds one non-root, read-only-capable
image for every Forge process. Copy `.env.production.example` to
`.env.production`, replace every placeholder, build the image, and run the
migration as a one-off task before starting services:

```powershell
Copy-Item .env.production.example .env.production
docker compose -f deploy/compose.production.yaml build
docker compose -f deploy/compose.production.yaml run --rm migrate
docker compose -f deploy/compose.production.yaml up -d api dispatcher worker scheduler recovery maintenance
```

Scale stateless processes with Compose's `--scale`, for example
`--scale worker=4`. PostgreSQL connections are pooled separately in every
process. Budget for at most
`(FORGE_POSTGRES_POOL_SIZE + FORGE_POSTGRES_MAX_OVERFLOW) × process count`
connections, plus migration and administrative connections.

The production Compose file expects PostgreSQL, Redis, and S3-compatible object
storage to be supplied externally. It does not embed development credentials or
publish dependency ports.

Export the committed API contract after an intentional route or schema change
with `uv run python scripts/export_openapi.py`. Contract tests fail when the
application and [`docs/openapi.json`](docs/openapi.json) drift apart.

## Local API

```powershell
Copy-Item .env.example .env
docker compose up -d postgres redis object-store
uv sync
uv run forge-api
```

Run the transactional outbox dispatcher in a separate terminal:

```powershell
uv run forge-dispatcher
```

Run one or more workers in additional terminals:

```powershell
uv run forge-worker
```

Each worker runs up to `FORGE_WORKER_CONCURRENCY` jobs concurrently. The JSON
object in `FORGE_JOB_TYPE_CONCURRENCY_LIMITS` applies a shared limit across all
workers for selected job types. These Redis-backed permits are leased and
renewed, so capacity returns automatically when a worker disappears.

The API rejects new submissions with `503 system_overloaded` and a `Retry-After`
header when the durable outstanding backlog reaches
`FORGE_ADMISSION_BACKLOG_LIMIT`. An idempotent retry of an already accepted job
still returns that job while admission is closed.

Every authenticated endpoint also applies the request limit stored on its API
key. Successful responses include `X-RateLimit-Limit` and
`X-RateLimit-Remaining`; exhausted keys receive `429 rate_limit_exceeded` with
`Retry-After`. The sliding window is coordinated through Redis across API
processes.

HTTP request bodies are capped by `FORGE_MAX_REQUEST_BODY_BYTES` (10 MB by
default). Validated job payloads and results are serialized to canonical JSON;
values above `FORGE_MAX_INLINE_PAYLOAD_BYTES` (256 KB by default) are stored in
the configured S3-compatible object store. API responses expose storage flags
without revealing internal object keys. The local Compose service uses
SeaweedFS because MinIO Community Edition and its public images were retired in
2026.

Run the scheduler to promote due scheduled and retrying jobs:

```powershell
uv run forge-scheduler
```

Run recovery to handle expired worker leases:

```powershell
uv run forge-recovery
```

Recovery also reconstructs queued Redis deliveries from PostgreSQL every
`FORGE_DELIVERY_RECONCILIATION_INTERVAL_SECONDS`. Reconstructed duplicates are
safe because the database attempt claim permits only one current owner.

Dispatcher, scheduler, worker, recovery, and maintenance processes handle
`SIGINT` and `SIGTERM`. They stop accepting new work, finish their current
database operation, and close connections; workers keep their heartbeat while
draining already claimed jobs.

All entry points emit JSON logs. Fields named for credentials, secrets, tokens,
payloads, or results are recursively redacted, and API-key or Bearer values
embedded in text are masked. Temporary PostgreSQL, Redis, and object-store
client failures return `503 dependency_unavailable` without exposing connection
details.

Run maintenance to remove expired idempotency records, archive terminal jobs
older than `FORGE_JOB_RETENTION_DAYS`, and delete unreferenced payload/result
objects after `FORGE_ORPHAN_OBJECT_GRACE_HOURS`:

```powershell
uv run forge-maintenance
```

Archives are stored at `archives/jobs/<job-id>.json` with the job, its attempts,
and its transition history. Jobs that are still referenced as replay sources are
retained until those references no longer exist.

The public liveness endpoint is available at `GET /health`; `GET /ready` checks
PostgreSQL, Redis, and the object store.

## First administrator key

Set `FORGE_API_KEY_PEPPER` in `.env`, apply the database migration, and create
the first key from the trusted local shell:

```powershell
uv run alembic upgrade head
uv run forge key create --name "local admin" --scope admin
```

The CLI prints the credential once. Send it as an opaque API key, not as a JWT:

```http
Authorization: Bearer forge_live_<public-id>_<secret>
```

An administrator can create later scoped credentials through
`POST /v1/admin/api-keys`.

Administrators can inspect key metadata with `GET /v1/admin/api-keys` and revoke
a credential with `POST /v1/admin/api-keys/{api_key_id}/revoke`. Listing supports
UUIDv7 cursor pagination plus `enabled` and `scope` filters. Revocation is
idempotent, immediately blocks authentication, and writes one audit event.

## Job API

Authenticated clients use these job endpoints:

- `POST /v1/jobs` submits a job.
- `GET /v1/jobs` lists visible jobs using UUIDv7 cursor pagination and optional
  `status`, `type`, and `priority` filters.
- `GET /v1/jobs/{job_id}` returns one job.
- `GET /v1/jobs/{job_id}/result` retrieves its result transparently from
  PostgreSQL or object storage.
- `GET /v1/jobs/{job_id}/attempts` returns its execution attempts in attempt order.
- `GET /v1/jobs/{job_id}/transitions` returns its state-change timeline in
  chronological order.
- `POST /v1/jobs/{job_id}/cancel` cancels queued work or requests cooperative
  cancellation from its worker.
- `POST /v1/jobs/{job_id}/replay` creates a linked job from a dead-lettered job.

`GET /v1/handlers` returns the deployed handler catalog, including every
version's active state, execution mode, timeout, default attempt limit, and JSON
Schema payload contract.

Set `limit` from 1 to 100 on the list endpoint. When `next_cursor` is present in
the response, pass it as `cursor` to fetch the next page.

## Security audit log

Forge writes API-key creation, cancellation requests, and dead-letter replays to
an append-only `audit_events` table in the same transaction as each action. The
records retain the actor's public API-key identifier but never store credentials
or secrets.

Administrators query the log through `GET /v1/admin/audit-events`. The endpoint
supports `action`, `resource_type`, and `actor_public_id` filters plus `limit`
and identity-based cursor pagination.

## Operational API

Dashboard data is available to administrator keys through three read-only
endpoints:

- `GET /v1/admin/operations/summary` returns job-state totals, durable
  outstanding work, active workers, and queue totals.
- `GET /v1/admin/operations/queues` compares PostgreSQL's durable outstanding
  count with Redis priority and worker-processing queue depths.
- `GET /v1/admin/operations/workers` reports live heartbeat TTL, active database
  attempts, and reserved Redis messages for each worker.
- `GET /v1/admin/operations/performance?window_minutes=60` reports throughput,
  outcomes, retries, and p50/p95 queue-wait, execution-time, and scheduling-lag
  distributions for a bounded time window.
- `GET /v1/admin/operations/metrics` exposes low-cardinality Prometheus text
  gauges and requires the same administrator authentication.
- `GET /v1/admin/operations/events` streams job transitions as authenticated
  server-sent events. Pass `after=<transition-id>` to resume after a disconnect.

HTTP requests accept and return W3C `traceparent`. Forge carries the trace ID
through the durable outbox and Redis message, then binds a new worker span so
API, dispatcher, and worker JSON logs can be correlated without storing request
bodies or credentials.

These values are operational snapshots taken across PostgreSQL and Redis. A
temporary difference is expected while dispatch, acknowledgement, or recovery
is in progress.
