# Forge dashboard

The dashboard is a React/TypeScript client of the same API used by scripts and
other integrations. It has no embedded administrator key and no sample-data mode.
Every visible job, result, worker, audit record and metric comes from Forge.

## Start the complete local workspace

Docker Desktop's Linux engine must be running. From the repository root:

```powershell
uv sync --frozen
uv run python scripts/start_forge.py
```

Open **http://127.0.0.1:13010**. This starts the existing isolated validation
topology, monitoring and the dashboard; it does not delete databases or rerun
the long endurance benchmark. It builds the dashboard into a read-only,
non-root Nginx container. For a dedicated operator credential:

```powershell
docker compose -f deploy/compose.validation.yaml -f deploy/compose.observability.yaml exec -T api-1 forge key create --name dashboard-operator --scope admin --rate-limit-requests 1000
```

Paste the resulting key into the connection form. Keys are printed once. Keep
them out of Git, screenshots and recordings. Administrator keys can inspect all
jobs and administer credentials. Read keys inspect only jobs their key owns;
write keys can submit work. An admin can create scoped credentials in API keys.

## What each screen means

- **Overview:** outstanding durable jobs, terminal completions in the selected
  window, recent worker heartbeats, latency percentiles and queue distribution.
  The queue chart is a current snapshot, not an invented historical time series.
- **Jobs:** filters are executed by the API, with cursor pagination. Open a job
  to see attempts, transition history and its stored result. Large results are
  retrieved through the result endpoint.
- **Submit job:** use a registered payload schema, choose priority and optionally
  schedule a local date/time. Dates are converted to an explicit UTC instant.
  Retrying uncertain failures preserves the idempotency key and exact body.
- **Workers & queues:** heartbeat presence differs from database attempt ownership.
  A missing worker can leave an active lease that must expire before recovery.
  Redis message counts can differ from durable job counts because delivery is
  at least once.
- **Handlers:** deployed versions, execution mode, timeout and payload JSON schema.
- **API keys:** issue scoped keys, display each new secret once, and revoke keys
  with explicit confirmation. Revoking the current key disconnects the session.
- **Audit history:** sensitive operations and their actors/resources. Job
  execution history lives in the job transition timeline instead.

Queued cancellation is immediate. Running cancellation is cooperative and cannot
undo an external effect. Dead-letter replay creates a new linked job; check
downstream effects before confirming it. The demo report handler returns JSON
and a suggested path; it does not generate a report file.

## Connection and update decisions

`GET /v1/auth/me` validates a key and returns only its public identity, scopes,
expiry and quota metadata. It never returns the credential or stored digest.
The key stays in React memory, not local/session storage, cookies, URLs or Git.
Reloading requires reconnecting. Disconnect clears all query and mutation caches,
including one-time secrets. HTTP 401 also disconnects the session.

Both Vite and container deployment use a same-origin `/api` reverse proxy. The
proxy forwards the caller's bearer header; it holds no server-side Forge key.
No wildcard CORS permission is needed. Place a trusted TLS reverse proxy in front
of the dashboard for non-loopback deployment.

Admin sessions consume SSE with authenticated `fetch`, because native EventSource
cannot attach the bearer header. The client retains the transition cursor for
reconnection and falls back to polling during stream failure. Live refreshes are
batched at 15 seconds to avoid a request storm during large runs. Background-tab
polling pauses. A failed stream is labeled as polling/reconnecting, not live.
No samples display as a dash; they never become zero latency.

## Work on the frontend

```powershell
cd dashboard
npm ci
npm run dev
```

Vite serves http://127.0.0.1:5173 and proxies `/api` to the local validation API
at port 18080. Set `FORGE_API_PROXY_TARGET` to use another trusted local backend.
Never point the key form at an untrusted proxy.

```powershell
npm run generate:api
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

To verify the real browser → proxy → API → worker → stored-result path, with
the complete stack running:

```powershell
# From the repository root, after installing the dashboard's test browser:
uv run python scripts/validate_dashboard.py
```

This creates an expiring administrator test key in memory and revokes it after
the browser run. It checks live connection, submission, stored result, workers,
scheduling, cancellation and disconnect. Browser traces are disabled in this
test to keep credentials out of recording artifacts. Its local report and
review screenshots are ignored under `tests/reports/`.

API types are generated from the reviewed `docs/openapi.json`. CI regenerates
them and rejects drift, then runs TypeScript/build, component tests and mocked
browser interaction tests. Backend CI runs the real PostgreSQL/Redis/object-store
integration suite. Browser mocks test UI contracts; they are not production or
live-stack evidence. Dated verification documents distinguish the two.

The production Compose file includes the same dashboard and proxy. Supply its
external PostgreSQL, Redis and S3 dependencies and private environment file as
described in the operations runbook. No public server or DNS is provisioned by
these local commands.
