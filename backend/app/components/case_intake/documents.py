"""Document identity and a durable, frozen intake-to-processing handoff."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class UploadRequest(Base):
    """Durable request intent; may exist before an original is admitted."""
    __tablename__ = "upload_requests"
    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.case_id", ondelete="RESTRICT"), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_documents_request_id"),
        UniqueConstraint("file_id", name="uq_documents_file_id"),
        UniqueConstraint("case_id", "category", name="uq_documents_case_category"),
        ForeignKeyConstraint(["submission_id", "case_id"], ["case_submissions.submission_id", "case_submissions.case_id"], name="fk_documents_submission_case"),
        CheckConstraint("category IN ('questionnaire', 'soc2', 'dpa', 'bcp_dr')", name="ck_documents_category"),
        CheckConstraint("page_count BETWEEN 1 AND 200", name="ck_documents_pages"),
    )

    document_id: Mapped[UUID] = mapped_column(primary_key=True)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.case_id", ondelete="RESTRICT"), nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    file_id: Mapped[UUID] = mapped_column(ForeignKey("stored_files.file_id", ondelete="RESTRICT"), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    page_count: Mapped[int] = mapped_column(nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntakeHandoff(Base):
    __tablename__ = "intake_handoffs"
    __table_args__ = (
        UniqueConstraint("case_id", name="uq_intake_handoffs_case_id"),
        UniqueConstraint("request_id", name="uq_intake_handoffs_request_id"),
        ForeignKeyConstraint(["submission_id", "case_id"], ["case_submissions.submission_id", "case_submissions.case_id"], name="fk_handoffs_submission_case"),
        CheckConstraint("jsonb_array_length(document_ids) = 4", name="ck_handoffs_document_count"),
        CheckConstraint("duration_ms >= 0", name="ck_handoffs_duration"),
    )

    handoff_id: Mapped[UUID] = mapped_column(primary_key=True)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.case_id", ondelete="RESTRICT"), nullable=False)
    submission_id: Mapped[UUID] = mapped_column(nullable=False)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    document_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
