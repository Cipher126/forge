import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy" / "compose.validation.yaml"
DEFAULT_BASE_URL = "http://127.0.0.1:18080"
TERMINAL_STATUSES = {"succeeded", "failed", "dead_lettered", "cancelled"}


def run(*arguments: str) -> str:
    completed = subprocess.run(
        arguments,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def compose(*arguments: str) -> str:
    return run("docker", "compose", "-f", str(COMPOSE_FILE), *arguments)


def request_json(
    method: str,
    url: str,
    *,
    api_key: str,
    body: dict[str, object] | None = None,
) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": f"worker-kill-{uuid4()}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} returned HTTP {error.code}: {detail}") from error


def create_validation_key() -> str:
    output = compose(
        "run",
        "--rm",
        "--no-deps",
        "api-1",
        "forge",
        "key",
        "create",
        "--name",
        f"worker-kill-validation-{uuid4().hex[:8]}",
        "--scope",
        "jobs:read",
        "--scope",
        "jobs:write",
        "--rate-limit-requests",
        "10000",
    )
    credential = output.splitlines()[-1].strip()
    if not credential.startswith("forge_live_"):
        raise RuntimeError("Forge CLI did not return an API credential")
    return credential


def wait_for_running_attempt(
    base_url: str,
    api_key: str,
    job_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        attempts = request_json(
            "GET", f"{base_url}/v1/jobs/{job_id}/attempts", api_key=api_key
        )["items"]
        running = [attempt for attempt in attempts if attempt["status"] == "running"]
        if running:
            return running[-1]
        time.sleep(0.25)
    raise TimeoutError("job did not enter a running attempt before the deadline")


def worker_container_for(worker_id: str) -> str:
    hostname = worker_id.partition(":")[0]
    for service in ("worker-1", "worker-2"):
        container_id = compose("ps", "-q", service)
        if container_id.startswith(hostname):
            return container_id
    raise RuntimeError(f"no validation worker container matches worker {worker_id!r}")


@contextmanager
def kill_worker_temporarily(container_id: str) -> Iterator[None]:
    run("docker", "kill", container_id)
    try:
        yield
    finally:
        run("docker", "start", container_id)


def wait_for_terminal_job(
    base_url: str,
    api_key: str,
    job_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = request_json("GET", f"{base_url}/v1/jobs/{job_id}", api_key=api_key)
        if job["status"] in TERMINAL_STATUSES:
            return job
        time.sleep(0.5)
    raise TimeoutError("job did not reach a terminal state before the deadline")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kill a worker mid-job and verify lease-based recovery."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--job-duration", type=float, default=45.0)
    parser.add_argument("--timeout", type=float, default=150.0)
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    compose("up", "-d", "worker-1", "worker-2")
    time.sleep(2)
    api_key = create_validation_key()
    submitted_at = datetime.now(UTC)
    job = request_json(
        "POST",
        f"{arguments.base_url}/v1/jobs",
        api_key=api_key,
        body={
            "type": "demo.wait",
            "payload": {
                "task_id": f"worker-kill-{uuid4().hex[:8]}",
                "duration_seconds": arguments.job_duration,
            },
            "priority": "high",
            "max_attempts": 3,
        },
    )
    first_attempt = wait_for_running_attempt(
        arguments.base_url,
        api_key,
        job["id"],
        timeout_seconds=20,
    )
    killed_container = worker_container_for(first_attempt["worker_id"])
    with kill_worker_temporarily(killed_container):
        terminal_job = wait_for_terminal_job(
            arguments.base_url,
            api_key,
            job["id"],
            timeout_seconds=arguments.timeout,
        )
        attempts = request_json(
            "GET",
            f"{arguments.base_url}/v1/jobs/{job['id']}/attempts",
            api_key=api_key,
        )["items"]
        statuses = [attempt["status"] for attempt in attempts]
        if terminal_job["status"] != "succeeded":
            raise RuntimeError(f"recovered job ended as {terminal_job['status']!r}")
        if statuses.count("abandoned") != 1 or statuses.count("succeeded") != 1:
            raise RuntimeError(f"unexpected attempt outcomes after worker kill: {statuses}")
        if len({attempt["worker_id"] for attempt in attempts}) != len(attempts):
            raise RuntimeError("recovered attempts did not use distinct worker identities")

    integrity_checks = json.loads(
        compose(
            "run",
            "--rm",
            "--no-deps",
            "api-1",
            "python",
            "scripts/validate_integrity.py",
        )
    )
    if any(integrity_checks.values()):
        raise RuntimeError(f"integrity violations after worker kill: {integrity_checks}")

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "submitted_at": submitted_at.isoformat(),
        "job_id": job["id"],
        "killed_worker_id": first_attempt["worker_id"],
        "killed_container_id": killed_container[:12],
        "final_status": terminal_job["status"],
        "attempts": [
            {
                "attempt_number": attempt["attempt_number"],
                "status": attempt["status"],
                "worker_id": attempt["worker_id"],
                "failure_kind": attempt["failure_kind"],
            }
            for attempt in attempts
        ],
        "integrity_checks": integrity_checks,
        "elapsed_seconds": round(
            (datetime.now(UTC) - submitted_at).total_seconds(), 3
        ),
    }
    output = ROOT / "tests" / "reports" / "worker-kill-validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
