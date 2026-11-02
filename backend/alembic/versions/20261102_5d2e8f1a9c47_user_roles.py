"""six user roles; calls remember who placed them

Revision ID: 5d2e8f1a9c47
Revises: 9a3c5e7f1b24
Create Date: 2026-11-02 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d2e8f1a9c47'
down_revision: Union[str, Sequence[str], None] = '9a3c5e7f1b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLES = "('owner', 'admin', 'campaign_manager', 'caller', 'reviewer', 'viewer')"


def upgrade() -> None:
    """Upgrade schema."""
    for table, name in (('users', 'user_role'), ('api_keys', 'api_key_role')):
        op.drop_constraint(name, table, type_='check')
        op.create_check_constraint(name, table, f"role IN {ROLES}")
    # The first admin (the one created from .env) becomes the owner.
    op.execute(
        "UPDATE users SET role = 'owner' WHERE id = (SELECT id FROM users WHERE role = 'admin' "
        "AND is_active ORDER BY created_at LIMIT 1)"
    )
    op.add_column('calls', sa.Column('placed_by_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_calls_placed_by_id'), 'calls', ['placed_by_id'])
    op.create_foreign_key(op.f('fk_calls_placed_by_id_users'), 'calls', 'users', ['placed_by_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_calls_placed_by_id_users'), 'calls', type_='foreignkey')
    op.drop_index(op.f('ix_calls_placed_by_id'), table_name='calls')
    op.drop_column('calls', 'placed_by_id')
    op.execute("UPDATE users SET role = 'admin' WHERE role = 'owner'")
    op.execute("UPDATE users SET role = 'viewer' WHERE role NOT IN ('admin', 'viewer')")
    op.execute("UPDATE api_keys SET role = 'viewer' WHERE role NOT IN ('admin', 'viewer')")
    for table, name in (('users', 'user_role'), ('api_keys', 'api_key_role')):
        op.drop_constraint(name, table, type_='check')
        op.create_check_constraint(name, table, "role IN ('admin', 'viewer')")
