"""Identify admitted originals and freeze one complete intake handoff.

Revision ID: 0005_document_intake
Revises: 0004_remove_entity_versions
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_document_intake"
down_revision = "0004_remove_entity_versions"
branch_labels = depends_on = None


def upgrade():
    op.add_column("audit_events", sa.Column("document_id", sa.Uuid(), nullable=True))
    op.add_column("audit_events", sa.Column("submission_id", sa.Uuid(), nullable=True))
    op.create_table(
        "upload_requests",
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.case_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint("uq_case_submissions_identity_case", "case_submissions", ["submission_id", "case_id"])
    op.create_table(
        "documents",
        sa.Column("document_id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.case_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("stored_files.file_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_documents_request_id"),
        sa.UniqueConstraint("file_id", name="uq_documents_file_id"),
        sa.UniqueConstraint("case_id", "category", name="uq_documents_case_category"),
        sa.ForeignKeyConstraint(["submission_id", "case_id"], ["case_submissions.submission_id", "case_submissions.case_id"], name="fk_documents_submission_case"),
        sa.CheckConstraint("category IN ('questionnaire', 'soc2', 'dpa', 'bcp_dr')", name="ck_documents_category"),
        sa.CheckConstraint("page_count BETWEEN 1 AND 200", name="ck_documents_pages"),
    )
    op.create_table(
        "intake_handoffs",
        sa.Column("handoff_id", sa.Uuid(), primary_key=True),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("cases.case_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("document_ids", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.UniqueConstraint("case_id", name="uq_intake_handoffs_case_id"),
        sa.UniqueConstraint("request_id", name="uq_intake_handoffs_request_id"),
        sa.ForeignKeyConstraint(["submission_id", "case_id"], ["case_submissions.submission_id", "case_submissions.case_id"], name="fk_handoffs_submission_case"),
        sa.CheckConstraint("jsonb_array_length(document_ids) = 4", name="ck_handoffs_document_count"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_handoffs_duration"),
    )


def downgrade():
    # Only use on disposable data: admitted identities and handoffs are lost.
    op.drop_table("intake_handoffs")
    op.drop_table("documents")
    op.drop_table("upload_requests")
    op.drop_column("audit_events", "document_id")
    op.drop_column("audit_events", "submission_id")
    op.drop_constraint("uq_case_submissions_identity_case", "case_submissions", type_="unique")
