"""Use case/submission/document identities without entity version numbers.

Revision ID: 0004_remove_entity_versions
Revises: 0003_case_state_boundary

Only the obsolete version fields are removed. Existing identities, business
fields, audit events and original-file references remain unchanged.
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_remove_entity_versions"
down_revision = "0003_case_state_boundary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_cases_initial_version", "cases", type_="check")
    op.drop_column("cases", "case_version")
    op.drop_constraint("ck_case_submissions_initial_version", "case_submissions", type_="check")
    op.drop_column("case_submissions", "submission_version")
    op.drop_column("audit_events", "document_version")


def downgrade() -> None:
    # Restore the old schema only; no removed historical version values can be recovered.
    # Only use downgrade on disposable test data.
    op.add_column("cases", sa.Column("case_version", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("cases", "case_version", server_default=None)
    op.create_check_constraint("ck_cases_initial_version", "cases", "case_version = 1")
    op.add_column("case_submissions", sa.Column("submission_version", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("case_submissions", "submission_version", server_default=None)
    op.create_check_constraint("ck_case_submissions_initial_version", "case_submissions", "submission_version = 1")
    op.add_column("audit_events", sa.Column("document_version", sa.Integer(), nullable=True))
