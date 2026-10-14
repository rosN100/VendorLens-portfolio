"""F1: file metadata and audit events.

Revision ID: 0001_file_storage_audit
Revises: None
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_file_storage_audit"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("file_id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum_algorithm", sa.String(16), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_stored_files_request_id"),
        sa.CheckConstraint("byte_size >= 0", name="ck_stored_files_size"),
        sa.CheckConstraint("checksum_algorithm = 'sha256'", name="ck_stored_files_algorithm"),
    )
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("component", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("parent_event_id", sa.Uuid(), nullable=True),
        sa.Column("file_id", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("document_version", sa.Integer(), nullable=True),
        sa.Column("ledger_revision", sa.Integer(), nullable=True),
        sa.Column("policy_version", sa.String(100), nullable=True),
        sa.CheckConstraint("outcome IN ('success', 'failure')", name="ck_audit_events_outcome"),
    )
    op.create_index("ix_audit_events_file_id", "audit_events", ["file_id"])
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])


def downgrade() -> None:
    # Destructive: only use on disposable test databases, never to troubleshoot access.
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    op.drop_index("ix_audit_events_file_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("stored_files")
