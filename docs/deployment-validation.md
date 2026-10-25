# Local deployment validation

Forge has one deployment acceptance command for the complete local Linux
topology:

```powershell
uv run python scripts/validate_deployment.py
```

The first run builds the Forge image, starts the validation and observability
services, and can take several minutes. The command then:

1. waits for PostgreSQL, Redis, and object storage readiness;
2. verifies or rotates the ignored local administrator credential;
3. creates a one-hour, high-quota API key for this run;
4. submits jobs with k6 at 10 requests per second for 25 seconds;
5. finds only those jobs through the API and waits for every one to finish;
6. confirms the queues are empty, both workers are online, and all required
   services are still running;
7. runs the migration, dependency, handler, and database-integrity checks; and
8. revokes the temporary load-test key.

The command exits with code zero only when the requested arrival count was
accepted, every accepted job succeeded, the k6 thresholds passed, no scheduled
iteration was dropped, and all health and integrity checks passed. k6 can
occasionally start one iteration on the duration boundary; its configured error
threshold still applies, while the planned count remains `rate * duration`.

The machine-readable report is written to
`tests/reports/deployment-validation.json`. The k6 report is written to
`load/results/deployment-validation-k6.json`. Both locations are ignored by Git
because results are meaningful only with their machine and deployment context.
Neither report contains an API credential.

When the stack is already running and the Forge image is current, skip the build
and startup step:

```powershell
uv run python scripts/validate_deployment.py --skip-start
```

The workload and timeouts are configurable:

```powershell
uv run python scripts/validate_deployment.py `
  --rate 20 `
  --duration 1m `
  --drain-timeout 300
```

Increasing the rate changes the claim being tested. Record the hardware,
process counts, database-pool settings, and full result before presenting a run
as capacity evidence.

## Dependency outage checks

Run both controlled dependency experiments with:

```powershell
uv run python scripts/validate_dependency_outages.py
```

Add `--skip-start` to use the running validation stack. The runner requires an
idle stack because it temporarily stops PostgreSQL and object storage. It uses
the local `forge-validation` Compose project and preserves its data volumes.

The PostgreSQL scenario accepts three long-running jobs, observes a running
attempt, stops PostgreSQL for at least five seconds, and checks three failed
submissions. Each must return `503 dependency_unavailable`, identify PostgreSQL,
include `Retry-After`, and finish within five seconds. After restoration, the
rejected request is retried with its original idempotency key and all accepted
jobs must succeed. Every Forge process must retain its container ID, start
time, and restart count.

After readiness returns, API replicas can still need time to replace stale
database connections. The runner retries recoverable submission 503 responses
for up to 30 seconds, always preserving the idempotency key, and records every
response. An unexpected status or failure to recover still fails the test.

The object-storage scenario temporarily applies the blob-validation overlay
with a 128-byte inline limit. While storage is unavailable:

- a payload over the limit must receive a bounded, retryable dependency error
  and leave no job behind;
- a payload below the limit must still be accepted; and
- its larger result must record an `object_store_unavailable` attempt.

The accepted job requests only one handler attempt. After storage returns, the
job must succeed, the result must be stored externally and retrieved correctly,
and the rejected payload must be accepted when retried. Forge processes must
again remain running without restarting during the outage.

Each dependency is restarted in a `finally` block. After the storage experiment,
the runner restores normal inline limits, revokes its one-hour test key, and
runs startup and integrity checks. Intentional container recreation to apply
and remove the blob overlay is excluded from outage restart assertions.

The report is saved to `tests/reports/dependency-outages.json` and the process
returns zero only when every scenario and cleanup check passes. These are
deterministic recovery checks with a handful of jobs; they complement the load
benchmark and do not establish throughput during an outage.
