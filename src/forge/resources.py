from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from forge.config import Settings
from forge.domain.handlers import HandlerRegistry
from forge.handlers import create_handler_registry
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
            pool_timeout_seconds=settings.postgres_pool_timeout_seconds,
            pool_recycle_seconds=settings.postgres_pool_recycle_seconds,
        )
        return cls(
            settings=settings,
            database=database,
            sessions=async_sessionmaker(database, expire_on_commit=False),
            redis=create_redis_client(settings.redis_url),
            handlers=create_handler_registry(),
            object_store=ObjectStore(
                endpoint_url=settings.object_store_endpoint_url,
                access_key=settings.object_store_access_key,
                secret_key=settings.object_store_secret_key.get_secret_value(),
                bucket=settings.object_store_bucket,
            ),
        )

    async def close(self) -> None:
        await self.redis.aclose()
        await self.database.dispose()
