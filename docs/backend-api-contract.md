# Backend API contract for the dashboard

The dashboard uses `docs/openapi.json` as the reviewed backend contract.
Contract tests compare it with the running application definition. Changes
require an intentional export, review and passing contract tests.

Authentication stays `Authorization: Bearer <opaque API key>`. Keys are not
JWTs. Read/write scopes apply to owned jobs; administrator operations require
the admin scope. A browser must not contain a hardcoded administrator token.

Existing job submission, listing, result, attempts, transitions, cancellation,
dead-letter replay, key administration, queue, worker, summary, metrics and
SSE routes support the agreed dashboard. No dashboard-only second backend is
required.

The performance response now also contains `claim_time` and `end_to_end_time`,
with the same sample-count/p50/p95 structure as other distributions.
No timing sample means nullable percentiles, not zero latency. Their definitions
and retry caveats live in `docs/backend-acceptance.md`.

Public job IDs are UUIDv7. Transition cursors are database identity values.
Scope checks determine access; possessing an identifier grants no access.
Submission retries preserve the original idempotency key and request body.
Replay creates a new linked job; terminal jobs remain immutable.

Display structured error codes and retry instructions from API responses.
Distinguish key throttling from system backpressure. Cancellation can be
pending while a handler cooperates; it does not promise to undo side effects.
Use attempt/transition history to explain retries and worker recovery.

SSE connections can be resumed with their cursor and use polling fallback.
Keep secrets out of URLs and logs. The implemented dashboard keeps credentials
in memory, clears caches on disconnect/401, and uses a same-origin API proxy.
`GET /v1/auth/me` exposes the current key's public metadata and scopes without
its credential or digest. See ADR-022 and `docs/dashboard.md`.

The contract is a development baseline, not a pledge never to evolve it.
Schema evolution must be explicit; no silent endpoint redesign is planned
during the initial dashboard implementation.
