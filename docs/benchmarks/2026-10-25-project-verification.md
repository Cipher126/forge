# Project completion verification — 2026-10-25

This records the feature-completion batch separately from the earlier backend
endurance benchmarks. It does not replace their measurements or claim that the
original 1,000-enqueue/sec and 50 ms enqueue-to-claim targets now pass.

## Implemented product

The accepted React/TypeScript dashboard now lives in `dashboard/`, using Vite,
React Router, TanStack Query, Tailwind CSS and Recharts. It consumes the actual
Forge API; browser fixtures exist only in test code.

The views cover operations, queues, workers, filterable/paginated jobs,
submission/scheduling, attempts, transition timelines, stored results,
cooperative cancellation, linked dead-letter replay, deployed handlers,
scoped API-key creation/revocation and audit history.

Keys remain in memory, with public identity/scopes retrieved through
`GET /v1/auth/me`. No credential is embedded in the bundle or stored in browser
storage. Disconnect clears query/mutation caches; HTTP 401 also disconnects.
An authenticated fetch stream supports cursor reconnection, batched refreshes
and polling fallback. An initial SSE comment flushes an idle connection promptly.
The generated TypeScript contract comes from the reviewed OpenAPI schema.

## Verification

- The full backend suite passed **99 tests**, with zero skips, against separate
  real PostgreSQL, Redis and S3-compatible test dependencies. The expanded key
  test covers writer identity without digest/credential disclosure and revoked
  identity rejection. Four targeted integration/contract tests passed after the
  initial SSE greeting change.
- Ruff passed. Strict mypy passed the backend and new start/validation scripts.
- The TypeScript production build passed with page-level lazy loading. The
  charting page is a separate bundle from the connection screen.
- **12 component/API tests** cover bearer-header placement, no cookies/cache,
  explicit retry metadata, invalid proxy responses, revoked sessions, SSE chunk
  parsing, empty measurements, and rendering untrusted text safely.
- **Nine mocked Chromium browser tests** passed: connect/reload, submission and
  result navigation, server-side filters, read-scope restrictions, one-time key
  display, failed login, mobile width, cancellation confirmation and replay
  retry with one preserved idempotency key.
- A **real Chromium browser test against the Docker dashboard at port 13010**
  passed. It connected through the same-origin proxy, established SSE,
  submitted a unique report, observed a successful attempt, verified the stored
  result, inspected workers, scheduled and cancelled another job, and
  disconnected. Its expiring administrator test key was revoked afterward.
  Trace recording was disabled to avoid recording the bearer header.
- Both the backend and dashboard images built, and the dashboard health check
  passed. The dashboard runs as non-root with a read-only filesystem and a
  temporary runtime directory. Production Compose includes the same UI service.
- Desktop and narrow-screen screenshots were captured for local review. Visual
  inspection caught label encoding and short-viewport navigation issues; those
  were corrected. Generated reports and screenshots remain ignored.

The repeatable live browser command is:

```powershell
uv run python scripts/validate_dashboard.py
```

See [the dashboard guide](../dashboard.md) for prerequisites and every screen.
The GitHub Actions workflow implements backend integration checks and frontend
build/component/mocked-browser checks. A checked-in workflow is not by itself
evidence of a successful hosted run; consult the repository's Actions result.

## Host recovery

Docker Desktop initially failed before Forge could start, reporting a stale
`dockerInference` socket. With Desktop stopped, only its two local IPC
directories were renamed aside and recreated. Docker then started successfully
with the existing services and stored jobs intact. No VM disk, container volume,
database or Docker settings reset was performed.

## Boundaries

The earlier 100,000-job and separate 5,000-job evidence remains dated
2026-10-05 and tied to its recorded images. These jobs were not rerun or silently
relabelled as a 2026-10-25 performance result. The new browser test adds real
full-stack product evidence with a small workload.

Public hosting, DNS/TLS, multi-host failover and production backup restoration
still depend on the selected deployment environment. The original throughput
and enqueue-to-claim targets remain unmet. This batch completes the functional
portfolio implementation and local packaging, not those outstanding claims.
