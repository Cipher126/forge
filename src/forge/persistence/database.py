from asyncpg import exceptions as asyncpg_errors  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, DisconnectionError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DRIVER_CONNECTION_ERRORS: tuple[type[Exception], ...] = (
    asyncpg_errors.PostgresConnectionError,
    asyncpg_errors.CannotConnectNowError,
    asyncpg_errors.AdminShutdownError,
    asyncpg_errors.CrashShutdownError,
    asyncpg_errors.TooManyConnectionsError,
)
# Connection establishment can expose raw asyncpg exceptions before SQLAlchemy
# adapts them. Keep syntax/constraint errors outside the driver connection family.
DATABASE_RETRY_ERRORS: tuple[type[Exception], ...] = (
    DBAPIError,
    DisconnectionError,
    SQLAlchemyTimeoutError,
    OSError,
    *DRIVER_CONNECTION_ERRORS,
)


def create_database_engine(
    url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
    connect_timeout_seconds: float = 0.5,
    pool_timeout_seconds: float = 1.0,
    pool_recycle_seconds: int = 1800,
) -> AsyncEngine:
    return create_async_engine(
        url,
        connect_args={"timeout": connect_timeout_seconds},
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_seconds,
        pool_recycle=pool_recycle_seconds,
    )


async def probe_database(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
