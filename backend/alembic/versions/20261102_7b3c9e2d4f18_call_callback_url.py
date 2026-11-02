"""calls started by API can name a callback URL

Revision ID: 7b3c9e2d4f18
Revises: 5d2e8f1a9c47
Create Date: 2026-11-02 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b3c9e2d4f18'
down_revision: Union[str, Sequence[str], None] = '5d2e8f1a9c47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('calls', sa.Column('callback_url', sa.String(length=2048), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('calls', 'callback_url')
