from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_COMPOSE = ROOT / "deploy" / "compose.validation.yaml"
OBSERVABILITY_COMPOSE = ROOT / "deploy" / "compose.observability.yaml"
TOKEN_FILE = ROOT / "deploy" / "observability" / "metrics-token"


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
        check=True,
        text=True,
        capture_output=capture_output,
    )


def provision_metrics_token() -> None:
    result = compose(
        "run",
        "--rm",
        "--no-deps",
        "api-1",
        "forge",
        "key",
        "create",
        "--name",
        "local-prometheus",
        "--scope",
        "admin",
        "--rate-limit-requests",
        "10000",
        "--rate-limit-window-seconds",
        "60",
        capture_output=True,
    )
    credential = result.stdout.strip().splitlines()[-1]
    if not credential.startswith("forge_live_"):
        raise RuntimeError("Forge did not return an API credential for Prometheus")
    TOKEN_FILE.write_text(f"{credential}\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start Forge's validation and local observability services."
    )
    parser.add_argument(
        "--rotate-token",
        action="store_true",
        help="issue a new Prometheus API key even if a local token file exists",
    )
    args = parser.parse_args()

    compose("up", "-d", "--build", "postgres", "redis", "object-store", "migrate")
    if args.rotate_token or not TOKEN_FILE.exists():
        provision_metrics_token()
    compose(
        "up",
        "-d",
        "api-1",
        "api-2",
        "proxy",
        "dispatcher",
        "worker-1",
        "worker-2",
        "scheduler",
        "recovery",
        "prometheus",
        "tempo",
        "grafana",
    )

    print("Forge API:   http://127.0.0.1:18080")
    print("Grafana:     http://127.0.0.1:13000/d/forge-overview")
    print("Prometheus:  http://127.0.0.1:19090")
    print("Tempo ready: http://127.0.0.1:13200/ready (view traces through Grafana)")


if __name__ == "__main__":
    main()
