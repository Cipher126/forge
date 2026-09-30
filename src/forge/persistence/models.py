from datetime import datetime
from typing import Any
from uuid import UUID, uuid7

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from forge.persistence.base import Base

timestamp_default = text("CURRENT_TIMESTAMP")


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint("char_length(public_id) BETWEEN 1 AND 64", name="public_id_length"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name_length"),
        CheckConstraint("octet_length(secret_digest) = 32", name="secret_digest_length"),
        CheckConstraint("pepper_version >= 1", name="pepper_version_positive"),
        CheckConstraint("cardinality(scopes) >= 1", name="scopes_not_empty"),
        CheckConstraint(
            "scopes <@ ARRAY['jobs:read', 'jobs:write', 'admin']::text[]",
            name="scopes_supported",
        ),
        CheckConstraint("rate_limit_requests >= 1", name="rate_limit_requests_positive"),
        CheckConstraint("rate_limit_window_s >= 1", name="rate_limit_window_positive"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    public_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    secret_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    pepper_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_limit_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    rate_limit_window_s: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "char_length(actor_public_id) BETWEEN 1 AND 64", name="actor_public_id_length"
        ),
        CheckConstraint("char_length(action) BETWEEN 1 AND 128", name="action_length"),
        CheckConstraint("char_length(resource_type) BETWEEN 1 AND 64", name="resource_type_length"),
        CheckConstraint("char_length(resource_id) BETWEEN 1 AND 128", name="resource_id_length"),
        Index("ix_audit_events_action_id", "action", "id"),
        Index("ix_audit_events_resource_id", "resource_type", "resource_id", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True, autoincrement=True
    )
    actor_api_key_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    actor_public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("char_length(job_type) BETWEEN 1 AND 128", name="job_type_length"),
        CheckConstraint(
            "char_length(handler_version) BETWEEN 1 AND 64", name="handler_version_length"
        ),
        CheckConstraint(
            "status IN ('scheduled', 'queued', 'running', 'retry_scheduled', "
            "'cancellation_requested', 'succeeded', 'failed', 'dead_lettered', 'cancelled')",
            name="status_supported",
        ),
        CheckConstraint("priority IN ('high', 'normal', 'low')", name="priority_supported"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("attempt_count BETWEEN 0 AND max_attempts", name="attempt_count_valid"),
        CheckConstraint(
            "(payload IS NOT NULL)::integer + (payload_blob_key IS NOT NULL)::integer = 1",
            name="one_payload_location",
        ),
        CheckConstraint(
            "(result IS NOT NULL)::integer + (result_blob_key IS NOT NULL)::integer <= 1",
            name="at_most_one_result_location",
        ),
        CheckConstraint(
            "status NOT IN ('scheduled', 'retry_scheduled') OR run_at IS NOT NULL",
            name="scheduled_has_run_at",
        ),
        CheckConstraint(
            "(status IN ('running', 'cancellation_requested')) = (active_attempt_id IS NOT NULL)",
            name="active_attempt_matches_state",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'dead_lettered', 'cancelled')) = "
            "(finished_at IS NOT NULL)",
            name="finished_at_matches_state",
        ),
        Index("ix_jobs_status_created_at", "status", "created_at"),
        Index("ix_jobs_api_key_id_id", "api_key_id", "id"),
        Index("ix_jobs_created_at", "created_at"),
        Index(
            "ix_jobs_terminal_finished_at",
            "finished_at",
            "id",
            postgresql_where=text(
                "status IN ('succeeded', 'failed', 'dead_lettered', 'cancelled')"
            ),
        ),
        Index("ix_jobs_job_type_status_created_at", "job_type", "status", "created_at"),
        Index(
            "ix_jobs_due",
            "status",
            "run_at",
            postgresql_where=text("status IN ('scheduled', 'retry_scheduled')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    api_key_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="RESTRICT"), nullable=False
    )
    replayed_from_job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), index=True
    )
    job_type: Mapped[str] = mapped_column(String(128), nullable=False)
    handler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    payload_blob_key: Mapped[str | None] = mapped_column(Text)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_attempt_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "job_attempts.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_jobs_active_attempt",
        ),
    )
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    result_blob_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )

    @property
    def payload_stored_externally(self) -> bool:
        return self.payload_blob_key is not None

    @property
    def result_stored_externally(self) -> bool:
        return self.result_blob_key is not None


class AdmissionSlot(Base):
    __tablename__ = "admission_slots"

    slot_number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    job_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "jobs.id",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
        unique=True,
    )


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'abandoned', 'cancelled')",
            name="status_supported",
        ),
        CheckConstraint("char_length(worker_id) BETWEEN 1 AND 128", name="worker_id_length"),
        CheckConstraint(
            "failure_kind IS NULL OR failure_kind IN "
            "('temporary', 'permanent', 'worker_lost', 'cancelled')",
            name="failure_kind_supported",
        ),
        CheckConstraint(
            "error_code IS NULL OR char_length(error_code) <= 128", name="error_code_length"
        ),
        CheckConstraint(
            "error_message IS NULL OR char_length(error_message) <= 4096",
            name="error_message_length",
        ),
        CheckConstraint(
            "status != 'running' OR lease_expires_at IS NOT NULL", name="running_has_lease"
        ),
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="finished_at_matches_state",
        ),
        Index(
            "ix_job_attempts_active_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ownership_token: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, default=uuid7, unique=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_kind: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(String(4096))
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("api_key_id", "idempotency_key"),
        CheckConstraint(
            "char_length(idempotency_key) BETWEEN 1 AND 255", name="idempotency_key_length"
        ),
        CheckConstraint("octet_length(request_fingerprint) = 32", name="fingerprint_length"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True, autoincrement=True
    )
    api_key_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )


class JobTransition(Base):
    __tablename__ = "job_transitions"
    __table_args__ = (
        CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('scheduled', 'queued', 'running', 'retry_scheduled', "
            "'cancellation_requested', 'succeeded', 'failed', 'dead_lettered', 'cancelled')",
            name="from_status_supported",
        ),
        CheckConstraint(
            "to_status IN ('scheduled', 'queued', 'running', 'retry_scheduled', "
            "'cancellation_requested', 'succeeded', 'failed', 'dead_lettered', 'cancelled')",
            name="to_status_supported",
        ),
        CheckConstraint("char_length(reason) BETWEEN 1 AND 128", name="reason_length"),
        CheckConstraint(
            "actor_type IN ('api', 'scheduler', 'worker', 'recovery', 'admin', 'system')",
            name="actor_type_supported",
        ),
        CheckConstraint("actor_id IS NULL OR char_length(actor_id) <= 128", name="actor_id_length"),
        Index("ix_job_transitions_job_id_created_at", "job_id", "created_at"),
        Index("ix_job_transitions_to_status_created_at", "to_status", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True, autoincrement=True
    )
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("job_attempts.id", ondelete="CASCADE")
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("char_length(event_type) BETWEEN 1 AND 128", name="event_type_length"),
        CheckConstraint("dispatch_attempts >= 0", name="dispatch_attempts_nonnegative"),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)", name="complete_lease_pair"
        ),
        CheckConstraint(
            "published_at IS NULL OR lease_token IS NULL", name="published_has_no_lease"
        ),
        CheckConstraint(
            "last_error IS NULL OR char_length(last_error) <= 4096", name="last_error_length"
        ),
        Index(
            "ix_outbox_events_available_unpublished",
            "available_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )
    dispatch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(4096))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=timestamp_default
    )
