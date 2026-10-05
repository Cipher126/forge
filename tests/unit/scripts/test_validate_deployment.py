import argparse
from collections import Counter

import pytest

from scripts.validate_deployment import build_checks, parse_duration_seconds


def passing_summary() -> dict[str, object]:
    return {
        "metrics": {
            "accepted_jobs": {"count": 250},
            "iterations": {"count": 251},
            "dropped_iterations": {"count": 0},
        }
    }


def test_duration_parser_accepts_k6_units() -> None:
    assert parse_duration_seconds("25s") == 25
    assert parse_duration_seconds("2m") == 120
    assert parse_duration_seconds("1h") == 3600


def test_checks_require_every_accepted_job_to_succeed() -> None:
    checks = build_checks(
        k6_exit_code=0,
        summary=passing_summary(),
        expected_jobs=250,
        statuses=Counter({"succeeded": 249, "failed": 1}),
        operations={
            "active_workers": 2,
            "queues": {"queued_total": 0, "processing_total": 0},
        },
        services=set(),
        startup_check=True,
        integrity_check=True,
    )

    assert checks["all_validation_jobs_found"] is True
    assert checks["all_validation_jobs_succeeded"] is False
    assert checks["all_services_running"] is False
    assert checks["planned_arrivals_accepted"] is True


def test_duration_parser_rejects_ambiguous_values() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="duration must use k6 form"):
        parse_duration_seconds("25")
