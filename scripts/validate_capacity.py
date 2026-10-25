"""Measure capacity, a 10x burst, authentication cost, and 100,000 real submissions.

Uses the existing isolated validation stack. Capacity shortfalls are reported separately
from correctness failures. No token or job payload is written into these reports.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import validate_deployment as validation  # noqa: E402
from scripts.validate_dependency_outages import snapshot_processes  # noqa: E402

NETWORK = "forge-validation_default"
RESULTS = ROOT / "load" / "results"


def container_json(script: str, *arguments: str, input_value: object = None) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(validation.BASE_COMPOSE),
            "-f",
            str(validation.OBSERVABILITY_COMPOSE),
            "exec",
            "-T",
            "api-1",
            "python",
            script,
            *arguments,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
        input=json.dumps(input_value) if input_value is not None else None,
    )
    parsed = json.loads(completed.stdout)
    if not isinstance(parsed, dict):
        raise validation.ValidationFailure(f"{script} returned a non-object JSON response")
    return parsed


def durable_jobs(key_id: str) -> dict[str, Any]:
    return container_json("scripts/report_validation_jobs.py", key_id)


def wait_for_drain(key_id: str, expected: int, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        report = durable_jobs(key_id)
        statuses = report["jobs_by_status"]
        outstanding = sum(
            n for status, n in statuses.items() if status not in validation.TERMINAL_STATUSES
        )
        if report["total_jobs"] == expected and outstanding == 0:
            return report
        print(f"Drain: {report['total_jobs']} durable jobs; {outstanding} outstanding.", flush=True)
        time.sleep(10)
    raise validation.ValidationFailure("accepted jobs did not drain or durable count mismatched")


class Sampler:
    def __init__(self, admin_token: str, report_name: str = "backend-capacity") -> None:
        self.admin_token = admin_token
        self.samples: list[dict[str, Any]] = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.started = time.monotonic()
        self.stage = "authentication"
        self.last_progress = self.started
        self.progress_name = (
            "backend-progress" if report_name == "backend-capacity" else f"{report_name}-progress"
        )

    def set_stage(self, stage: str) -> None:
        self.stage = stage

    def sample(self) -> None:
        while not self.stop.is_set():
            point: dict[str, Any] = {
                "elapsed_seconds": round(time.monotonic() - self.started, 3),
                "stage": self.stage,
            }
            try:
                state = validation.request_json(
                    "GET",
                    "/v1/admin/operations/summary",
                    token=self.admin_token,
                    timeout=3,
                )
                point.update(
                    {
                        "outstanding_jobs": state["outstanding_jobs"],
                        "terminal_jobs": state["terminal_jobs"],
                        "queued_messages": state["queues"]["queued_total"],
                        "active_workers": state["active_workers"],
                    }
                )
            except Exception as error:
                point["error"] = type(error).__name__
            self.samples.append(point)
            # This checkpoint makes long runs observable without exposing their credential.
            temporary = RESULTS / f"{self.progress_name}.tmp"
            temporary.write_text(json.dumps(point) + "\n", encoding="utf-8")
            temporary.replace(RESULTS / f"{self.progress_name}.json")
            if time.monotonic() - self.last_progress >= 60:
                print(f"Benchmark progress: {json.dumps(point)}", flush=True)
                self.last_progress = time.monotonic()
            self.stop.wait(5)


def run_load(
    credential: str, name: str, script: str, variables: dict[str, str]
) -> tuple[int, dict[str, Any]]:
    destination = RESULTS / f"{name}-k6.json"
    destination.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "API_KEY": credential,
            "BASE_URL": "http://proxy:8080",
            "SUMMARY_PATH": f"/load/results/{destination.name}",
            **variables,
        }
    )
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        NETWORK,
        "--memory",
        "512m",
        "--cpus",
        "2",
        "-v",
        f"{ROOT / 'load'}:/load",
    ]
    for name in ["API_KEY", "BASE_URL", "SUMMARY_PATH", *variables]:
        command.extend(["-e", name])
    command.extend([validation.K6_IMAGE, "run", "--quiet", f"/load/k6/{script}"])
    returncode = subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode
    return returncode, validation.load_k6_summary(destination)


def accepted(summary: dict[str, Any]) -> int:
    return int(summary["metrics"].get("accepted_jobs", {}).get("count", 0))


def checkpoint(report: dict[str, Any]) -> None:
    (RESULTS / f"{report['report_name']}.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )


def chart(samples: list[dict[str, Any]], destination: Path) -> None:
    """A portable standalone SVG; no browser, CDN, or plotting dependency."""
    points = [p for p in samples if "outstanding_jobs" in p]
    if not points:
        return
    maximum_x = max(p["elapsed_seconds"] for p in points) or 1
    maximum_y = max(p["outstanding_jobs"] for p in points) or 1
    coordinates = " ".join(
        f"{60 + 880 * p['elapsed_seconds'] / maximum_x:.1f},"
        f"{340 - 280 * p['outstanding_jobs'] / maximum_y:.1f}"
        for p in points
    )
    destination.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="400" viewBox="0 0 1000 400">'
        '<rect width="1000" height="400" fill="#fff"/>'
        '<text x="60" y="30" font-family="sans-serif" font-size="22">'
        "Forge durable outstanding jobs</text>"
        '<path d="M60 60V340H940" fill="none" stroke="#555"/>'
        f'<polyline points="{coordinates}" fill="none" stroke="#2563eb" stroke-width="2"/>'
        f'<text x="60" y="380" font-family="sans-serif">0 to {maximum_x:.0f} seconds</text>'
        f'<text x="60" y="55" font-family="sans-serif">Peak: {maximum_y} jobs</text>'
        "</svg>",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rates", default="10,20,40,80,160")
    parser.add_argument("--step-seconds", type=int, default=15)
    parser.add_argument("--endurance-jobs", type=int, default=100000)
    parser.add_argument("--endurance-vus", type=int, default=8)
    parser.add_argument("--drain-timeout", type=int, default=3600)
    parser.add_argument("--skip-capacity", action="store_true")
    parser.add_argument("--report-name", default="backend-capacity")
    args = parser.parse_args()
    if re.fullmatch(r"[a-zA-Z0-9-]+", args.report_name) is None:
        parser.error("report name must contain only ASCII letters, digits and hyphens")
    rates = [int(value) for value in args.rates.split(",")]
    if (
        min(*rates, args.step_seconds, args.endurance_jobs, args.endurance_vus, args.drain_timeout)
        < 1
    ):
        parser.error("rates, durations, job counts and concurrency must be positive")
    if args.endurance_vus > 128:
        parser.error("endurance VUs must be at most 128 on the local validation stack")
    RESULTS.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": started.isoformat(),
        "passed": False,
        "network_path": "k6 -> Docker internal proxy -> two API replicas",
        "topology": "8 worker slots; report handler global concurrency 4; tracing enabled",
        "capacity_steps": [],
        "test_keys_revoked": [],
        "in_progress": True,
        "report_name": args.report_name,
    }
    admin_token = validation.get_admin_token(may_rotate=False)
    report["processes_before"] = snapshot_processes()
    info = subprocess.run(
        [
            "docker",
            "info",
            "--format",
            '{"cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}},"os":{{json .OperatingSystem}}}',
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    report["docker_resources"] = json.loads(info.stdout)
    sampler = Sampler(admin_token, args.report_name)
    sampler.thread.start()
    keys: list[str] = []
    checks: dict[str, bool] = {}
    try:
        auth_id, auth_key = validation.create_validation_key(admin_token)
        keys.append(auth_id)
        report["authentication"] = container_json(
            "scripts/benchmark_auth.py",
            input_value={"credential": auth_key, "samples": 500},
        )
        checkpoint(report)
        baseline_rate = 10
        if not args.skip_capacity:
            for rate in rates:
                sampler.set_stage(f"capacity-{rate}")
                print(f"Capacity: {rate} arrivals/sec for {args.step_seconds}s", flush=True)
                key_id, credential = validation.create_validation_key(admin_token)
                keys.append(key_id)
                code, summary = run_load(
                    credential,
                    f"capacity-{rate}",
                    "submit-jobs.js",
                    {
                        "RATE": str(rate),
                        "DURATION": f"{args.step_seconds}s",
                        "PREALLOCATED_VUS": str(min(32, rate)),
                        "MAX_VUS": "128",
                    },
                )
                durable = wait_for_drain(key_id, accepted(summary), args.drain_timeout)
                step = {
                    "rate": rate,
                    "k6_exit_code": code,
                    "metrics": summary["metrics"],
                    "durable": durable,
                    "slo_passed": code == 0,
                    "elapsed_seconds": round(time.monotonic() - sampler.started, 3),
                }
                report["capacity_steps"].append(step)
                checkpoint(report)
                checks[f"capacity_{rate}_durable_success"] = (
                    durable["jobs_by_status"]
                    == {
                        "succeeded": accepted(summary),
                    }
                    and accepted(summary) > 0
                )
                checks[f"capacity_{rate}_no_unexpected_server_errors"] = (
                    summary["metrics"]["server_errors"].get("count", 0) == 0
                )
                if code == 0:
                    baseline_rate = rate
            for name, rate in (("burst-10x", baseline_rate * 10), ("target-1000", 1000)):
                sampler.set_stage(name)
                print(f"{name}: {rate} planned arrivals/sec for 3s (128 VU limit)", flush=True)
                key_id, credential = validation.create_validation_key(admin_token)
                keys.append(key_id)
                code, summary = run_load(
                    credential,
                    name,
                    "submit-jobs.js",
                    {
                        "RATE": str(rate),
                        "DURATION": "3s",
                        "PREALLOCATED_VUS": "128",
                        "MAX_VUS": "128",
                    },
                )
                durable = wait_for_drain(key_id, accepted(summary), args.drain_timeout)
                report[name] = {
                    "rate": rate,
                    "reference_rate": baseline_rate,
                    "planned_arrivals": rate * 3,
                    "k6_exit_code": code,
                    "metrics": summary["metrics"],
                    "durable": durable,
                    "capacity_target_met": code == 0 and accepted(summary) >= rate * 3,
                }
                checkpoint(report)
                checks[f"{name}_durable_success"] = (
                    durable["jobs_by_status"]
                    == {
                        "succeeded": accepted(summary),
                    }
                    and accepted(summary) > 0
                )
                checks[f"{name}_no_unexpected_server_errors"] = (
                    summary["metrics"]["server_errors"].get("count", 0) == 0
                )
        key_id, credential = validation.create_validation_key(admin_token, lifetime_hours=3)
        keys.append(key_id)
        sampler.set_stage("endurance")
        report["endurance_key_id"] = key_id
        report["endurance_baseline"] = validation.request_json(
            "GET",
            "/v1/admin/operations/summary",
            token=admin_token,
        )
        checkpoint(report)
        print(
            f"Endurance: {args.endurance_jobs:,} real HTTP submissions at {args.endurance_vus} VUs",
            flush=True,
        )
        code, summary = run_load(
            credential,
            "endurance"
            if args.report_name == "backend-capacity"
            else f"{args.report_name}-endurance",
            "endurance.js",
            {
                "JOBS": str(args.endurance_jobs),
                "VUS": str(args.endurance_vus),
                "RUN_ID": started.strftime("%Y%m%d%H%M%S"),
                "MAX_DURATION": "90m",
            },
        )
        sampler.set_stage("endurance-drain")
        durable = wait_for_drain(key_id, accepted(summary), args.drain_timeout)
        report["endurance"] = {
            "k6_exit_code": code,
            "planned_jobs": args.endurance_jobs,
            "metrics": summary["metrics"],
            "durable": durable,
        }
        checks["endurance_submissions"] = code == 0 and accepted(summary) == args.endurance_jobs
        checks["endurance_durable_success"] = durable["jobs_by_status"] == {
            "succeeded": args.endurance_jobs,
        }
        checks["endurance_results_match_payloads"] = durable["invalid_report_results"] == 0
        report["integrity"] = container_json("scripts/validate_integrity.py")
        checks["integrity"] = not any(report["integrity"].values())
        report["services"] = sorted(validation.running_services())
        checks["services"] = validation.REQUIRED_SERVICES <= set(report["services"])
        report["processes_after"] = snapshot_processes()
        checks["processes_stable"] = report["processes_before"] == report["processes_after"]
        report["checks"] = checks
        report["passed"] = all(checks.values())
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        sampler.stop.set()
        sampler.thread.join(timeout=5)
        for key_id in keys:
            try:
                validation.revoke_validation_key(admin_token, key_id)
                report["test_keys_revoked"].append(key_id)
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(type(error).__name__)
                report["passed"] = False
        report["samples"] = sampler.samples
        report["in_progress"] = False
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["duration_seconds"] = (datetime.now(UTC) - started).total_seconds()
        destination = RESULTS / f"{args.report_name}.json"
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        chart(
            sampler.samples,
            RESULTS
            / (
                "backend-queue.svg"
                if args.report_name == "backend-capacity"
                else f"{args.report_name}-queue.svg"
            ),
        )
        print(f"Capacity report: {destination}; correctness passed: {report['passed']}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
