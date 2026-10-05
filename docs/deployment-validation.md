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
