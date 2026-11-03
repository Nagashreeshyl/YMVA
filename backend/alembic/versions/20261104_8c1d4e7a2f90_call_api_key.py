"""calls remember the API key that placed them

Revision ID: 8c1d4e7a2f90
Revises: 3f6a1c8e2b57
Create Date: 2026-11-04 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c1d4e7a2f90'
down_revision: Union[str, Sequence[str], None] = '3f6a1c8e2b57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('calls', sa.Column('placed_by_api_key_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_calls_placed_by_api_key_id'), 'calls', ['placed_by_api_key_id'])
    op.create_foreign_key(
        op.f('fk_calls_placed_by_api_key_id_api_keys'), 'calls', 'api_keys',
        ['placed_by_api_key_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_calls_placed_by_api_key_id_api_keys'), 'calls', type_='foreignkey')
    op.drop_index(op.f('ix_calls_placed_by_api_key_id'), table_name='calls')
    op.drop_column('calls', 'placed_by_api_key_id')
