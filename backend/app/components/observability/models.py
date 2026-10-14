"""Durable audit envelope; no raw document contents or arbitrary payloads."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'failure')", name="ck_audit_events_outcome"),
        Index("ix_audit_events_file_id", "file_id"),
        Index("ix_audit_events_correlation_id", "correlation_id"),
    )

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    schema_version: Mapped[int] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    component: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(nullable=False)
    parent_event_id: Mapped[UUID | None] = mapped_column(nullable=True)
    # No FK: a failed read may legitimately reference a missing file.
    file_id: Mapped[UUID | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    case_id: Mapped[UUID | None] = mapped_column(nullable=True)
    document_id: Mapped[UUID | None] = mapped_column(nullable=True)
    submission_id: Mapped[UUID | None] = mapped_column(nullable=True)
    run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    ledger_revision: Mapped[int | None] = mapped_column(nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
