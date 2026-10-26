"""simulator provider

Revision ID: 8c1f3e5a7b92
Revises: 5b7e2a9c4d10
Create Date: 2026-10-26 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8c1f3e5a7b92'
down_revision: Union[str, Sequence[str], None] = '5b7e2a9c4d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BEFORE = ('plivo', 'vobiz', 'exotel', 'twilio', 'telnyx', 'vonage', 'sip')
AFTER = (*BEFORE, 'simulator')


def _allow(values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f('ck_provider_accounts_telephony_provider'), 'provider_accounts', type_='check')
    op.create_check_constraint(
        op.f('ck_provider_accounts_telephony_provider'),
        'provider_accounts',
        f"provider IN ({', '.join(repr(v) for v in values)})",
    )


def upgrade() -> None:
    """Upgrade schema."""
    _allow(AFTER)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM provider_accounts WHERE provider = 'simulator'")
    _allow(BEFORE)
