from forge.application.operations import render_prometheus_metrics
from forge.schemas.operations import JobTypePermitUsage, OperationalRates, QueueStatistics


def test_prometheus_metrics_include_rates_dead_letters_and_permit_usage() -> None:
    output = render_prometheus_metrics(
        {
            "scheduled": 0,
            "queued": 2,
            "running": 1,
            "retry_scheduled": 0,
            "cancellation_requested": 0,
            "succeeded": 7,
            "failed": 1,
            "dead_lettered": 3,
            "cancelled": 0,
        },
        QueueStatistics(
            queued_by_priority={"high": 1, "normal": 1, "low": 0},
            queued_total=2,
            processing_by_worker={},
            processing_total=0,
            durable_outstanding=3,
        ),
        2,
        {
            "outbox_unpublished": 0,
            "attempts_running": 1,
            "jobs_due": 0,
            "attempt_leases_expired": 0,
        },
        OperationalRates(
            window_minutes=5,
            enqueued_per_minute=2.4,
            completed_per_minute=2.0,
            failure_ratio=0.25,
        ),
        {"demo.generate_report": JobTypePermitUsage(used=1, limit=2)},
    )

    assert "forge_jobs_enqueued_rate_per_minute 2.4" in output
    assert "forge_jobs_completed_rate_per_minute 2.0" in output
    assert "forge_job_failure_ratio 0.25" in output
    assert "forge_metrics_rate_window_minutes 5" in output
    assert "forge_dead_letter_jobs 3" in output
    assert (
        'forge_job_type_permits_used{job_type="demo.generate_report"} 1' in output
    )
    assert (
        'forge_job_type_permits_limit{job_type="demo.generate_report"} 2' in output
    )
    assert (
        'forge_job_type_permit_utilization_ratio{job_type="demo.generate_report"} 0.5'
        in output
    )


def test_prometheus_job_type_labels_are_escaped() -> None:
    output = render_prometheus_metrics(
        {"dead_lettered": 0},
        QueueStatistics(
            queued_by_priority={},
            queued_total=0,
            processing_by_worker={},
            processing_total=0,
            durable_outstanding=0,
        ),
        0,
        {
            "outbox_unpublished": 0,
            "attempts_running": 0,
            "jobs_due": 0,
            "attempt_leases_expired": 0,
        },
        OperationalRates(5, 0.0, 0.0, 0.0),
        {'quoted"type': JobTypePermitUsage(used=0, limit=1)},
    )

    assert 'job_type="quoted\\"type"' in output
