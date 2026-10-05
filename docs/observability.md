# Local observability

Forge's local observability stack runs beside the Linux validation topology. It
contains Prometheus for metrics, Grafana for dashboards, and Tempo for traces.
The API and workers export OTLP traces directly to Tempo. Prometheus scrapes the
authenticated operations endpoint through the same proxy used by clients.

Start the stack from the repository root:

```powershell
uv run python scripts/start_observability.py
```

To start the same topology and immediately prove that submission, execution,
drain, service health, and database integrity all work together, run:

```powershell
uv run python scripts/validate_deployment.py
```

See `docs/deployment-validation.md` for the exact pass criteria and options.

The script starts the validation dependencies, issues a dedicated administrator
API key for Prometheus, stores that credential in the ignored
`deploy/observability/metrics-token` file, and starts all Forge and observability
services. Use `--rotate-token` after resetting the validation database or when a
new scrape credential is required.

Open these local endpoints:

- Forge API proxy: `http://127.0.0.1:18080`
- Forge Grafana dashboard: `http://127.0.0.1:13000/d/forge-overview`
- Prometheus: `http://127.0.0.1:19090`
- Tempo readiness check: `http://127.0.0.1:13200/ready`

Tempo is an API service and has no web page at its root URL, so
`http://127.0.0.1:13200/` returns `404`. View and search traces through the Tempo
data source in Grafana.

Grafana allows anonymous local read-only access. That setting belongs only to
this validation overlay. The production Compose file does not expose Grafana or
enable anonymous access.

The five-minute rate contract includes runnable queue entries per minute,
terminal completions per minute, and the ratio of failed or dead-lettered jobs
to non-cancelled completions. Snapshot gauges cover current dead-letter volume,
queue depth, worker health, expired leases, and permit utilization for the
configured job types. The configured job-type names are the only labels added by
the permit metrics, which keeps cardinality bounded.

Stop the stack without deleting its data:

```powershell
docker compose -f deploy/compose.validation.yaml -f deploy/compose.observability.yaml down
```
