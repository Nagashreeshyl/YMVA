"""Branch states; calls: branch, action items and retries

Revision ID: 5d2e8a1b9c43
Revises: 8c1d4e7a2f90
Create Date: 2026-11-06

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5d2e8a1b9c43"
down_revision: str | Sequence[str] | None = "8c1d4e7a2f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "branches", sa.Column("state", sa.String(60), nullable=False, server_default="")
    )
    op.add_column(
        "calls",
        sa.Column(
            "action_items", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )
    op.add_column("calls", sa.Column("branch", sa.String(120), nullable=True))
    op.create_index("ix_calls_branch", "calls", ["branch"])
    op.add_column("calls", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("calls", sa.Column("first_call_id", sa.Uuid(), nullable=True))
    op.create_index("ix_calls_first_call_id", "calls", ["first_call_id"])
    op.create_foreign_key(
        "fk_calls_first_call_id", "calls", "calls", ["first_call_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column(
        "calls",
        sa.Column("retry_status", sa.String(16), nullable=False, server_default="none"),
    )
    op.add_column("calls", sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_calls_retry_pending",
        "calls",
        ["retry_at"],
        postgresql_where=sa.text("retry_status IN ('pending', 'scheduled')"),
    )


def downgrade() -> None:
    op.drop_index("ix_calls_retry_pending", table_name="calls")
    op.drop_column("calls", "retry_at")
    op.drop_column("calls", "retry_status")
    op.drop_constraint("fk_calls_first_call_id", "calls", type_="foreignkey")
    op.drop_index("ix_calls_first_call_id", table_name="calls")
    op.drop_column("calls", "first_call_id")
    op.drop_column("calls", "attempt")
    op.drop_index("ix_calls_branch", table_name="calls")
    op.drop_column("calls", "branch")
    op.drop_column("calls", "action_items")
    op.drop_column("branches", "state")
