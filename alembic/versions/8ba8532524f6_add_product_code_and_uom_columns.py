"""add product code and uom columns

Revision ID: 8ba8532524f6
Revises: dd0265d84662
Create Date: 2026-07-15 13:02:32.375290

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8ba8532524f6'
down_revision: Union[str, Sequence[str], None] = 'dd0265d84662'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SKU / manufacturer code - nullable (existing products predate it) but
    # unique once set, same nullable+unique shape as reset_token/
    # verification_token elsewhere in this schema (Postgres allows any
    # number of NULLs through a unique index).
    op.add_column('products', sa.Column('product_code', sa.String(length=50), nullable=True))
    op.create_index(op.f('ix_products_product_code'), 'products', ['product_code'], unique=True)

    op.add_column('products', sa.Column('uom', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('products', 'uom')

    op.drop_index(op.f('ix_products_product_code'), table_name='products')
    op.drop_column('products', 'product_code')
