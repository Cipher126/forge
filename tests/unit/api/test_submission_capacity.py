import asyncio
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from forge.api.admission import SubmissionGate
from forge.api.error_handlers import submission_capacity_exceeded_handler
from forge.api.middleware.submission_capacity import enforce_submission_capacity
from forge.errors.capacity import SubmissionCapacityExceededError


async def test_submission_gate_rejects_excess_work_and_releases_its_slot() -> None:
    gate = SubmissionGate(limit=1, retry_after_seconds=3)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def occupy_slot() -> None:
        async with gate.slot():
            entered.set()
            await release.wait()

    occupant = asyncio.create_task(occupy_slot())
    await entered.wait()

    with pytest.raises(SubmissionCapacityExceededError) as captured:
        async with gate.slot():
            pytest.fail("an excess request acquired a submission slot")

    assert captured.value.active_submissions == 1
    assert captured.value.limit == 1
    assert captured.value.retry_after_seconds == 3
    assert gate.active == 1

    release.set()
    await occupant
    assert gate.active == 0

    async with gate.slot():
        assert gate.active == 1
    assert gate.active == 0


async def test_submission_gate_releases_slot_after_failure() -> None:
    gate = SubmissionGate(limit=1, retry_after_seconds=1)

    with pytest.raises(RuntimeError, match="submission failed"):
        async with gate.slot():
            raise RuntimeError("submission failed")

    assert gate.active == 0


async def test_submission_capacity_error_has_retryable_http_contract() -> None:
    request = Request({"type": "http", "method": "POST", "path": "/v1/jobs", "headers": []})
    error = SubmissionCapacityExceededError(
        active_submissions=8,
        limit=8,
        retry_after_seconds=2,
    )

    response = await submission_capacity_exceeded_handler(request, error)

    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert json.loads(response.body) == {
        "error": {
            "code": "submission_capacity_exceeded",
            "message": "Forge is handling the maximum number of concurrent submissions",
            "details": {"active_submissions": 8, "limit": 8},
        }
    }


async def test_submission_middleware_holds_slot_for_the_entire_route() -> None:
    gate = SubmissionGate(limit=1, retry_after_seconds=1)
    entered = asyncio.Event()
    release = asyncio.Event()
    app = FastAPI()
    app.state.submission_gate = gate
    app.add_exception_handler(
        SubmissionCapacityExceededError,
        submission_capacity_exceeded_handler,  # type: ignore[arg-type]
    )
    app.middleware("http")(enforce_submission_capacity)

    @app.post("/v1/jobs")
    async def slow_submission() -> dict[str, str]:
        entered.set()
        await release.wait()
        return {"status": "accepted"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        occupant = asyncio.create_task(client.post("/v1/jobs"))
        await entered.wait()

        rejected = await client.post("/v1/jobs")

        assert rejected.status_code == 503
        assert rejected.json()["error"]["code"] == "submission_capacity_exceeded"
        assert gate.active == 1
        release.set()
        response = await occupant

    assert response.status_code == 200
    assert gate.active == 0
