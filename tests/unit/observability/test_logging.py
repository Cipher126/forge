from forge.observability.logging import REDACTED, redact_sensitive_fields


def test_sensitive_log_fields_and_embedded_credentials_are_redacted() -> None:
    event = redact_sensitive_fields(
        object(),
        "info",
        {
            "event": "request failed with Bearer forge_live_public_secret-value",
            "payload": {"report": "private"},
            "context": {
                "password": "do-not-log",
                "safe": "visible",
                "items": [{"token": "also-private"}],
            },
        },
    )

    assert event["event"] == f"request failed with Bearer {REDACTED}"
    assert event["payload"] == REDACTED
    assert event["context"] == {
        "password": REDACTED,
        "safe": "visible",
        "items": [{"token": REDACTED}],
    }
