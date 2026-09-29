# Forge Technology Stack

Status: Accepted design; application scaffolding has not started.

## Backend

| Concern | Choice | Role |
|---|---|---|
| Language | Python 3.14 | API, coordination processes, and handlers |
| Package management | uv | Locked dependencies and project commands |
| HTTP API | FastAPI | Authenticated REST API, health, metrics, and SSE |
| Validation | Pydantic | API configuration and registered payload schemas |
| Database access | SQLAlchemy 2 async and asyncpg | Routine mapping plus explicit concurrency SQL |
| Schema changes | Alembic | Reviewed, ordered PostgreSQL migrations |
| Queue coordination | redis-py asyncio | Queues, permits, rate limits, and atomic Lua protocols |

## Worker execution

Each worker is an asyncio-driven process. Registered handlers explicitly select
async-task, bounded-thread-pool, or bounded-process-pool execution. PostgreSQL
attempt leases and Redis concurrency permits are renewed independently of handler
execution.

## Dashboard

| Concern | Choice |
|---|---|
| UI | React and TypeScript |
| Build tool | Vite |
| Routing | React Router |
| Server state | TanStack Query |
| Styling | Tailwind CSS |
| Charts | Recharts |
| Live updates | Server-Sent Events with polling fallback |
| Package manager | npm |

## Testing and evidence

| Evidence | Tooling |
|---|---|
| Unit/application behavior | pytest and pytest-asyncio |
| Lifecycle invariants | Hypothesis stateful testing |
| PostgreSQL and Redis semantics | Testcontainers |
| API contracts | HTTPX |
| Failure recovery | Controlled process/container termination |
| Dashboard components | Vitest and Testing Library |
| Browser flows | Playwright |
| Load and latency | k6 |

## Observability

- structlog emits correlated JSON logs.
- prometheus-client exposes low-cardinality metrics.
- OpenTelemetry emits traces through OTLP.
- Prometheus, Grafana, and Tempo provide the local observability environment.

## Repository shape

```text
forge/
├── src/forge/
│   ├── api/
│   ├── application/
│   ├── domain/
│   ├── persistence/
│   ├── queue/
│   ├── handlers/
│   ├── observability/
│   ├── security/
│   └── processes/
├── dashboard/
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── failure/
│   └── contract/
├── load/k6/
├── deploy/
├── scripts/
└── docs/
```

The API, worker, dispatcher, scheduler, and recovery loop share domain and
application code but run as separate processes. The dashboard consumes the same
documented API as any other client.

## Quality tooling

- Ruff handles Python linting and formatting.
- mypy checks Python types.
- ESLint and Prettier check dashboard code.
- Pre-commit hooks run quick local checks.

Exact dependency versions will be selected and locked when scaffolding begins.
