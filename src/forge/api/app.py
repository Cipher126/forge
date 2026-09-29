from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from forge.api.error_handlers import register_error_handlers
from forge.api.middleware.request_logging import log_request
from forge.api.middleware.request_size import enforce_request_size
from forge.api.routes.api_keys import router as api_keys_router
from forge.api.routes.audit import router as audit_router
from forge.api.routes.handlers import router as handlers_router
from forge.api.routes.health import router as health_router
from forge.api.routes.jobs import router as jobs_router
from forge.api.routes.operations import router as operations_router
from forge.config import get_settings
from forge.observability.logging import configure_logging
from forge.resources import RuntimeResources


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, service="api")
    resources = RuntimeResources.create(settings)
    app.state.resources = resources
    try:
        yield
    finally:
        await resources.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Forge API",
        version="0.1.0",
        description="API-driven distributed job execution platform.",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(audit_router)
    app.include_router(handlers_router)
    app.include_router(api_keys_router)
    app.include_router(jobs_router)
    app.include_router(operations_router)
    register_error_handlers(app)
    app.middleware("http")(enforce_request_size)
    app.middleware("http")(log_request)
    return app


app = create_app()
