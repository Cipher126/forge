import asyncio

from forge.schemas.handlers import GenerateReportPayload, WaitPayload


async def generate_report(payload: GenerateReportPayload) -> dict[str, str]:
    """Return deterministic demo output until a real artifact store is introduced."""
    return {
        "report_id": payload.report_id,
        "format": payload.format,
        "artifact": f"reports/{payload.report_id}.{payload.format}",
    }


async def wait(payload: WaitPayload) -> dict[str, str | float]:
    """Provide deterministic long-running work for lifecycle demonstrations."""
    await asyncio.sleep(payload.duration_seconds)
    return {
        "task_id": payload.task_id,
        "duration_seconds": payload.duration_seconds,
    }
