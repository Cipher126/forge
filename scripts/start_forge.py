"""Start Forge's local Linux stack, observability, and same-origin dashboard."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    subprocess.run([sys.executable, "-B", "scripts/start_observability.py"], cwd=ROOT, check=True)
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "deploy/compose.validation.yaml",
            "-f",
            "deploy/compose.observability.yaml",
            "-f",
            "deploy/compose.dashboard.yaml",
            "up",
            "-d",
            "--build",
            "--no-deps",
            "--wait",
            "dashboard",
        ],
        cwd=ROOT,
        check=True,
    )
    print("Forge dashboard: http://127.0.0.1:13010")
    print("Create a dedicated key using docs/dashboard.md; credentials are never embedded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
