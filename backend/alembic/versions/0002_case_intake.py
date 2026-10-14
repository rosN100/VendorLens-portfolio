"""I1: case and submission identity.

Revision ID: 0002_case_intake
Revises: 0001_file_storage_audit
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_case_intake"
down_revision = "0001_file_storage_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("case_id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("vendor_name", sa.String(200), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("assessment_type", sa.String(50), nullable=False),
        sa.Column("risk_tier", sa.String(16), nullable=False),
        sa.Column("assessment_date", sa.Date(), nullable=False),
        sa.Column("policy_id", sa.String(100), nullable=False),
        sa.Column("policy_version", sa.String(50), nullable=False),
        sa.Column("policy_status", sa.String(16), nullable=False),
        sa.Column("state", sa.String(50), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_cases_request_id"),
        sa.CheckConstraint("case_version = 1", name="ck_cases_initial_version"),
        sa.CheckConstraint("assessment_type = 'new_vendor_onboarding'", name="ck_cases_assessment_type"),
        sa.CheckConstraint("risk_tier = 'high'", name="ck_cases_risk_tier"),
        sa.CheckConstraint("policy_status = 'ACTIVE'", name="ck_cases_policy_status"),
        sa.CheckConstraint("state = 'intake_in_progress'", name="ck_cases_initial_state"),
    )
    op.create_index("ix_cases_created_at", "cases", ["created_at"])
    op.create_table(
        "case_submissions",
        sa.Column("submission_id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.case_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("submission_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", name="uq_case_submissions_case_id"),
        sa.CheckConstraint("submission_version = 1", name="ck_case_submissions_initial_version"),
    )


def downgrade() -> None:
    # Destructive: only use on a disposable test database.
    op.drop_table("case_submissions")
    op.drop_index("ix_cases_created_at", table_name="cases")
    op.drop_table("cases")
