import pytest

from forge.cli import build_parser, main


def test_key_creation_accepts_a_custom_rate_policy() -> None:
    arguments = build_parser().parse_args(
        [
            "key",
            "create",
            "--name",
            "load test",
            "--scope",
            "jobs:write",
            "--rate-limit-requests",
            "10000",
            "--rate-limit-window-seconds",
            "60",
        ]
    )

    assert arguments.rate_limit_requests == 10000
    assert arguments.rate_limit_window_seconds == 60


def test_key_creation_rejects_a_nonpositive_rate_policy() -> None:
    with pytest.raises(SystemExit, match="positive integers"):
        main(
            [
                "key",
                "create",
                "--name",
                "invalid",
                "--scope",
                "jobs:write",
                "--rate-limit-requests",
                "0",
            ]
        )
