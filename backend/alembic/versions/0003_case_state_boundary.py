"""Keep the initial state valid without blocking later intake transitions.

Revision ID: 0003_case_state_boundary
Revises: 0002_case_intake
"""

from alembic import op

revision = "0003_case_state_boundary"
down_revision = "0002_case_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_cases_initial_state", "cases", type_="check")
    op.create_check_constraint("ck_cases_state_present", "cases", "state <> ''")


def downgrade() -> None:
    # Destructive for later-state rows; only use on a disposable test database.
    op.drop_constraint("ck_cases_state_present", "cases", type_="check")
    op.create_check_constraint("ck_cases_initial_state", "cases", "state = 'intake_in_progress'")
