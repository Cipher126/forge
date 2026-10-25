# Automated dependency outage validation — 2026-10-05

The runner was executed against the local Linux validation stack on Docker
Desktop, with two API replicas and two workers. The backend checkpoint was
`4a6bbe1`; the outage runner was an uncommitted addition on top of it.

```powershell
uv run python scripts/validate_dependency_outages.py --skip-start
```

## PostgreSQL

Three 12-second jobs were accepted before PostgreSQL was stopped. The runner
observed a running attempt, stopped the database, and kept it unavailable for
five seconds. Three additional submission probes received
`503 dependency_unavailable`, identified PostgreSQL, and included `Retry-After`.
Their measured response times were 516, 516, and 508 ms.

After database restoration, the first rejected submission was retried using its
original idempotency key and received HTTP 202. All four accepted jobs reached
`succeeded`. The seven Forge containers retained their IDs, process start times,
and restart counts throughout the outage.

## Object storage

The runner temporarily applied a 128-byte inline limit to the APIs and workers.
It stopped object storage before sending an oversized report payload. The API
returned `503 dependency_unavailable` after 3.037 seconds and left no persisted
job for that request.

A second report payload fit inline, so Forge accepted it during the outage. Its
result required external storage. The submitter allowed one handler attempt;
five attempts recorded `temporary/object_store_unavailable` failures while
storage and its client connections recovered. Attempt six succeeded. The
effective allowance grew from one to six because infrastructure failures do
not consume the handler budget. The runner retrieved the external result and
verified its report identifier.

The initially rejected blob submission also succeeded when retried after
storage returned. Forge container identity, process start time, and restart
count remained unchanged during the outage. Container recreation used to apply
and remove the blob test profile was intentional and outside that comparison.

## Restoration and scope

The dependencies were restored, normal inline limits reinstated, and the
temporary credential revoked. All 14 services were running, startup checks
passed, and all five database integrity counts were zero.

The ignored machine-readable report is `tests/reports/dependency-outages.json`.
These experiments prove bounded rejection and eventual accepted-job recovery
at this tested scale. They use a handful of controlled jobs and do not measure
maximum throughput, combined dependency outages, or exactly-once external side
effects.

## Repeatability check

A second invocation exposed a recovery timing race in the runner: readiness
through one API replica passed while a submission through the other still
encountered a stale PostgreSQL connection. All accepted jobs completed and
cleanup passed, but the runner incorrectly required the first post-readiness
submission to succeed immediately.

The runner now allows up to 30 seconds for bounded, recoverable 503 responses,
preserves the original idempotency key on every retry, and records all recovery
probes. Unexpected errors and recovery deadline exhaustion still fail the run.

The corrected repeat passed every scenario. PostgreSQL rejection times were
507–509 ms; object storage rejected the missing payload write in 3.035 seconds.
The result job again survived five storage failures before succeeding on attempt
six. All four PostgreSQL scenario jobs and both storage scenario jobs succeeded.
The final report confirmed zero queued, processing, and outstanding jobs, two
online workers, all 14 services running, revoked credentials, normal settings,
and zero integrity violations.

## Shutdown handshake regression

A later repeat on a rebuilt image caught a different failure: a connection
attempt during PostgreSQL shutdown raised raw `asyncpg.CannotConnectNowError`.
SQLAlchemy had not yet adapted that handshake error into its usual database
exception. Dispatcher, scheduler and recovery could therefore exit despite their
existing retry handling. Accepted work completed after restart, but the runner
correctly failed its stronger process-survival assertion.

The shared retry family now includes raw driver connection, shutdown and
connection-limit errors. API handlers return the existing sanitized 503 contract;
background processes back off and reconnect. Maintenance also retries bounded
database/object-store availability failures. Targeted API, process-loop and
worker-reservation tests reproduce these paths. The updated suite passed 99 tests.

The corrected real repeat completed at 15:20:59 UTC. PostgreSQL rejected probes
in 514–518 ms, all four accepted jobs succeeded, and storage rejected a missing
payload write in 3.063 seconds. The external result again succeeded on attempt
six after five storage failures. All seven Forge processes retained container
identity, start time and restart count during both scenarios. Cleanup, key
revocation, queue drain, startup, all 14 services and integrity checks passed.
