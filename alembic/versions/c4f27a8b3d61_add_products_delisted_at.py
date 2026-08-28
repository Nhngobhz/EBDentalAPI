"""add products.delisted_at

Revision ID: c4f27a8b3d61
Revises: b7e93d5a1c02
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f27a8b3d61'
down_revision: Union[str, Sequence[str], None] = 'b7e93d5a1c02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # When SAP stopped listing this item. NULL = currently listed, which is the
    # correct state for every row that exists today: machinery never enters SAP,
    # and every synced material was present and valid at the last sync. So there
    # is deliberately no backfill - the default of NULL already says the true
    # thing about all of them.
    op.add_column('products', sa.Column('delisted_at', sa.DateTime(timezone=True), nullable=True))

    # Partial index, not a plain one. Every public catalog read filters on
    # "delisted_at IS NULL", and that is the overwhelming majority of rows - a
    # full index on a column that is almost entirely NULL earns nothing. Indexing
    # only the delisted rows keeps it small while still letting the planner find
    # them for the admin screen that lists what SAP has withdrawn.
    op.create_index(
        'ix_products_delisted_at',
        'products',
        ['delisted_at'],
        unique=False,
        postgresql_where=sa.text('delisted_at IS NOT NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_products_delisted_at', table_name='products')
    op.drop_column('products', 'delisted_at')
