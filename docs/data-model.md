# Forge Initial Data Model

Status: Implemented by the initial Alembic migration.

## Identifier policy

| Table | Primary identifier | Reason |
|---|---|---|
| `api_keys` | UUIDv7 | Administrative domain entity |
| `jobs` | UUIDv7 | Public and cross-process identifier |
| `job_attempts` | UUIDv7 | Ownership and tracing across workers |
| `outbox_events` | UUIDv7 | Travels through the delivery path |
| `job_transitions` | bigint identity | Internal append-only history |
| `idempotency_keys` | bigint identity | Internal row addressed by a business key |

## API keys

`api_keys` stores a public key identifier, HMAC secret digest and pepper version,
name, scopes, enabled and expiry state, rate policy, and audit timestamps. Forge
never stores or returns the original secret after creation.

The first administrator key is created through a trusted CLI. Later keys can be
created by an administrator through the documented API and dashboard. Key
creation, scope changes, rotation, and revocation are audited.

## Jobs

`jobs` stores the submitting key, job type, handler version, status, priority,
inline or blob payload, attempt limits and count, active attempt, schedule and
lifecycle timestamps, inline or blob result, and an optional self-reference to
the dead-lettered job from which a replay was created.

Constraints enforce positive attempt limits, a valid attempt count, exactly one
payload location, at most one result location, valid checked-text states and
priorities, and a schedule timestamp for scheduled states.

## Idempotency keys

`idempotency_keys` has a unique `(api_key_id, idempotency_key)` pair, normalized
request fingerprint, associated job, creation time, and expiry. It is separate
from `jobs` so its TTL can expire without deleting or mutating retained job
history.

## Attempts

`job_attempts` stores the job, attempt number, state, worker, unique ownership
token, lease expiry, execution timestamps, and bounded failure information.
`(job_id, attempt_number)` is unique. Full stack traces belong in correlated
logs rather than unbounded database columns.

## Transitions

`job_transitions` is append-only and records the job, optional attempt, previous
and next state, reason, actor, bounded metadata, and database timestamp. A status
update and transition insert share one transaction.

## Outbox events

`outbox_events` stores event and aggregate identity, bounded payload,
availability, dispatch count, dispatch lease, publication time, last bounded
error, and creation time.

Dispatch uses a short database transaction to claim a batch, commits before
contacting Redis, publishes, then records success. An expired dispatch lease is
recoverable. A crash after Redis publication and before the success update may
publish twice, which is compatible with Forge's at-least-once contract.

## Job lifecycle

```text
submission ──> queued ──> running ──> succeeded
     │                       ├──────> failed
     │                       ├──────> retry_scheduled ──> queued
     │                       └──────> dead_lettered
     └──────> scheduled ───────────────────────────────> queued

queued / scheduled / retry_scheduled ──> cancelled
running ──> cancellation_requested ──> cancelled / succeeded / failed
```

`succeeded`, `failed`, `dead_lettered`, and `cancelled` are terminal. Publishing
to Redis is not a job-state transition: `queued` means durably eligible for
execution, even while Redis is unavailable.

## Initial query indexes

- Jobs by status and creation time
- Jobs by type, status, and creation time
- Due scheduled and retry jobs by status and `run_at`
- Attempts by job and attempt number
- Active attempt leases by expiry
- Idempotency records by expiry
- Unpublished outbox events by availability

Exact SQL, index predicates, foreign-key deletion rules, timestamp defaults, and
bounded field sizes will be reviewed in the first Alembic migration.

## Retention and archival

The maintenance process removes expired idempotency records independently of
job retention. Terminal jobs older than the configured retention window are
serialized with their attempts and transitions to
`archives/jobs/<job-id>.json`, then deleted from PostgreSQL. A job remains in
PostgreSQL while another job names it as a replay source so the replay lineage
foreign key remains valid.

Payload and result objects that have no live job reference are deleted only
after a configurable grace period. The delay protects objects uploaded shortly
before a failed or unfinished database transaction. Archive objects use a
separate prefix and are outside this reconciliation pass.
