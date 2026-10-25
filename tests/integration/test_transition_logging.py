import os
from unittest.mock import Mock
from uuid import uuid7

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from forge.config import get_settings
from forge.observability import transitions
from forge.persistence.models import ApiKey, Job, JobTransition
from forge.resources import RuntimeResources
from forge.security.api_keys import issue_api_key

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("FORGE_RUN_INTEGRATION") != "1",
        reason="set FORGE_RUN_INTEGRATION=1 to use local Forge services",
    ),
]


async def test_transition_logs_follow_outer_commit_and_discard_savepoint_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = RuntimeResources.create(get_settings())
    logger = Mock()
    monkeypatch.setattr(transitions, "logger", logger)
    key_id = uuid7()
    job_id = uuid7()
    try:
        async with resources.sessions.begin() as session:
            owner = await issue_api_key(
                session,
                name="transition logging",
                scopes={"jobs:write"},
                pepper=get_settings().api_key_pepper.get_secret_value(),
            )
            key_id = owner.record.id
            session.add(
                Job(
                    id=job_id,
                    api_key_id=key_id,
                    job_type="demo.generate_report",
                    handler_version="v1",
                    status="queued",
                    priority="normal",
                    payload={"report_id": "log-test", "format": "json"},
                    max_attempts=1,
                    attempt_count=0,
                )
            )
        async with resources.sessions() as session:
            async with session.begin():
                async with session.begin_nested():
                    session.add(
                        JobTransition(
                            job_id=job_id,
                            from_status=None,
                            to_status="queued",
                            reason="retained",
                            actor_type="api",
                            actor_id="test",
                            details={"payload": "must never appear in logs"},
                        )
                    )
                    await session.flush()
                assert logger.info.call_count == 0
                with pytest.raises(ValueError):
                    async with session.begin_nested():
                        session.add(
                            JobTransition(
                                job_id=job_id,
                                from_status="queued",
                                to_status="cancelled",
                                reason="rolled_back",
                                actor_type="api",
                                actor_id="test",
                            )
                        )
                        await session.flush()
                        raise ValueError("abort savepoint")
                assert logger.info.call_count == 0
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(
                            JobTransition(
                                job_id=uuid7(),
                                from_status=None,
                                to_status="queued",
                                reason="invalid_job_reference",
                                actor_type="api",
                                actor_id="test",
                            )
                        )
                        await session.flush()
                assert logger.info.call_count == 0
            assert logger.info.call_count == 1
            assert logger.info.call_args.kwargs["reason"] == "retained"
            assert "details" not in logger.info.call_args.kwargs
            session.add(
                JobTransition(
                    job_id=job_id,
                    from_status="queued",
                    to_status="cancelled",
                    reason="outer_rollback",
                    actor_type="api",
                    actor_id="test",
                )
            )
            await session.flush()
            await session.rollback()
            assert logger.info.call_count == 1
            logger.info.side_effect = RuntimeError("logging sink unavailable")
            async with session.begin():
                session.add(
                    JobTransition(
                        job_id=job_id,
                        from_status="queued",
                        to_status="cancelled",
                        reason="commit_survives_logging_failure",
                        actor_type="api",
                        actor_id="test",
                    )
                )
            assert logger.info.call_count == 2
    finally:
        async with resources.sessions.begin() as session:
            await session.execute(delete(Job).where(Job.id == job_id))
            await session.execute(delete(ApiKey).where(ApiKey.id == key_id))
        await resources.close()
