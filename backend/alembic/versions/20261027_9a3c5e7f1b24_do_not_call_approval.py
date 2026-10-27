"""do not call approval

Revision ID: 9a3c5e7f1b24
Revises: 4e6d1b8a2c35
Create Date: 2026-10-27 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a3c5e7f1b24'
down_revision: Union[str, Sequence[str], None] = '4e6d1b8a2c35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('do_not_call', sa.Column('pending', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('do_not_call', sa.Column('call_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_do_not_call_call_id_calls'), 'do_not_call', 'calls', ['call_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_do_not_call_call_id_calls'), 'do_not_call', type_='foreignkey')
    op.drop_column('do_not_call', 'call_id')
    op.drop_column('do_not_call', 'pending')
