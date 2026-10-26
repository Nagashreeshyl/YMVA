"""do not call list

Revision ID: 4e6d1b8a2c35
Revises: 8c1f3e5a7b92
Create Date: 2026-10-27 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e6d1b8a2c35'
down_revision: Union[str, Sequence[str], None] = '8c1f3e5a7b92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'do_not_call',
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('phone', name=op.f('pk_do_not_call')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('do_not_call')
