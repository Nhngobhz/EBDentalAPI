"""add users.date_of_birth and users.gender

Revision ID: d7c4a2f19b60
Revises: c5b1e83f7a04
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7c4a2f19b60'
down_revision: Union[str, Sequence[str], None] = 'c5b1e83f7a04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Mirrors c5b1e83f7a04 (the same pair on `customers`) - both nullable, no
    # backfill. Staff fill them in on their own profile page, or a
    # user_management admin sets them via PUT /users/{id}.
    op.add_column('users', sa.Column('date_of_birth', sa.Date(), nullable=True))
    op.add_column('users', sa.Column('gender', sa.String(length=10), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'gender')
    op.drop_column('users', 'date_of_birth')
