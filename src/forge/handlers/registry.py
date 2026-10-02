from forge.domain.handlers import ExecutionMode, HandlerRegistry
from forge.handlers.demo import generate_report, wait
from forge.schemas.handlers import GenerateReportPayload, WaitPayload


def create_handler_registry() -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register(
        job_type="demo.generate_report",
        version="1",
        payload_model=GenerateReportPayload,
        handler=generate_report,
        execution_mode=ExecutionMode.ASYNC,
        timeout_seconds=30,
        default_max_attempts=3,
        active=True,
    )
    registry.register(
        job_type="demo.wait",
        version="1",
        payload_model=WaitPayload,
        handler=wait,
        execution_mode=ExecutionMode.ASYNC,
        timeout_seconds=180,
        default_max_attempts=3,
        active=True,
    )
    return registry
