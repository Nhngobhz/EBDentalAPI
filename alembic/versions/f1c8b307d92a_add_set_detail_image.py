"""add sets.detail_image

Revision ID: f1c8b307d92a
Revises: d4a9e21f7c88
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1c8b307d92a'
down_revision: Union[str, Sequence[str], None] = 'd4a9e21f7c88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Optional second image rendered under the name/description on the
    # storefront set card - nullable, since every existing set predates it.
    op.add_column('sets', sa.Column('detail_image', sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('sets', 'detail_image')
