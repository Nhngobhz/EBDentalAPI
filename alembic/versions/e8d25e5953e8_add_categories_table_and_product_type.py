"""add categories table and product type

Revision ID: e8d25e5953e8
Revises: 7f3a9c1d8b2e
Create Date: 2026-07-11 09:06:47.952619

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8d25e5953e8'
down_revision: Union[str, Sequence[str], None] = '7f3a9c1d8b2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category_name', sa.String(length=150), nullable=False),
        sa.Column('category_image', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_categories_category_name'), 'categories', ['category_name'], unique=True)
    op.create_index(op.f('ix_categories_id'), 'categories', ['id'], unique=False)

    # Turn each distinct existing free-text products.category value into a
    # real Category row before the column is dropped, so no data is lost.
    op.execute(
        """
        INSERT INTO categories (category_name, created_at)
        SELECT DISTINCT category, now() FROM products WHERE category IS NOT NULL
        """
    )

    op.add_column('products', sa.Column('category_id', sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE products
        SET category_id = categories.id
        FROM categories
        WHERE products.category = categories.category_name
        """
    )
    op.create_foreign_key(
        'products_category_id_fkey', 'products', 'categories', ['category_id'], ['id'], ondelete='RESTRICT'
    )
    op.create_index(op.f('ix_products_category_id'), 'products', ['category_id'], unique=False)

    op.drop_index(op.f('ix_products_category'), table_name='products')
    op.drop_column('products', 'category')

    op.add_column(
        'products',
        sa.Column('product_type', sa.String(length=20), nullable=False, server_default='single'),
    )
    op.create_index(op.f('ix_products_product_type'), 'products', ['product_type'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_products_product_type'), table_name='products')
    op.drop_column('products', 'product_type')

    op.add_column('products', sa.Column('category', sa.String(length=100), nullable=True))
    op.execute(
        """
        UPDATE products
        SET category = categories.category_name
        FROM categories
        WHERE products.category_id = categories.id
        """
    )
    op.create_index(op.f('ix_products_category'), 'products', ['category'], unique=False)

    op.drop_index(op.f('ix_products_category_id'), table_name='products')
    op.drop_constraint('products_category_id_fkey', 'products', type_='foreignkey')
    op.drop_column('products', 'category_id')

    op.drop_index(op.f('ix_categories_id'), table_name='categories')
    op.drop_index(op.f('ix_categories_category_name'), table_name='categories')
    op.drop_table('categories')
