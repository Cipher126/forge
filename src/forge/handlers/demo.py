from forge.schemas.handlers import GenerateReportPayload


async def generate_report(payload: GenerateReportPayload) -> dict[str, str]:
    """Return deterministic demo output until a real artifact store is introduced."""
    return {
        "report_id": payload.report_id,
        "format": payload.format,
        "artifact": f"reports/{payload.report_id}.{payload.format}",
    }
