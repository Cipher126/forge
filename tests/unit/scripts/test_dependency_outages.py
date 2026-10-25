from unittest.mock import patch

import pytest

from scripts.validate_dependency_outages import (
    dependency_stopped,
    require_dependency_error,
    submit_after_recovery,
)
from scripts.validate_deployment import ValidationFailure


def test_dependency_restored_after_probe_failure() -> None:
    with patch("scripts.validate_dependency_outages.compose") as compose:
        with pytest.raises(RuntimeError, match="probe failed"), dependency_stopped("postgres"):
            raise RuntimeError("probe failed")
    assert compose.call_args_list[-1].args == ("start", "postgres")


def test_dependency_restored_after_partial_stop_failure() -> None:
    with patch("scripts.validate_dependency_outages.compose") as compose:
        compose.side_effect = [RuntimeError("partial stop"), ""]
        with pytest.raises(RuntimeError, match="partial stop"), dependency_stopped("object-store"):
            pytest.fail("Cannot probe when stop failed")
    assert compose.call_args_list[-1].args == ("start", "object-store")


def test_recovery_retry_preserves_idempotency_key_and_response_evidence() -> None:
    rejected = {"status": 503, "body": {"error": {"code": "dependency_unavailable"}}}
    accepted = {"status": 202, "body": {"id": "job-id"}}
    with (
        patch(
            "scripts.validate_dependency_outages.submit", side_effect=[rejected, accepted]
        ) as send,
        patch("scripts.validate_dependency_outages.time.sleep"),
    ):
        result, evidence = submit_after_recovery("credential", {"type": "demo"}, "same-key")
    assert result == accepted
    assert evidence == [rejected, accepted]
    assert all(call.args[2] == "same-key" for call in send.call_args_list)


def test_recovery_does_not_retry_unexpected_server_errors() -> None:
    with (
        patch(
            "scripts.validate_dependency_outages.submit", return_value={"status": 500, "body": {}}
        ),
        pytest.raises(ValidationFailure, match="Submission did not recover"),
    ):
        submit_after_recovery("credential", {}, "key")


@pytest.mark.parametrize(
    "seconds,status,dependency", [(6, 503, "postgres"), (1, 502, "postgres"), (1, 503, "redis")]
)
def test_slow_or_wrong_dependency_response_fails_validation(
    seconds: int,
    status: int,
    dependency: str,
) -> None:
    with pytest.raises(ValidationFailure):
        require_dependency_error(
            {
                "seconds": seconds,
                "status": status,
                "retry_after": "1",
                "body": {
                    "error": {
                        "code": "dependency_unavailable",
                        "details": {"dependency": dependency},
                    }
                },
            },
            "postgres",
        )
