"""branches and their managers, for transferring calls to the customer's branch

Revision ID: 3f6a1c8e2b57
Revises: 7b3c9e2d4f18
Create Date: 2026-11-03 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '3f6a1c8e2b57'
down_revision: Union[str, Sequence[str], None] = '7b3c9e2d4f18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'branches',
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('other_names', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('manager_name', sa.String(length=120), nullable=False),
        sa.Column('manager_phone', sa.String(length=20), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_branches')),
        sa.UniqueConstraint('name', name=op.f('uq_branches_name')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('branches')
