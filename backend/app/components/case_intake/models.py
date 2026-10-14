"""Case and submission identity for the first intake increment."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_cases_request_id"),
        CheckConstraint("assessment_type = 'new_vendor_onboarding'", name="ck_cases_assessment_type"),
        CheckConstraint("risk_tier = 'high'", name="ck_cases_risk_tier"),
        CheckConstraint("policy_status = 'ACTIVE'", name="ck_cases_policy_status"),
        CheckConstraint("state <> ''", name="ck_cases_state_present"),
        Index("ix_cases_created_at", "created_at"),
    )

    case_id: Mapped[UUID] = mapped_column(primary_key=True)
    request_id: Mapped[UUID] = mapped_column(nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    assessment_type: Mapped[str] = mapped_column(String(50), nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    assessment_date: Mapped[date] = mapped_column(Date(), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    policy_status: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaseSubmission(Base):
    __tablename__ = "case_submissions"
    __table_args__ = (
        UniqueConstraint("case_id", name="uq_case_submissions_case_id"),
        UniqueConstraint("submission_id", "case_id", name="uq_case_submissions_identity_case"),
    )

    submission_id: Mapped[UUID] = mapped_column(primary_key=True)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.case_id", ondelete="RESTRICT"), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
