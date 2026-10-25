"""agent assistant messages

Revision ID: c3a9d2e4f781
Revises: e70b59f1d705
Create Date: 2026-10-25 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c3a9d2e4f781'
down_revision: Union[str, Sequence[str], None] = 'e70b59f1d705'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'agent_assistant_messages',
        sa.Column('agent_id', sa.Uuid(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('changes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('config_before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('config_after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], name=op.f('fk_agent_assistant_messages_agent_id_agents'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_agent_assistant_messages_created_by_id_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_assistant_messages')),
    )
    op.create_index(op.f('ix_agent_assistant_messages_agent_id'), 'agent_assistant_messages', ['agent_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_agent_assistant_messages_agent_id'), table_name='agent_assistant_messages')
    op.drop_table('agent_assistant_messages')
