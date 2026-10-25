"""Run the entire backend acceptance sequence with one command.

This operates only on the local Forge development/validation Compose projects.
It never commits, pushes, or deploys to an external server.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-start", action="store_true")
    parser.add_argument("--endurance-jobs", type=int, default=100000)
    parser.add_argument("--endurance-vus", type=int, default=8)
    args = parser.parse_args()
    if args.endurance_jobs < 1 or not 1 <= args.endurance_vus <= 128:
        parser.error("job count must be positive and endurance VUs between 1 and 128")
    environment = os.environ.copy()
    environment.update(
        {
            "FORGE_RUN_INTEGRATION": "1",
            "FORGE_ENVIRONMENT": "development",
            "FORGE_API_KEY_PEPPER": "development-only-change-me",
            "FORGE_POSTGRES_URL": "postgresql+asyncpg://forge:forge@127.0.0.1:54321/forge",
            "FORGE_REDIS_URL": "redis://127.0.0.1:56379/0",
            "FORGE_OBJECT_STORE_ENDPOINT_URL": "http://127.0.0.1:59000",
            "FORGE_OBJECT_STORE_ACCESS_KEY": "forge",
            "FORGE_OBJECT_STORE_SECRET_KEY": "development-only-change-me",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    python = [sys.executable, "-B"]
    started = datetime.now(UTC)
    steps: list[dict[str, object]] = []
    commands = [
        (
            "lint",
            [*python, "-m", "ruff", "check", "--no-cache", "src", "scripts", "tests", "migrations"],
        ),
        (
            "types",
            [
                *python,
                "-m",
                "mypy",
                "--explicit-package-bases",
                "--no-incremental",
                "--cache-dir",
                os.devnull,
                "src/forge",
                "scripts/benchmark_auth.py",
                "scripts/render_backend_evidence.py",
                "scripts/report_validation_jobs.py",
                "scripts/validate_backend.py",
                "scripts/validate_capacity.py",
                "scripts/validate_dependency_outages.py",
            ],
        ),
        ("test dependencies", ["docker", "compose", "-f", "compose.yaml", "up", "-d", "--wait"]),
        ("test migrations", [*python, "-m", "alembic", "upgrade", "head"]),
        ("all tests", [*python, "-m", "pytest", "tests", "-p", "no:cacheprovider", "-q"]),
    ]
    if not args.skip_start:
        commands.append(("validation startup", [*python, "scripts/start_observability.py"]))
    commands.extend(
        [
            ("deployment", [*python, "scripts/validate_deployment.py", "--skip-start"]),
            (
                "dependency outages",
                [*python, "scripts/validate_dependency_outages.py", "--skip-start"],
            ),
            ("worker kill", [*python, "scripts/run_worker_kill_validation.py"]),
            (
                "capacity and endurance",
                [
                    *python,
                    "scripts/validate_capacity.py",
                    "--endurance-jobs",
                    str(args.endurance_jobs),
                    "--endurance-vus",
                    str(args.endurance_vus),
                ],
            ),
        ]
    )
    try:
        for name, command in commands:
            print(f"Backend acceptance: {name}", flush=True)
            before = time.monotonic()
            code = subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode
            steps.append(
                {
                    "name": name,
                    "exit_code": code,
                    "seconds": round(time.monotonic() - before, 3),
                }
            )
            # Preserve a report and stop fault injection if the stack is broken.
            if code:
                break
    finally:
        report = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "steps": steps,
            "passed": len(steps) == len(commands) and all(step["exit_code"] == 0 for step in steps),
            "endurance_jobs_requested": args.endurance_jobs,
            "full_endurance_test": args.endurance_jobs == 100000
            and any(
                step["name"] == "capacity and endurance" and step["exit_code"] == 0
                for step in steps
            ),
            "capacity_target": (
                "Consult backend-capacity.json; correctness does not imply 1000 jobs/sec"
            ),
        }
        output = ROOT / "tests" / "reports" / "backend-acceptance.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
