"""add app_settings table

Revision ID: b6f29d40a71c
Revises: a3d81f6c94e2
Create Date: 2026-08-17 00:00:00.000000

Key/value storage for the site-wide settings behind the admin Settings screen.

Only *overrides* are stored: every setting's default lives in code
(app/core/settings_spec.py), and a key with no row reads as its default. So this table
is empty on a fresh install, no seeding step is needed, and "reset to default" is a
DELETE. Adding a new setting later is a code change with no migration.

Nothing secret goes in here - credentials stay in the environment (app/config.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6f29d40a71c'
down_revision: Union[str, Sequence[str], None] = 'a3d81f6c94e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'app_settings',
        # The setting's key from the spec is the primary key - there's no surrogate id,
        # since a key is already unique and every lookup is by it.
        sa.Column('key', sa.String(length=100), primary_key=True, nullable=False),
        # JSON, not text: a bool stays a bool and a number stays a number, so nothing
        # downstream has to guess how to parse "false" or "30".
        sa.Column('value', sa.JSON(), nullable=True),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('updated_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['updated_by_user_id'], ['users.id'],
            name='fk_app_settings_updated_by_user_id_users',
            ondelete='SET NULL',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('app_settings')
