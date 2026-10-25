"""Exercise the real dashboard through its proxy using an expiring, revoked test key."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import validate_deployment as validation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:13010")
    args = parser.parse_args()
    parsed_url = urlparse(args.base_url)
    if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        parser.error("browser validation only accepts a local Forge dashboard URL")
    admin = validation.get_admin_token(may_rotate=False)
    issued = validation.request_json(
        "POST",
        "/v1/admin/api-keys",
        token=admin,
        body={
            "name": "dashboard-browser-validation",
            "scopes": ["admin"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "rate_limit_requests": 10000,
            "rate_limit_window_s": 60,
        },
    )
    report: dict[str, object] = {
        "passed": False,
        "credential_revoked": False,
        "base_url": args.base_url,
    }
    try:
        environment = os.environ.copy()
        environment.update(FORGE_E2E_KEY=issued["api_key"], FORGE_E2E_URL=args.base_url)
        code = subprocess.run(
            [
                shutil.which("npm.cmd") or shutil.which("npm") or "npm",
                "run",
                "test:e2e",
                "--",
                "live.spec.ts",
            ],
            cwd=ROOT / "dashboard",
            env=environment,
            check=False,
        ).returncode
        report["browser_exit_code"] = code
        report["passed"] = code == 0
    finally:
        validation.revoke_validation_key(admin, issued["id"])
        report["credential_revoked"] = True
        destination = ROOT / "tests/reports/dashboard-live-validation.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
