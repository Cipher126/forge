"""Render portable SVG evidence from the current backend benchmark reports."""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "docs" / "benchmarks" / "figures"


def bars(title: str, unit: str, values: list[tuple[str, float]], path: Path) -> None:
    maximum = max(value for _, value in values) or 1
    height = 100 + len(values) * 55
    items = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="{height}" '
        f'viewBox="0 0 1100 {height}" role="img">',
        f"<title>{html.escape(title)}</title>",
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="30" y="35" font-family="sans-serif" font-size="22">{html.escape(title)}</text>',
    ]
    for index, (label, value) in enumerate(values):
        y = 65 + index * 55
        items.extend(
            [
                f'<text x="30" y="{y + 22}" font-family="sans-serif">{html.escape(label)}</text>',
                f'<rect x="310" y="{y}" width="{600 * value / maximum:.2f}" '
                'height="32" fill="#2563eb"/>',
                f'<text x="{320 + 600 * value / maximum:.2f}" y="{y + 22}" '
                f'font-family="sans-serif">{value:.1f} {html.escape(unit)}</text>',
            ]
        )
    items.append("</svg>")
    path.write_text("\n".join(items) + "\n", encoding="utf-8")


def main() -> None:
    report = json.loads((ROOT / "load/results/backend-capacity.json").read_text(encoding="utf-8"))
    DESTINATION.mkdir(parents=True, exist_ok=True)
    bars(
        "Submission p95 during arrival-rate probes",
        "ms",
        [
            (f"{step['rate']} arrivals/sec", step["metrics"]["http_request_duration"]["p(95)"])
            for step in report["capacity_steps"]
        ],
        DESTINATION / "2026-10-05-capacity-latency.svg",
    )
    timings = report["endurance"]["durable"]["timings"]
    bars(
        "100,000-job run: waiting and completion (p95)",
        "minutes",
        [
            (name.replace("_", " "), values["p95_seconds"] / 60)
            for name, values in timings.items()
            if name in ("queue_wait", "end_to_end") and values["p95_seconds"] is not None
        ],
        DESTINATION / "2026-10-05-endurance-latency.svg",
    )
    bars(
        "100,000-job run: database claim work and execution (p95)",
        "ms",
        [
            (name.replace("_", " "), values["p95_seconds"] * 1000)
            for name, values in timings.items()
            if name in ("claim", "execution") and values["p95_seconds"] is not None
        ],
        DESTINATION / "2026-10-05-endurance-work.svg",
    )
    bars(
        "100,000-job run: queue wait by priority (p95)",
        "minutes",
        [
            (priority["priority"], priority["queue_wait_p95_seconds"] / 60)
            for priority in report["endurance"]["durable"]["priorities"]
        ],
        DESTINATION / "2026-10-05-priority-wait.svg",
    )
    source = ROOT / "load/results/backend-queue.svg"
    (DESTINATION / "2026-10-05-backend-queue.svg").write_text(
        source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    recovery_path = ROOT / "tests/reports/worker-kill-validation.json"
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    if "recovery_claim_seconds" in recovery:
        bars(
            "Worker kill: confirmed stop to replacement claim",
            "seconds",
            [("Replacement attempt", recovery["recovery_claim_seconds"])],
            DESTINATION / "2026-10-05-worker-recovery.svg",
        )
    print(f"Rendered benchmark figures in {DESTINATION}")


if __name__ == "__main__":
    main()
