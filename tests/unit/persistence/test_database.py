from typing import Any

from forge.persistence import database


def test_database_engine_applies_pool_limits(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def fake_create_async_engine(url: str, **options: object) -> object:
        captured["url"] = url
        captured.update(options)
        return sentinel

    monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)

    engine = database.create_database_engine(
        "postgresql+asyncpg://forge:test@postgres/forge",
        pool_size=7,
        max_overflow=3,
        pool_timeout_seconds=12,
        pool_recycle_seconds=600,
    )

    assert engine is sentinel
    assert captured == {
        "url": "postgresql+asyncpg://forge:test@postgres/forge",
        "pool_pre_ping": True,
        "pool_size": 7,
        "max_overflow": 3,
        "pool_timeout": 12,
        "pool_recycle": 600,
    }
