"""Reproduce PostgreSQL and object-storage failures on the local validation stack."""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

# Support both `python scripts/...py` and imports from the unit tests.
if __package__ in {None, ""}:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import validate_deployment as deployment

BLOB_COMPOSE = deployment.ROOT / "deploy" / "compose.blob-validation.yaml"
BLOB_SERVICES = ("api-1", "api-2", "worker-1", "worker-2")
FORGE_SERVICES = (*BLOB_SERVICES, "dispatcher", "scheduler", "recovery")
INLINE_BYTES = 128


def compose(*arguments: str, blob: bool = False) -> str:
    command = [
        "docker",
        "compose",
        "-f",
        str(deployment.BASE_COMPOSE),
        "-f",
        str(deployment.OBSERVABILITY_COMPOSE),
    ]
    if blob:
        command.extend(["-f", str(BLOB_COMPOSE)])
    command.extend(arguments)
    environment = os.environ.copy()
    environment["FORGE_BLOB_TEST_INLINE_BYTES"] = str(INLINE_BYTES)
    result = subprocess.run(
        command,
        cwd=deployment.ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise deployment.ValidationFailure(
            f"Docker operation {arguments[:2]} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


@contextmanager
def dependency_stopped(service: str) -> Iterator[None]:
    # Stop may partly succeed before reporting an error; always attempt restoration.
    try:
        compose("stop", "--timeout", "5", service)
        yield
    finally:
        compose("start", service)


def submit(
    credential: str,
    body: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{deployment.API_URL}/v1/jobs",
        method="POST",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {credential}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
    )
    started = time.monotonic()
    try:
        with deployment.OPENER.open(request, timeout=10) as response:
            status, headers, data = response.status, response.headers, json.load(response)
    except urllib.error.HTTPError as error:
        status, headers, data = error.code, error.headers, json.load(error)
    return {
        "status": status,
        "seconds": round(time.monotonic() - started, 3),
        "retry_after": headers.get("Retry-After"),
        "body": data,
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise deployment.ValidationFailure(message)


def submit_after_recovery(
    credential: str,
    body: dict[str, Any],
    idempotency_key: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + 30
    responses = []
    while True:
        response = submit(credential, body, idempotency_key)
        responses.append(response)
        if response["status"] in {200, 202}:
            return response, responses
        code = response["body"].get("error", {}).get("code")
        require(
            response["status"] == 503
            and code in {"dependency_unavailable", "submission_capacity_exceeded"}
            and time.monotonic() < deadline,
            f"Submission did not recover: {response}",
        )
        time.sleep(1)


def require_dependency_error(response: dict[str, Any], dependency: str) -> None:
    error = response["body"].get("error", {})
    require(
        response["status"] == 503
        and error.get("code") == "dependency_unavailable"
        and error.get("details", {}).get("dependency") == dependency
        and response["retry_after"] is not None
        and response["seconds"] < 5,
        f"Expected bounded retryable {dependency} 503; received {response}",
    )


def wait_for_job(credential: str, job_id: str, *, timeout: float = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = deployment.request_json("GET", f"/v1/jobs/{job_id}", token=credential)
        if job["status"] in deployment.TERMINAL_STATUSES:
            require(job["status"] == "succeeded", f"Job {job_id} ended as {job['status']}")
            return job
        time.sleep(0.5)
    raise deployment.ValidationFailure(f"Job {job_id} did not finish within {timeout}s")


def attempts(credential: str, job_id: str) -> list[dict[str, Any]]:
    return list(
        deployment.request_json(
            "GET",
            f"/v1/jobs/{job_id}/attempts",
            token=credential,
        )["items"]
    )


def wait_for_attempt(
    credential: str,
    job_id: str,
    *,
    storage_failure: bool = False,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        history = attempts(credential, job_id)
        if any(
            item.get("error_code") == "object_store_unavailable"
            if storage_failure
            else item["status"] == "running"
            for item in history
        ):
            return history
        time.sleep(0.25)
    raise deployment.ValidationFailure(
        "The expected running/failed storage attempt was not observed"
    )


def snapshot_processes() -> dict[str, Any]:
    ids = compose("ps", "-q", *FORGE_SERVICES).splitlines()
    require(len(ids) == len(FORGE_SERVICES), "Not all Forge processes are running")
    result = subprocess.run(
        ["docker", "inspect", *ids],
        cwd=deployment.ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return {
        item["Config"]["Labels"]["com.docker.compose.service"]: {
            "id": item["Id"],
            "started_at": item["State"]["StartedAt"],
            "restart_count": item["RestartCount"],
            "running": item["State"]["Running"],
        }
        for item in json.loads(result.stdout)
    }


def assert_processes_survived(before: dict[str, Any]) -> dict[str, Any]:
    after = snapshot_processes()
    require(
        before == after,
        "A Forge process restarted, was recreated, or stopped during the outage: "
        + json.dumps(
            {
                name: {"before": before.get(name), "after": after.get(name)}
                for name in before.keys() | after.keys()
                if before.get(name) != after.get(name)
            },
            sort_keys=True,
        ),
    )
    return after


def postgres_scenario(credential: str) -> dict[str, Any]:
    print("PostgreSQL: interrupting running work and probing rejected submissions...", flush=True)
    before = snapshot_processes()
    accepted = []
    for _ in range(3):
        response = submit(
            credential,
            {
                "type": "demo.wait",
                "payload": {"task_id": f"pg-{uuid4().hex}", "duration_seconds": 12},
                "max_attempts": 3,
            },
            uuid4().hex,
        )
        require(response["status"] == 202, "Pre-outage work was not accepted")
        accepted.append(str(response["body"]["id"]))
    wait_for_attempt(credential, accepted[0])
    rejection_key = uuid4().hex
    body = {"type": "demo.generate_report", "payload": {"report_id": "pg-probe", "format": "json"}}
    responses = []
    with dependency_stopped("postgres"):
        outage_started = time.monotonic()
        for index in range(3):
            response = submit(credential, body, rejection_key if index == 0 else uuid4().hex)
            responses.append(response)
            require_dependency_error(response, "postgres")
        time.sleep(max(0, 5 - (time.monotonic() - outage_started)))
        outage_seconds = round(time.monotonic() - outage_started, 3)
    deployment.wait_for_ready(120)
    retry, recovery_probes = submit_after_recovery(credential, body, rejection_key)
    require(retry["status"] == 202, "The rejected PostgreSQL submission could not be retried")
    accepted.append(str(retry["body"]["id"]))
    jobs = [wait_for_job(credential, job_id) for job_id in accepted]
    assert_processes_survived(before)
    return {
        "passed": True,
        "outage_seconds": outage_seconds,
        "rejected_probes": responses,
        "rejected_submission_retry_status": retry["status"],
        "recovery_submission_probes": recovery_probes,
        "processes_survived": True,
        "jobs": [{"id": job["id"], "status": job["status"]} for job in jobs],
    }


def blob_scenarios(credential: str) -> dict[str, Any]:
    print("Object storage: testing payload rejection and result retry...", flush=True)
    before = snapshot_processes()
    prior_jobs = len(deployment.list_validation_jobs(credential))
    rejected_key = uuid4().hex
    oversized_body: dict[str, Any] = {
        "type": "demo.generate_report",
        "max_attempts": 1,
        "payload": {"report_id": uuid4().hex + "x" * 96, "format": "json"},
    }
    # The normalized payload fits inline, while the result's artifact field puts it over 128 bytes.
    inline_body: dict[str, Any] = {
        "type": "demo.generate_report",
        "max_attempts": 1,
        "payload": {"report_id": uuid4().hex + "r" * 32, "format": "json"},
    }
    with dependency_stopped("object-store"):
        rejected = submit(credential, oversized_body, rejected_key)
        require_dependency_error(rejected, "object_store")
        require(
            len(deployment.list_validation_jobs(credential)) == prior_jobs,
            "An object-store-rejected payload left a phantom job",
        )
        accepted = submit(credential, inline_body, uuid4().hex)
        require(
            accepted["status"] == 202, "An inline payload could not be accepted during storage loss"
        )
        job_id = str(accepted["body"]["id"])
        require(not accepted["body"]["payload_stored_externally"], "Test payload was not inline")
        wait_for_attempt(credential, job_id, storage_failure=True)
    deployment.wait_for_ready(120)
    job = wait_for_job(credential, job_id)
    require(job["result_stored_externally"], "Recovered result did not use object storage")
    result = deployment.request_json("GET", f"/v1/jobs/{job_id}/result", token=credential)
    require(result["result"]["report_id"] == inline_body["payload"]["report_id"], "Result mismatch")
    history = attempts(credential, job_id)
    require(
        any(item["error_code"] == "object_store_unavailable" for item in history)
        and history[-1]["status"] == "succeeded",
        "Missing infrastructure-failure/recovery evidence",
    )
    retry, recovery_probes = submit_after_recovery(credential, oversized_body, rejected_key)
    require(retry["status"] == 202, "The rejected blob submission could not be retried")
    retried_job = wait_for_job(credential, str(retry["body"]["id"]))
    require(retried_job["payload_stored_externally"], "Oversized payload was not stored externally")
    assert_processes_survived(before)
    return {
        "passed": True,
        "rejected_payload": rejected,
        "phantom_jobs": 0,
        "rejected_submission_retry_status": retry["status"],
        "recovery_submission_probes": recovery_probes,
        "processes_survived": True,
        "result_job_id": job_id,
        "result_stored_externally": True,
        "result_verified": True,
        "requested_handler_attempts": 1,
        "effective_max_attempts": job["max_attempts"],
        "attempts": history,
        "retried_payload_job_id": retried_job["id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-start", action="store_true", help="Use an already running stack")
    args = parser.parse_args()
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now(UTC).isoformat(),
        "passed": False,
    }
    admin: str | None = None
    key_id: str | None = None
    blob_profile_applied = False
    try:
        if not args.skip_start:
            deployment.start_stack()
        deployment.wait_for_ready(120)
        admin = deployment.get_admin_token(may_rotate=not args.skip_start)
        baseline = deployment.request_json("GET", "/v1/admin/operations/summary", token=admin)
        require(
            baseline["outstanding_jobs"] == 0, "Run the outage checks on an idle validation stack"
        )
        require(deployment.REQUIRED_SERVICES <= deployment.running_services(), "Services missing")
        key_id, credential = deployment.create_validation_key(admin)
        report["postgres"] = postgres_scenario(credential)
        blob_profile_applied = True
        compose("up", "-d", "--no-deps", *BLOB_SERVICES, blob=True)
        deployment.wait_for_ready(120)
        report["object_store"] = blob_scenarios(credential)
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if blob_profile_applied:
            try:
                compose("up", "-d", "--no-deps", *BLOB_SERVICES)
                deployment.wait_for_ready(120)
                report["normal_profile_restored"] = True
            except Exception as error:
                report["normal_profile_restored"] = False
                report["restoration_error"] = str(error)
                report["passed"] = False
        if admin is not None and key_id is not None:
            try:
                deployment.revoke_validation_key(admin, key_id)
                report["validation_key_revoked"] = True
            except Exception as error:
                report["revocation_error"] = str(error)
                report["passed"] = False
        try:
            startup_ok, startup_output = deployment.run_container_check("scripts/check_startup.py")
            integrity_ok, integrity_output = deployment.run_container_check(
                "scripts/validate_integrity.py",
            )
            report["startup_checks"] = {"passed": startup_ok, "output": startup_output}
            report["integrity_checks"] = {"passed": integrity_ok, "output": integrity_output}
            report["final_services"] = sorted(deployment.running_services())
            require(startup_ok and integrity_ok, "Final startup or integrity checks failed")
            require(
                deployment.REQUIRED_SERVICES <= set(report["final_services"]),
                "Final services missing",
            )
            if admin is not None:
                deadline = time.monotonic() + 60
                while True:
                    summary = deployment.request_json(
                        "GET",
                        "/v1/admin/operations/summary",
                        token=admin,
                    )
                    queues = summary["queues"]
                    drained = (
                        summary["outstanding_jobs"] == 0
                        and queues["queued_total"] == 0
                        and queues["processing_total"] == 0
                        and summary["active_workers"] == 2
                    )
                    if drained or time.monotonic() >= deadline:
                        break
                    time.sleep(1)
                report["final_operations"] = summary
                require(drained, "Queues did not drain or both workers did not return online")
        except Exception as error:
            report["final_check_error"] = str(error)
            report["passed"] = False
        report["finished_at"] = datetime.now(UTC).isoformat()
        deployment.REPORTS.mkdir(parents=True, exist_ok=True)
        output = deployment.REPORTS / "dependency-outages.json"
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Outage report: {output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
