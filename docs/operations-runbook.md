# Operating Forge

PostgreSQL holds accepted jobs, ownership, results or object references, retries,
and transition history. Redis is replaceable delivery and coordination state.
Object storage holds large payloads, large results and retention archives.
Keep PostgreSQL and object backups together. A healthy Redis alone does not
mean accepted work is safe.

## Start and release

Use an immutable image tag for a release and a private `.env.production`.
Keep development credentials out of production. Supply PostgreSQL, Redis and
S3-compatible storage externally; the Compose file does not provision them.
Place the API behind the chosen TLS proxy and keep dependency ports private.

```powershell
docker compose -f deploy/compose.production.yaml build
docker compose -f deploy/compose.production.yaml up -d
docker compose -f deploy/compose.production.yaml exec -T api python scripts/check_startup.py
docker compose -f deploy/compose.production.yaml exec -T api python scripts/validate_integrity.py
```

Every process waits for the migration service to exit successfully. Failed
migrations must block a release. Review generated migrations: schema changes
that remove fields or constraints can require a staged deployment. Running
`alembic downgrade` is not a general data rollback strategy.

For a release on an existing installation, stop the public submission route
temporarily, drain workers, run the reviewed migration, then replace processes.
This single-host Compose setup does not promise zero-downtime rolling releases.
Keep every handler version referenced by outstanding jobs deployed until those
jobs drain. Missing exact versions fail permanently; substituting a version
silently could change a submitted job's meaning.

Each process owns its database pool. Budget maximum connections as
`(pool size + overflow) * process count`, with extra room for migrations,
monitoring and administration. More worker containers cannot overcome a global
job-type permit cap without deliberately changing that cap.

## Routine checks

- `/health` answers whether the API process is alive.
- `/ready` checks its dependencies. Check it after releases and restoration.
- The authenticated operations summary shows durable outstanding jobs,
  queues and live worker heartbeats.
- Growing outstanding work with stable completions usually means incoming work
  exceeds execution capacity. Compare queue wait with claim and execution time.
- Expired leases, unexpected attempt failures and an unpublished outbox that
  keeps growing require investigation. A retry spike alone can be temporary.
- A 429 means the calling key's request quota was exhausted. A 503 can mean
  admission backpressure or a dependency failure; inspect the response code.
  Retry with the same idempotency key and body and respect `Retry-After`.

PostgreSQL transitions and the SSE cursor are the durable audit trail.
`job_transition_committed` JSON logs are emitted after the outer commit.
Rolled-back transactions and savepoints emit no committed log. There is a
crash window between commit and logging, and a broken log sink must not make
committed requests fail. Use PostgreSQL transitions for an exact job history.
Tokens, payloads, results and ownership tokens are excluded from these
transition log records.

For local monitoring, run `scripts/start_observability.py` and open Grafana at
`http://127.0.0.1:13000/d/forge-overview`. Prometheus collects counters and gauges;
Grafana displays them; Tempo stores the path a request/job followed.
Tempo's root URL is not a dashboard. Use Grafana Explore with the Tempo source.
Choose a recent time range and automatic refresh while work is running.

## Dependency failure

**PostgreSQL:** submissions fail with bounded retryable errors. Processes back
off and reconnect. Restore the database, wait for readiness and stable reads,
then verify outstanding jobs and attempt ownership. Stale pooled connections
can fail briefly after the database becomes ready. Retry submission with its
original idempotency key; an accepted job may already exist.

**Redis:** PostgreSQL still holds work. Restore Redis, let reservation/lease
recovery and delivery reconciliation run, and wait for queues to drain. Do not
hand-insert queue messages or change attempt tokens. Lease expiry may cause
re-execution, so handlers must protect external side effects themselves.

**Object storage:** large submissions are rejected without committing a job
when the object cannot be written. Inline submissions can still be accepted.
An accepted job's result-storage failure is recorded as an infrastructure
attempt and retried without consuming its handler retry allowance. Restore the
store and inspect retry traffic. A prolonged outage leaves work outstanding;
it does not turn missing objects into successful results.

**Worker death:** another worker recovers expired ownership and runs a new
attempt. Cancellation cannot undo an external action already performed.
Inspect the job's attempt timeline and the downstream system before replaying
work with irreversible side effects.

After restoration, run both container checks above and verify that previously
accepted jobs reach terminal states. The local fault commands in
`docs/backend-acceptance.md` demonstrate these paths on disposable validation
services; do not point them at production.

## Backups and restoration

Schedule PostgreSQL backups using the database provider or `pg_dump` in custom
format, and test restoring to a **separate** database with `pg_restore`.
Record the schema revision, backup time, image tag, retained handler versions,
API-key pepper and object-store snapshot/version set. Store secret material in
the environment's secret manager; it must not enter Git or benchmark reports.
Retention archives are evidence exports, not complete database backups.
Archive schema 2 includes the actual payload and result, including data read
from external objects before the live row is deleted. Original blob keys remain
as provenance; the archive survives later orphan collection. Failure to read or
write storage aborts deletion. Older schema 1 archives may contain only blob
references and depend on the referenced objects still being available.
Archival waits until active submission idempotency keys expire. Configuring
a deduplication window longer than job retention therefore keeps the live job
longer, rather than silently cutting short the promised deduplication window.

For a restore drill:

1. Keep the restored instance isolated from production clients and dependencies.
2. Restore PostgreSQL and the matching referenced objects. Keep the original
   installation stopped if restoring back into its environment.
3. Use a fresh empty Redis database, the recorded pepper and compatible handlers.
4. Apply only migrations needed for the selected image, then start recovery,
   dispatcher and workers. Expect expired attempts to be abandoned and eligible
   queued deliveries to be reconstructed.
5. Run startup/integrity checks and inspect results and object references.
6. Check downstream idempotency before admitting traffic. Restoring an older
   database can repeat a side effect that happened after the backup.

Redis persistence shortens recovery but is not the sole accepted-job backup.
Do not restore stale Redis reservations as authoritative ownership.
Measured restore time and backup age determine the environment's recovery
time and data-loss window; this repository has not claimed production values.

## Keys and shutdown

Bootstrap an administrator key with `forge key create --scope admin` on a trusted
process. It is printed once; store it securely. Admin API routes create, list
and revoke later scoped keys. Provision a replacement, update the client,
verify it, then revoke the old key.

Changing the HMAC pepper invalidates existing digests. The current implementation
supports one active pepper; changing it requires issuing replacement credentials.
The `pepper_version` column is not a working multi-pepper rotation mechanism.
Coordinate rotation with clients rather than assuming old keys will survive it.

On SIGTERM workers stop reserving and drain active jobs up to the configured
90-second default. Container grace is two minutes. Jobs exceeding the drain
deadline are left for lease recovery. A hard kill requires the same recovery
and may repeat external work.
