import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = ROOT / "deploy" / "compose.validation.yaml"
OBSERVABILITY_COMPOSE = ROOT / "deploy" / "compose.observability.yaml"
TOKEN_FILE = ROOT / "deploy" / "observability" / "metrics-token"
REPORTS = ROOT / "tests" / "reports"
LOAD_RESULTS = ROOT / "load" / "results"
API_URL = "http://127.0.0.1:18080"
K6_IMAGE = (
    "grafana/k6@sha256:23f2279054c3e01535455c4d92914a625df12aeb631ea0e3ea7a43f96bcbc843"
)
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "dead_lettered", "cancelled"})
REQUIRED_SERVICES = frozenset(
    {
        "api-1",
        "api-2",
        "dispatcher",
        "grafana",
        "object-store",
        "postgres",
        "prometheus",
        "proxy",
        "recovery",
        "redis",
        "scheduler",
        "tempo",
        "worker-1",
        "worker-2",
    }
)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ValidationFailure(RuntimeError):
    pass


def compose(*arguments: str, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(BASE_COMPOSE),
            "-f",
            str(OBSERVABILITY_COMPOSE),
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=capture_output,
    )


def request_json(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    request = urllib.request.Request(
        f"{API_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with OPENER.open(request, timeout=timeout) as response:
            parsed = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(500).decode(errors="replace")
        raise ValidationFailure(f"{method} {path} returned HTTP {error.code}: {detail}") from error
    if not isinstance(parsed, dict):
        raise ValidationFailure(f"{method} {path} returned a non-object JSON response")
    return parsed


def wait_for_ready(timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            ready = request_json("GET", "/ready", timeout=3)
            if ready.get("status") == "ready":
                return ready
        except (OSError, ValidationFailure) as error:
            last_error = error
        time.sleep(1)
    raise ValidationFailure(f"Forge did not become ready within {timeout_seconds}s: {last_error}")


def start_stack(*, rotate_token: bool = False) -> None:
    command = [sys.executable, str(ROOT / "scripts" / "start_observability.py")]
    if rotate_token:
        command.append("--rotate-token")
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        raise ValidationFailure(f"observability startup exited with {completed.returncode}")


def load_admin_token() -> str:
    if not TOKEN_FILE.exists():
        raise ValidationFailure(f"missing Prometheus administrator token: {TOKEN_FILE}")
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token.startswith("forge_live_"):
        raise ValidationFailure("Prometheus administrator token has an invalid format")
    return token


def get_admin_token(*, may_rotate: bool) -> str:
    token = load_admin_token()
    try:
        request_json("GET", "/v1/admin/operations/summary", token=token)
        return token
    except ValidationFailure:
        if not may_rotate:
            raise
    print("Stored metrics credential is unusable; rotating it once.")
    start_stack(rotate_token=True)
    wait_for_ready(120)
    token = load_admin_token()
    request_json("GET", "/v1/admin/operations/summary", token=token)
    return token


def create_validation_key(admin_token: str) -> tuple[str, str]:
    expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    created = request_json(
        "POST",
        "/v1/admin/api-keys",
        token=admin_token,
        body={
            "name": f"deployment-validation-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}",
            "scopes": ["jobs:read", "jobs:write"],
            "expires_at": expires_at,
            "rate_limit_requests": 1_000_000,
            "rate_limit_window_s": 60,
        },
    )
    key_id = created.get("id")
    credential = created.get("api_key")
    if not isinstance(key_id, str) or not isinstance(credential, str):
        raise ValidationFailure("API-key creation response omitted the id or credential")
    return key_id, credential


def revoke_validation_key(admin_token: str, key_id: str) -> None:
    request_json("POST", f"/v1/admin/api-keys/{key_id}/revoke", token=admin_token)


def parse_duration_seconds(value: str) -> int:
    matched = re.fullmatch(r"([1-9][0-9]*)([smh])", value)
    if matched is None:
        raise argparse.ArgumentTypeError("duration must use k6 form such as 25s, 2m, or 1h")
    amount = int(matched.group(1))
    multiplier = {"s": 1, "m": 60, "h": 3600}[matched.group(2)]
    return amount * multiplier


def run_k6(
    credential: str,
    *,
    rate: int,
    duration: str,
    summary_path: Path,
) -> int:
    environment = os.environ.copy()
    environment.update(
        {
            "API_KEY": credential,
            "BASE_URL": "http://host.docker.internal:18080",
            "RATE": str(rate),
            "DURATION": duration,
            "PREALLOCATED_VUS": str(max(rate, 10)),
            "MAX_VUS": str(max(rate * 3, 30)),
            "SUMMARY_PATH": f"/load/results/{summary_path.name}",
        }
    )
    summary_path.unlink(missing_ok=True)
    command = [
        "docker",
        "run",
        "--rm",
        "--add-host",
        "host.docker.internal:host-gateway",
        "-e",
        "API_KEY",
        "-e",
        "BASE_URL",
        "-e",
        "RATE",
        "-e",
        "DURATION",
        "-e",
        "PREALLOCATED_VUS",
        "-e",
        "MAX_VUS",
        "-e",
        "SUMMARY_PATH",
        "-v",
        f"{ROOT / 'load'}:/load",
        K6_IMAGE,
        "run",
        "/load/k6/submit-jobs.js",
    ]
    return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode


def list_validation_jobs(credential: str) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = {"limit": "100"}
        if cursor is not None:
            query["cursor"] = cursor
        page = request_json(
            "GET",
            f"/v1/jobs?{urllib.parse.urlencode(query)}",
            token=credential,
        )
        items = page.get("items")
        if not isinstance(items, list):
            raise ValidationFailure("job listing response omitted its items")
        jobs.extend(item for item in items if isinstance(item, dict))
        next_cursor = page.get("next_cursor")
        if next_cursor is None:
            return jobs
        if not isinstance(next_cursor, str):
            raise ValidationFailure("job listing returned an invalid cursor")
        cursor = next_cursor


def wait_for_validation_jobs(
    credential: str,
    *,
    accepted_jobs: int,
    timeout_seconds: int,
) -> Counter[str]:
    deadline = time.monotonic() + timeout_seconds
    statuses: Counter[str] = Counter()
    while time.monotonic() < deadline:
        jobs = list_validation_jobs(credential)
        statuses = Counter(str(job.get("status")) for job in jobs)
        non_terminal = sum(
            count for status, count in statuses.items() if status not in TERMINAL_STATUSES
        )
        if len(jobs) >= accepted_jobs and non_terminal == 0:
            return statuses
        time.sleep(1)
    raise ValidationFailure(
        "validation jobs did not drain within "
        f"{timeout_seconds}s; latest statuses: {dict(statuses)}"
    )


def load_k6_summary(summary_path: Path) -> dict[str, Any]:
    if not summary_path.exists():
        raise ValidationFailure(f"k6 did not create {summary_path}")
    parsed = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValidationFailure("k6 summary is not a JSON object")
    return parsed


def metric_value(summary: dict[str, Any], metric: str, value: str) -> float:
    try:
        result = summary["metrics"][metric][value]
    except (KeyError, TypeError) as error:
        raise ValidationFailure(f"k6 summary omitted metrics.{metric}.{value}") from error
    if not isinstance(result, int | float):
        raise ValidationFailure(f"k6 metric {metric}.{value} is not numeric")
    return float(result)


def running_services() -> set[str]:
    completed = compose("ps", "--services", "--status", "running", capture_output=True)
    if completed.returncode:
        raise ValidationFailure(f"docker compose ps failed: {completed.stderr.strip()}")
    return {line.strip() for line in completed.stdout.splitlines() if line.strip()}


def run_container_check(script: str) -> tuple[bool, str]:
    completed = compose(
        "exec",
        "-T",
        "api-1",
        "python",
        script,
        capture_output=True,
    )
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode == 0, output


def build_checks(
    *,
    k6_exit_code: int,
    summary: dict[str, Any],
    expected_jobs: int,
    statuses: Counter[str],
    operations: dict[str, Any],
    services: set[str],
    startup_check: bool,
    integrity_check: bool,
) -> dict[str, bool]:
    accepted = int(metric_value(summary, "accepted_jobs", "count"))
    queues = operations.get("queues", {})
    return {
        "k6_thresholds": k6_exit_code == 0,
        "planned_arrivals_accepted": accepted >= expected_jobs,
        "no_dropped_iterations": metric_value(summary, "dropped_iterations", "count") == 0,
        "all_validation_jobs_found": sum(statuses.values()) == accepted,
        "all_validation_jobs_succeeded": statuses == Counter({"succeeded": accepted}),
        "queues_drained": (
            isinstance(queues, dict)
            and queues.get("queued_total") == 0
            and queues.get("processing_total") == 0
        ),
        "workers_online": int(operations.get("active_workers", 0)) >= 2,
        "all_services_running": REQUIRED_SERVICES <= services,
        "startup_checks": startup_check,
        "integrity_checks": integrity_check,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Forge's repeatable local deployment acceptance check."
    )
    parser.add_argument("--rate", type=int, default=10)
    parser.add_argument("--duration", default="25s")
    parser.add_argument("--drain-timeout", type=int, default=120)
    parser.add_argument("--startup-timeout", type=int, default=180)
    parser.add_argument(
        "--skip-start",
        action="store_true",
        help="validate an already running observability stack without rebuilding it",
    )
    args = parser.parse_args()
    if args.rate < 1 or args.drain_timeout < 1 or args.startup_timeout < 1:
        parser.error("rate and timeout values must be positive")
    duration_seconds = parse_duration_seconds(args.duration)

    REPORTS.mkdir(parents=True, exist_ok=True)
    LOAD_RESULTS.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS / "deployment-validation.json"
    summary_path = LOAD_RESULTS / "deployment-validation-k6.json"
    started_at = datetime.now(UTC)
    admin_token: str | None = None
    key_id: str | None = None
    credential: str | None = None
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "workload": {"rate_per_second": args.rate, "duration": args.duration},
        "passed": False,
    }

    try:
        if not args.skip_start:
            print("Starting Forge validation and observability services...", flush=True)
            start_stack()
        report["readiness"] = wait_for_ready(args.startup_timeout)
        admin_token = get_admin_token(may_rotate=not args.skip_start)
        baseline = request_json("GET", "/v1/admin/operations/summary", token=admin_token)
        key_id, credential = create_validation_key(admin_token)

        print(f"Submitting jobs at {args.rate}/second for {args.duration}...", flush=True)
        k6_exit_code = run_k6(
            credential,
            rate=args.rate,
            duration=args.duration,
            summary_path=summary_path,
        )
        summary = load_k6_summary(summary_path)
        accepted_jobs = int(metric_value(summary, "accepted_jobs", "count"))
        expected_jobs = args.rate * duration_seconds
        print(
            f"Waiting for {accepted_jobs} accepted jobs to reach terminal state...",
            flush=True,
        )
        statuses = wait_for_validation_jobs(
            credential,
            accepted_jobs=accepted_jobs,
            timeout_seconds=args.drain_timeout,
        )
        operations = request_json("GET", "/v1/admin/operations/summary", token=admin_token)
        services = running_services()
        startup_ok, startup_output = run_container_check("scripts/check_startup.py")
        integrity_ok, integrity_output = run_container_check("scripts/validate_integrity.py")
        checks = build_checks(
            k6_exit_code=k6_exit_code,
            summary=summary,
            expected_jobs=expected_jobs,
            statuses=statuses,
            operations=operations,
            services=services,
            startup_check=startup_ok,
            integrity_check=integrity_ok,
        )
        report.update(
            {
                "expected_jobs": expected_jobs,
                "k6_exit_code": k6_exit_code,
                "k6_metrics": summary.get("metrics", {}),
                "validation_jobs_by_status": dict(statuses),
                "baseline_operations": baseline,
                "final_operations": operations,
                "running_services": sorted(services),
                "missing_services": sorted(REQUIRED_SERVICES - services),
                "startup_check_output": startup_output,
                "integrity_check_output": integrity_output,
                "checks": checks,
                "passed": all(checks.values()),
            }
        )
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if admin_token is not None and key_id is not None:
            try:
                revoke_validation_key(admin_token, key_id)
                report["validation_key_revoked"] = True
            except Exception as error:
                report["validation_key_revoked"] = False
                report["revocation_error"] = f"{type(error).__name__}: {error}"
                report["passed"] = False
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["duration_seconds"] = round(
            (datetime.now(UTC) - started_at).total_seconds(), 3
        )
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Validation report: {report_path}")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
