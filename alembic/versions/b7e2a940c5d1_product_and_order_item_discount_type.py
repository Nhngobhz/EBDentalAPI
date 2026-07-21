"""product and order_item discount type

Revision ID: b7e2a940c5d1
Revises: a1f4c9e27b03
Create Date: 2026-07-21 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2a940c5d1'
down_revision: Union[str, Sequence[str], None] = 'a1f4c9e27b03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # products.discount: Integer percent -> Numeric so it can also hold a $ amount,
    # plus discount_type to say which. Every existing value was always a percent.
    op.add_column('products', sa.Column('discount_type', sa.String(length=10), server_default='percent', nullable=False))
    op.alter_column(
        'products', 'discount',
        existing_type=sa.Integer(),
        type_=sa.Numeric(precision=10, scale=2),
        existing_server_default='0',
    )

    # order_items.discount: same change, snapshotted from products at quote time.
    op.add_column('order_items', sa.Column('discount_type', sa.String(length=10), server_default='percent', nullable=False))
    op.alter_column(
        'order_items', 'discount',
        existing_type=sa.Integer(),
        type_=sa.Numeric(precision=10, scale=2),
        existing_server_default='0',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'order_items', 'discount',
        existing_type=sa.Numeric(precision=10, scale=2),
        type_=sa.Integer(),
        existing_server_default='0',
    )
    op.drop_column('order_items', 'discount_type')

    op.alter_column(
        'products', 'discount',
        existing_type=sa.Numeric(precision=10, scale=2),
        type_=sa.Integer(),
        existing_server_default='0',
    )
    op.drop_column('products', 'discount_type')
