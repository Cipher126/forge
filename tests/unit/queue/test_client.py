from forge.queue.client import create_redis_client


async def test_redis_client_uses_bounded_network_timeouts() -> None:
    client = create_redis_client(
        "redis://localhost:6379/0",
        connect_timeout_seconds=0.25,
        operation_timeout_seconds=0.75,
    )
    try:
        connection_options = client.connection_pool.connection_kwargs
        assert connection_options["socket_connect_timeout"] == 0.25
        assert connection_options["socket_timeout"] == 0.75
        assert connection_options["decode_responses"] is True
    finally:
        await client.aclose()
