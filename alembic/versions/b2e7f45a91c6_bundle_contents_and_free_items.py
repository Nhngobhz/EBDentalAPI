"""promotion/set contents, product free items, order item components

Revision ID: b2e7f45a91c6
Revises: a9b3f6c21e47
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2e7f45a91c6'
down_revision: Union[str, Sequence[str], None] = 'a9b3f6c21e47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Promotions and Sets become COLLECTIONS of products: they keep their own
    # fixed bundle price, and these rows say what's inside. Buying one puts each
    # member on the order as a $0 line (see order_items.parent_item_id below).
    # ON DELETE CASCADE on product_id: deleting a product just drops it from any
    # bundle it was in - historical orders keep their own snapshot either way.
    op.create_table(
        'promotion_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('promotion_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False, server_default='1'),
        sa.ForeignKeyConstraint(['promotion_id'], ['promotions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('promotion_id', 'product_id', name='uq_promotion_item'),
    )
    op.create_index(op.f('ix_promotion_items_id'), 'promotion_items', ['id'], unique=False)
    op.create_index(op.f('ix_promotion_items_promotion_id'), 'promotion_items', ['promotion_id'], unique=False)
    op.create_index(op.f('ix_promotion_items_product_id'), 'promotion_items', ['product_id'], unique=False)

    op.create_table(
        'set_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('set_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False, server_default='1'),
        sa.ForeignKeyConstraint(['set_id'], ['sets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('set_id', 'product_id', name='uq_set_item'),
    )
    op.create_index(op.f('ix_set_items_id'), 'set_items', ['id'], unique=False)
    op.create_index(op.f('ix_set_items_set_id'), 'set_items', ['set_id'], unique=False)
    op.create_index(op.f('ix_set_items_product_id'), 'set_items', ['product_id'], unique=False)

    # "Buy this product, get these free." Both sides are real products;
    # product_id is the FREE one and parent_product_id the paid one (that column
    # naming keeps all three tables uniform - see BundleItemMixin in models.py).
    op.create_table(
        'product_free_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('parent_product_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False, server_default='1'),
        sa.ForeignKeyConstraint(['parent_product_id'], ['products.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('parent_product_id', 'product_id', name='uq_product_free_item'),
        sa.CheckConstraint('parent_product_id <> product_id', name='ck_product_free_item_not_self'),
    )
    op.create_index(op.f('ix_product_free_items_id'), 'product_free_items', ['id'], unique=False)
    op.create_index(op.f('ix_product_free_items_parent_product_id'), 'product_free_items', ['parent_product_id'], unique=False)
    op.create_index(op.f('ix_product_free_items_product_id'), 'product_free_items', ['product_id'], unique=False)

    # Component lines: the $0 rows a bundle/free-gift expands into, pointing at
    # the paid line they belong to. NULL on every pre-existing row (they were all
    # ordinary priced lines), so no backfill is needed.
    op.add_column('order_items', sa.Column('parent_item_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_order_items_parent_item_id'), 'order_items', ['parent_item_id'], unique=False)
    op.create_foreign_key(
        'fk_order_items_parent_item_id_order_items',
        'order_items', 'order_items',
        ['parent_item_id'], ['id'],
        ondelete='CASCADE',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_order_items_parent_item_id_order_items', 'order_items', type_='foreignkey')
    op.drop_index(op.f('ix_order_items_parent_item_id'), table_name='order_items')
    op.drop_column('order_items', 'parent_item_id')

    op.drop_index(op.f('ix_product_free_items_product_id'), table_name='product_free_items')
    op.drop_index(op.f('ix_product_free_items_parent_product_id'), table_name='product_free_items')
    op.drop_index(op.f('ix_product_free_items_id'), table_name='product_free_items')
    op.drop_table('product_free_items')

    op.drop_index(op.f('ix_set_items_product_id'), table_name='set_items')
    op.drop_index(op.f('ix_set_items_set_id'), table_name='set_items')
    op.drop_index(op.f('ix_set_items_id'), table_name='set_items')
    op.drop_table('set_items')

    op.drop_index(op.f('ix_promotion_items_product_id'), table_name='promotion_items')
    op.drop_index(op.f('ix_promotion_items_promotion_id'), table_name='promotion_items')
    op.drop_index(op.f('ix_promotion_items_id'), table_name='promotion_items')
    op.drop_table('promotion_items')
