import asyncio

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from forge.config import get_settings
from forge.persistence.database import probe_database
from forge.queue.client import probe_redis
from forge.resources import RuntimeResources


async def main() -> None:
    settings = get_settings()
    resources = RuntimeResources.create(settings)
    try:
        config = Config("alembic.ini")
        expected_heads = set(ScriptDirectory.from_config(config).get_heads())
        async with resources.database.connect() as connection:
            current_heads = set(
                await connection.run_sync(
                    lambda sync_connection: MigrationContext.configure(
                        sync_connection
                    ).get_current_heads()
                )
            )
        checks = {
            "migrations": current_heads == expected_heads,
            "postgres": await probe_database(resources.database),
            "redis": await probe_redis(resources.redis),
            "object_store": await resources.object_store.probe(),
            "handlers": bool(list(resources.handlers)),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise SystemExit(f"startup checks failed: {', '.join(failed)}")
        print(f"startup checks passed; migration heads: {', '.join(sorted(current_heads))}")
    finally:
        await resources.close()


if __name__ == "__main__":
    asyncio.run(main())
