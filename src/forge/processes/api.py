import uvicorn

from forge.config import get_settings
from forge.observability.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, service="api")
    uvicorn.run(
        "forge.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
