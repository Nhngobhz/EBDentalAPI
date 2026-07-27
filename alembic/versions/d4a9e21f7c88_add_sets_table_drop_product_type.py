"""add sets table, drop product_type

Revision ID: d4a9e21f7c88
Revises: c3f7a1e94d5b
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a9e21f7c88'
down_revision: Union[str, Sequence[str], None] = 'c3f7a1e94d5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Dedicated Promotion/Product models already cover "special pricing" - the
    # single/combo/promotional product_type dropdown (and the order-level
    # discount exemption it drove for "promotional" products) is redundant now
    # and is being removed outright.
    op.drop_index(op.f('ix_products_product_type'), table_name='products')
    op.drop_column('products', 'product_type')

    # Sets: bundle deals shown on the Promotions page, same shape as
    # Promotion minus start_date/end_date since a set is never time-boxed.
    op.create_table(
        'sets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('set_name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('old_price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('set_image', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_sets_id'), 'sets', ['id'], unique=False)
    op.create_index(op.f('ix_sets_set_name'), 'sets', ['set_name'], unique=False)

    # Lets a customer buy a Set the same way they buy a Product/Promotion -
    # exactly one of product_id/promotion_id/set_id is set per order_items row.
    # SET NULL, same as product_id/promotion_id: a deleted set must never
    # corrupt a historical order.
    op.add_column('order_items', sa.Column('set_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_order_items_set_id_sets',
        'order_items', 'sets',
        ['set_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_order_items_set_id_sets', 'order_items', type_='foreignkey')
    op.drop_column('order_items', 'set_id')

    op.drop_index(op.f('ix_sets_set_name'), table_name='sets')
    op.drop_index(op.f('ix_sets_id'), table_name='sets')
    op.drop_table('sets')

    op.add_column(
        'products',
        sa.Column('product_type', sa.String(length=20), nullable=False, server_default='single'),
    )
    op.create_index(op.f('ix_products_product_type'), 'products', ['product_type'], unique=False)
