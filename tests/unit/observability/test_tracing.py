from forge.observability.tracing import configure_tracing, parse_traceparent


def test_traceparent_parser_accepts_valid_w3c_context() -> None:
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"

    assert parse_traceparent(f"00-{trace_id}-00f067aa0ba902b7-01") == (trace_id, "01")


def test_traceparent_parser_rejects_invalid_and_zero_trace_ids() -> None:
    assert parse_traceparent("not-a-trace") is None
    assert parse_traceparent("00-00000000000000000000000000000000-00f067aa0ba902b7-01") is None


def test_otlp_export_is_disabled_without_creating_provider() -> None:
    assert (
        configure_tracing(
            service="test",
            enabled=False,
            endpoint="http://127.0.0.1:4318/v1/traces",
            sample_ratio=1.0,
        )
        is None
    )
