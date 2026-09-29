import argparse
import asyncio
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from sqlalchemy import delete, func, select

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
    parser.add_argument("--summary-name", default="k6-summary.json")
    arguments = parser.parse_args()
    REPORTS.mkdir(parents=True, exist_ok=True)
    summary = ROOT / "load" / "results" / arguments.summary_name
    summary.unlink(missing_ok=True)
    api_key_id, credential = asyncio.run(create_benchmark_key())
    process_environment = os.environ.copy()
    process_environment["FORGE_API_HOST"] = "0.0.0.0"
    process_environment["FORGE_API_PORT"] = str(arguments.port)
    executables = [
        "forge-api.exe",
        "forge-dispatcher.exe",
        "forge-scheduler.exe",
        "forge-recovery.exe",
        "forge-worker.exe",
    ]
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[BinaryIO] = []
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
        exit_code = completed.returncode
        outstanding = asyncio.run(wait_for_jobs(api_key_id))
        if outstanding:
            print(f"warning: {outstanding} benchmark jobs remained outstanding")
        if summary.exists():
            report = json.loads(summary.read_text(encoding="utf-8"))
            metrics = report["metrics"]
            print(json.dumps(metrics, indent=2, sort_keys=True))
    finally:
        stop_processes(processes)
        for log in logs:
            log.close()
        asyncio.run(cleanup(api_key_id))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
