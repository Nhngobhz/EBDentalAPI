"""add product_images table (product detail gallery)

Revision ID: e6b4d09f1a25
Revises: f2a9c4e18b73
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6b4d09f1a25'
down_revision: Union[str, Sequence[str], None] = 'f2a9c4e18b73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Extra photos for the storefront's product page gallery. A table rather than
    more columns on `products` because the count per product isn't fixed.
    `products.product_image` is untouched and stays the primary picture (catalog
    card, cart, printed quote) - rows here are only the additional ones.
    """
    op.create_table(
        'product_images',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('image', sa.String(length=500), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        # CASCADE: a gallery photo has no meaning without its product, and
        # deleting a product already cascades to its manuals the same way.
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_product_images_id'), 'product_images', ['id'], unique=False)
    op.create_index(
        op.f('ix_product_images_product_id'), 'product_images', ['product_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_product_images_product_id'), table_name='product_images')
    op.drop_index(op.f('ix_product_images_id'), table_name='product_images')
    op.drop_table('product_images')
