"""add users.admin permission

Revision ID: a3d81f6c94e2
Revises: c9d4b1a70f52
Create Date: 2026-08-17 00:00:00.000000

A fifth RBAC flag, gating site-wide configuration (the Settings screen added in
b6f29d40a71c). Deliberately not implied by the other four: creating staff accounts and
rewriting what every printed quote says are different jobs.

The backfill is the part worth reading. A plain `nullable=False, default false` column
would ship a system where the Settings screen exists and *nobody* can open it - and
since the only way to grant `admin` is through the admin panel, that state is only
recoverable with a hand-written UPDATE. So existing accounts holding all four original
permissions - what app/core/deps.py calls a "de-facto super admin", which is exactly
what scripts/create_admin.py creates - are granted it here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3d81f6c94e2'
down_revision: Union[str, Sequence[str], None] = 'c9d4b1a70f52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default is required, not cosmetic: the column is NOT NULL and the table
    # already has rows, so without it the ALTER fails outright.
    op.add_column(
        'users',
        sa.Column('admin', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE users
           SET admin = true
         WHERE user_management
           AND price_listing
           AND product_management
           AND customer_management
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'admin')
