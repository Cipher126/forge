import argparse
import asyncio
import json
import os
import subprocess
import threading
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy import text as sql_text

from forge.config import get_settings
from forge.persistence.models import ApiKey, Job
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "tests" / "reports"
K6_IMAGE = (
    "grafana/k6@sha256:23f2279054c3e01535455c4d92914a625df12aeb631ea0e3ea7a43f96bcbc843"
)


async def create_benchmark_key() -> tuple[UUID, str]:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    try:
        async with resources.sessions.begin() as session:
            issued = await issue_api_key(
                session,
                name="local benchmark",
                scopes={"jobs:write"},
                pepper=settings.api_key_pepper.get_secret_value(),
                rate_limit_requests=1_000_000,
                rate_limit_window_s=60,
            )
        return issued.record.id, issued.credential
    finally:
        await resources.close()


async def wait_for_jobs(api_key_id: UUID, timeout_seconds: int = 60) -> int:
    resources = RuntimeResources.create(get_settings())
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            async with resources.sessions() as session:
                outstanding = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(Job)
                        .where(
                            Job.api_key_id == api_key_id,
                            Job.status.not_in(
                                ["succeeded", "failed", "dead_lettered", "cancelled"]
                            ),
                        )
                    )
                    or 0
                )
            if outstanding == 0:
                return 0
            await asyncio.sleep(0.25)
        return outstanding
    finally:
        await resources.close()


async def cleanup(api_key_id: UUID) -> None:
    resources = RuntimeResources.create(get_settings())
    try:
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.api_key_id == api_key_id))
            await session.execute(delete(ApiKey).where(ApiKey.id == api_key_id))
    finally:
        await resources.close()


async def sample_postgres(stop: threading.Event) -> dict[str, object]:
    resources = RuntimeResources.create(get_settings())
    samples = 0
    peak_connections = 0
    peak_active = 0
    waits: Counter[str] = Counter()
    try:
        while not stop.is_set():
            async with resources.database.connect() as connection:
                rows = (
                    await connection.execute(
                        sql_text(
                            "SELECT state, wait_event_type, wait_event "
                            "FROM pg_stat_activity "
                            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                        )
                    )
                ).all()
            samples += 1
            peak_connections = max(peak_connections, len(rows))
            peak_active = max(peak_active, sum(row.state == "active" for row in rows))
            for row in rows:
                if row.wait_event_type:
                    waits[f"{row.wait_event_type}:{row.wait_event}"] += 1
            await asyncio.sleep(0.1)
    finally:
        await resources.close()
    return {
        "samples": samples,
        "peak_connections": peak_connections,
        "peak_active": peak_active,
        "observed_waits": dict(waits.most_common()),
    }


def postgres_sampler(stop: threading.Event, result: dict[str, object]) -> None:
    result.update(asyncio.run(sample_postgres(stop)))


def wait_for_api(port: int, timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f"Forge API did not become healthy: {last_error!r}")


def stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rate", type=int, default=10)
    parser.add_argument("--duration", default="20s")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--api-workers", type=int, default=1)
    parser.add_argument(
        "--processes",
        choices=("api-only", "all"),
        default="all",
        help="Run only durable API acceptance or the complete local process topology.",
    )
    parser.add_argument("--summary-name", default="k6-summary.json")
    arguments = parser.parse_args()
    REPORTS.mkdir(parents=True, exist_ok=True)
    summary = ROOT / "load" / "results" / arguments.summary_name
    summary.unlink(missing_ok=True)
    api_key_id, credential = asyncio.run(create_benchmark_key())
    process_environment = os.environ.copy()
    process_environment["FORGE_API_HOST"] = "0.0.0.0"
    process_environment["FORGE_API_PORT"] = str(arguments.port)
    process_environment["FORGE_API_WORKERS"] = str(arguments.api_workers)
    executables = ["forge-api.exe"]
    if arguments.processes == "all":
        executables.extend(
            [
                "forge-dispatcher.exe",
                "forge-scheduler.exe",
                "forge-recovery.exe",
                "forge-worker.exe",
            ]
        )
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[BinaryIO] = []
    sampler_stop = threading.Event()
    database_diagnostics: dict[str, object] = {}
    sampler = threading.Thread(
        target=postgres_sampler,
        args=(sampler_stop, database_diagnostics),
        daemon=True,
    )
    exit_code = 1
    try:
        for executable in executables:
            log = (REPORTS / f"benchmark-{executable}.log").open("wb")
            logs.append(log)
            processes.append(
                subprocess.Popen(
                    [str(ROOT / ".venv" / "Scripts" / executable)],
                    cwd=ROOT,
                    env=process_environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
        wait_for_api(arguments.port)
        sampler.start()
        docker_environment = os.environ.copy()
        docker_environment.update(
            {
                "API_KEY": credential,
                "BASE_URL": f"http://host.docker.internal:{arguments.port}",
                "RATE": str(arguments.rate),
                "DURATION": arguments.duration,
                "SUMMARY_PATH": f"/load/results/{arguments.summary_name}",
            }
        )
        command = [
            "docker",
            "run",
            "--rm",
            "-e",
            "API_KEY",
            "-e",
            "BASE_URL",
            "-e",
            "RATE",
            "-e",
            "DURATION",
            "-e",
            "SUMMARY_PATH",
            "-v",
            f"{ROOT / 'load'}:/load",
            K6_IMAGE,
            "run",
            "/load/k6/submit-jobs.js",
        ]
        completed = subprocess.run(command, env=docker_environment, cwd=ROOT, check=False)
        sampler_stop.set()
        sampler.join(timeout=5)
        exit_code = completed.returncode
        if arguments.processes == "all":
            outstanding = asyncio.run(wait_for_jobs(api_key_id))
            if outstanding:
                print(f"warning: {outstanding} benchmark jobs remained outstanding")
        if summary.exists():
            report = json.loads(summary.read_text(encoding="utf-8"))
            report["topology"] = {
                "processes": arguments.processes,
                "api_workers": arguments.api_workers,
            }
            report["postgres"] = database_diagnostics
            summary.write_text(f"{json.dumps(report, indent=2)}\n", encoding="utf-8")
            metrics = report["metrics"]
            print(json.dumps(metrics, indent=2, sort_keys=True))
            print(json.dumps({"postgres": database_diagnostics}, indent=2, sort_keys=True))
    finally:
        sampler_stop.set()
        if sampler.is_alive():
            sampler.join(timeout=5)
        stop_processes(processes)
        for log in logs:
            log.close()
        asyncio.run(cleanup(api_key_id))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
