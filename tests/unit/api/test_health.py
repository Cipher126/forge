import pytest
from httpx import ASGITransport, AsyncClient

from forge.api.app import create_app


@pytest.mark.asyncio
async def test_health_reports_live_process() -> None:
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/health",
            headers={"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.headers["traceparent"].startswith(f"00-{trace_id}-")
    assert response.json() == {"status": "ok"}
