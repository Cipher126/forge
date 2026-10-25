from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from forge.config import Settings
from forge.domain.handlers import HandlerRegistry
from forge.handlers import create_handler_registry
from forge.observability.transitions import ObservedSession
from forge.persistence.database import create_database_engine
from forge.persistence.object_store import ObjectStore
from forge.queue.client import create_redis_client


@dataclass(slots=True)
class RuntimeResources:
    settings: Settings
    database: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    handlers: HandlerRegistry
    object_store: ObjectStore

    @classmethod
    def create(cls, settings: Settings) -> RuntimeResources:
        database = create_database_engine(
            settings.postgres_url,
            pool_size=settings.postgres_pool_size,
            max_overflow=settings.postgres_max_overflow,
            connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
            pool_timeout_seconds=settings.postgres_pool_timeout_seconds,
            pool_recycle_seconds=settings.postgres_pool_recycle_seconds,
        )
        return cls(
            settings=settings,
            database=database,
            sessions=async_sessionmaker(
                database,
                expire_on_commit=False,
                sync_session_class=ObservedSession,
            ),
            redis=create_redis_client(
                settings.redis_url,
                connect_timeout_seconds=settings.redis_connect_timeout_seconds,
                operation_timeout_seconds=settings.redis_operation_timeout_seconds,
            ),
            handlers=create_handler_registry(),
            object_store=ObjectStore(
                endpoint_url=settings.object_store_endpoint_url,
                access_key=settings.object_store_access_key,
                secret_key=settings.object_store_secret_key.get_secret_value(),
                bucket=settings.object_store_bucket,
                connect_timeout_seconds=settings.object_store_connect_timeout_seconds,
                read_timeout_seconds=settings.object_store_read_timeout_seconds,
                max_attempts=settings.object_store_max_attempts,
                operation_timeout_seconds=settings.object_store_operation_timeout_seconds,
            ),
        )

    async def close(self) -> None:
        await self.redis.aclose()
        await self.database.dispose()
