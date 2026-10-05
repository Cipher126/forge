# Observability load demonstration — 2026-10-02

## Purpose

Exercise Forge with roughly 5,000 unique jobs while Prometheus, Grafana, and
Tempo observe the validation deployment. This was a demonstration workload and
an overload investigation, not a claim about maximum production capacity.

The deployment used two single-process API replicas behind Nginx, two workers
with concurrency four, PostgreSQL, Redis, and the object store. Each API replica
used a PostgreSQL pool of five persistent connections plus five overflow
connections.

## Initial saturation run

The first run attempted exactly 5,000 submissions at 50 requests per second for
100 seconds. Every payload and idempotency key was unique. Every fiftieth
submission used `demo.wait` for two seconds; the rest used
`demo.generate_report`.

- 3,926 requests were accepted with HTTP 202.
- 1,074 requests failed: 69 HTTP 500, 993 HTTP 502, and 12 HTTP 503.
- No k6 iterations were dropped.
- Submission latency was 159.3 ms on average and 1,221.1 ms at p95.
- Every accepted job later succeeded.

The API logs showed SQLAlchemy pool timeouts after all available PostgreSQL
connections were occupied. SQLAlchemy's pool `TimeoutError` was missing from
the API dependency handlers, so 69 pool timeouts escaped as HTTP 500. Four
upstream connection resets caused Nginx's passive health checks to mark both API
replicas unavailable; 992 later requests received `no live upstreams` responses.

Forge now maps SQLAlchemy pool timeouts to the existing sanitized HTTP 503
`dependency_unavailable` response with `Retry-After: 1`. A focused unit test
protects that contract. A 500-request check at the same 50 requests per second
then completed with 500 accepted requests, no HTTP failures, no dropped
iterations, and 213.0 ms p95 latency.

As a follow-up hardening step, each API process now has an in-memory submission
gate that runs before API-key authentication. The default admits six
simultaneous submission or replay requests against the ten-connection API pool.
Requests above that limit receive a retryable, explicit HTTP 503 instead of
waiting until the database pool times out.

### Admission burst follow-up

The first follow-up burst at 400 requests per second exposed a separate proxy
limit: Nginx allowed only 512 worker connections. Because a proxied request uses
both a client connection and an upstream connection, the proxy rejected work
before the API could apply admission. The validation proxy now allows 4,096
worker connections.

Admission was also moved from a route dependency to middleware that runs before
request-body loading and authentication. In a later 100-request-per-second run:

- 1,001 submissions were scheduled by k6; 960 executed and 41 were dropped by
  the load generator because its virtual users were occupied;
- 30 requests were accepted;
- 897 requests received `503 submission_capacity_exceeded` from the API gate;
- 33 requests received the sanitized PostgreSQL dependency `503`;
- no request received HTTP 500 or 502, and Nginx reported no connection-limit or
  upstream-reset error; and
- every container remained running after the test.

The remaining dependency responses were not SQLAlchemy pool timeouts.
PostgreSQL logged client connection resets during the same window, after Docker
Desktop had already stopped the entire stack under memory pressure during the
earlier extreme bursts. This local result demonstrates controlled application
backpressure but is not a passing 100-request-per-second capacity claim.

The gate was then checked against sustained traffic after the repeated overload
runs. With five slots per API process at 20 requests per second, 396 of 501
requests were accepted and 105 were safely rejected. The database reported no
pool timeout or connection error. Five slots were therefore too conservative.

The final default is six slots per API process, leaving four of each process's
ten database connections available to other endpoints. By this point the local
Docker session was materially slower: 20 sequential submissions averaged 211.8
ms each, compared with the earlier clean run's 28.0 ms average. At 20 requests
per second, the six-slot gate accepted 305 of 499 executed requests, rejected
190 with `submission_capacity_exceeded`, and exposed four sanitized dependency
503 responses. There were no pool timeouts or connection resets. This is a
controlled overload result, not a new 20-request-per-second capacity claim.

A final control run at 10 requests per second established the current safe side
of that boundary:

- all 251 scheduled requests were accepted;
- no HTTP request failed and no iteration was dropped;
- latency averaged 140.3 ms, with 246.6 ms p95; and
- no PostgreSQL pool timeout or connection error was logged.

These results show the gate remains out of the way below saturation and returns
an explicit retryable response above saturation. A fresh Docker session or
named deployment hardware is required before re-establishing the earlier
20-request-per-second capacity result with the gate enabled.

## Sustained 5,000-job run

The follow-up run targeted 20 submissions per second for 250 seconds. k6
scheduled one boundary iteration at the end, producing 5,001 unique jobs:
4,900 `demo.generate_report` jobs and 101 two-second `demo.wait` jobs.

- 5,001 of 5,001 submissions were accepted.
- No HTTP requests failed and no iterations were dropped.
- Submission latency was 28.0 ms on average, 21.5 ms at the median, 38.1 ms at
  p90, and 52.7 ms at p95. The maximum was 575.0 ms.
- PostgreSQL and the worker metrics confirmed all 5,001 jobs succeeded.
- The final queue and running-job counts were zero.
- The failure ratio was zero and the existing dead-letter total did not change.

Prometheus reported an enqueue and completion rate of 1,000.2 per minute after
the run. That value is 5,001 transitions divided across the metric's fixed
five-minute calculation window; it is not the actual configured arrival rate,
which remained 20 submissions per second.

The normalized k6 output is stored locally at
`load/results/5000-dashboard-clean-summary.json`. Load-result files are ignored
by Git, while this document preserves the reproducible findings.

## Interpretation

The sustained run proves that this validation topology can accept and finish
roughly 5,000 mixed demo jobs at 20 submissions per second without errors. The
initial run is equally useful: it exposed how database connection pressure and
proxy health behavior combined under a burstier workload. Further capacity
claims require repeated runs on named production hardware and systematic
changes to pool size, API replica count, and admission limits.
