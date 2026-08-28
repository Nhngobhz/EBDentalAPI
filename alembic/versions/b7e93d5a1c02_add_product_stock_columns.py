"""add products.stock_qty and products.stock_synced_at

Revision ID: b7e93d5a1c02
Revises: f4b7d16c9e30
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e93d5a1c02'
down_revision: Union[str, Sequence[str], None] = 'f4b7d16c9e30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # On-hand quantity as of the last SAP sync, summed across warehouses.
    #
    # Nullable with NO backfill, and that is the deliberate part: it would be easy
    # to default existing rows to 0, but 0 is a claim ("SAP says none left") and
    # every row that exists today is machinery, which never enters SAP and about
    # which we therefore know nothing. NULL says exactly that. Backfilling 0 here
    # would mark the entire existing catalogue out of stock the moment anything
    # starts reading this column.
    #
    # Numeric(12, 2) mirrors OITM.OnHand being a decimal in SAP - some materials
    # are counted in fractional boxes, and rounding at import would disagree with
    # the figure staff read off the SAP client.
    op.add_column('products', sa.Column('stock_qty', sa.Numeric(12, 2), nullable=True))

    # Age of the figure above. A stock number with no timestamp cannot be told
    # apart from a stale one, so anything that displays availability needs this to
    # decide whether to trust it.
    op.add_column(
        'products',
        sa.Column('stock_synced_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('products', 'stock_synced_at')
    op.drop_column('products', 'stock_qty')
