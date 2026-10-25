# Presenting Forge

## Short project description

Forge is a distributed job-processing platform with a FastAPI backend and React
operations dashboard. PostgreSQL stores accepted work and execution ownership;
Redis delivers messages. It supports scheduling, priorities, retries, recovery,
cancellation, dead-letter replay, scoped keys and observable job histories.

## CV bullets grounded in the evidence

- Built a distributed job-processing platform using Python, FastAPI, PostgreSQL
  and Redis, with transactional outbox delivery, leased execution ownership,
  retry/recovery and a React/TypeScript operations dashboard.
- Verified completion of 100,000 HTTP-submitted demo jobs with matching stored
  report IDs/formats, zero checked integrity violations, stable processes and
  a fully drained queue in a documented local Docker benchmark.
- Automated dependency-outage and worker-kill experiments, and added scoped
  API keys, Prometheus metrics, correlated traces, live job timelines and
  repeatable backend/browser validation.

Do not add “1,000 jobs/sec”, “50 ms job latency”, “exactly once”, “zero downtime”
or “production deployed” to these bullets. Those claims are not established.
The demo handler returns cheap JSON; it does not generate real PDF reports.

## A demo you can explain

1. Start Forge with `uv run python scripts/start_forge.py` and connect the
   dashboard using a dedicated operator key.
2. Submit a report and open its timeline: acceptance, claim, successful result.
   Explain that PostgreSQL acceptance is committed before the HTTP response.
3. Schedule another job and cancel it. Explain why running cancellation is
   cooperative and cannot undo an external action.
4. Run `scripts/run_worker_kill_validation.py` against the disposable local
   stack. Follow the abandoned attempt and replacement worker in the dashboard.
5. Show the benchmark graphs and describe where queueing dominated latency.
   Explain the difference between preserving accepted work and accepting every
   request under overload.

## Decisions worth discussing

- The outbox closes the gap between committing a job and publishing its message.
  A crash can still repeat delivery; database leases fence valid completion.
- Submission idempotency deduplicates requests. Handlers or downstream systems
  still need to protect their own external side effects.
- Redis is reconstructible delivery state; PostgreSQL is the accepted-work record.
- Weighted priority grants opportunities rather than a fixed start deadline.
  Low-priority queue wait grew under the measured overloaded workload.
- Browser keys stay in memory, with a same-origin proxy and backend-enforced
  scopes. The dashboard uses the same API as other clients.
- Large payloads/results add a second storage dependency. Retention archives
  include that data before cleanup, but are not full disaster-recovery backups.

The architecture decisions, completion audit and dated benchmark reports contain
the implementation details behind each point.
