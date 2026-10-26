"""app settings

Revision ID: 5b7e2a9c4d10
Revises: 1d2ce9961bd4
Create Date: 2026-10-26 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '5b7e2a9c4d10'
down_revision: Union[str, Sequence[str], None] = '1d2ce9961bd4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'app_settings',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('key', name=op.f('pk_app_settings')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('app_settings')
