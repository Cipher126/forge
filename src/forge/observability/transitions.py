"""Best-effort JSON mirrors of committed transition records.

PostgreSQL remains authoritative: a process can fail after commit but before logging.
"""

from typing import Any

import structlog
from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

from forge.persistence.models import JobTransition

logger = structlog.get_logger()
BUFFER = "forge_transition_logs"


class ObservedSession(Session):
    """Session whose committed job transitions are mirrored to structured logs."""


@event.listens_for(ObservedSession, "after_flush")
def collect_transitions(session: Session, _context: object) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    buffers = session.info.setdefault(BUFFER, {})
    records = buffers.setdefault(transaction, [])
    for item in session.new:
        if isinstance(item, JobTransition):
            records.append(
                {
                    "transition_id": item.id,
                    "job_id": str(item.job_id),
                    "attempt_id": str(item.attempt_id) if item.attempt_id else None,
                    "from_status": item.from_status,
                    "to_status": item.to_status,
                    "reason": item.reason,
                    "actor_type": item.actor_type,
                    "actor_id": item.actor_id,
                }
            )


@event.listens_for(ObservedSession, "after_commit")
def log_committed_transitions(session: Session) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    buffers: dict[SessionTransaction, list[dict[str, Any]]] = session.info.get(BUFFER, {})
    records = buffers.pop(transaction, [])  # type: ignore[arg-type]
    if transaction is not None and transaction.nested:
        buffers.setdefault(transaction.parent, []).extend(records)  # type: ignore[arg-type]
        return
    for record in records:
        try:
            logger.info("job_transition_committed", **record)
        except Exception:
            # Logging must never turn a successful database commit into an HTTP failure.
            pass
    session.info.pop(BUFFER, None)


@event.listens_for(ObservedSession, "after_soft_rollback")
def discard_rolled_back_transitions(session: Session, transaction: SessionTransaction) -> None:
    buffers = session.info.get(BUFFER, {})
    buffers.pop(transaction, None)
    # Flush failures use an internal child transaction. Its rollback must not
    # discard earlier outer records when a savepoint contains the failure.
    if transaction.parent is None:
        session.info.pop(BUFFER, None)
