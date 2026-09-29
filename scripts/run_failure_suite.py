import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "tests" / "reports"


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    junit_path = REPORTS / "failure-suite.xml"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-m",
        "failure",
        f"--junitxml={junit_path}",
    ]
    environment = os.environ.copy()
    environment["FORGE_RUN_INTEGRATION"] = "1"
    started_at = datetime.now(UTC)
    started = monotonic()
    completed = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    duration = monotonic() - started

    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    if junit_path.exists():
        root = ElementTree.parse(junit_path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        for suite in suites:
            for key in counts:
                counts[key] += int(suite.attrib.get(key, 0))

    report = {
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "duration_seconds": round(duration, 3),
        "exit_code": completed.returncode,
        **counts,
    }
    (REPORTS / "failure-suite.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
