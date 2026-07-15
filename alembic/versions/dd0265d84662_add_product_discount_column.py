"""add product discount column

Revision ID: dd0265d84662
Revises: e8d25e5953e8
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd0265d84662'
down_revision: Union[str, Sequence[str], None] = 'e8d25e5953e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Products only - Promotion keeps its own price/old_price pair.
    op.add_column('products', sa.Column('discount', sa.String(length=20), nullable=True))

    # Carry existing data forward as a "$" amount off rather than dropping
    # it silently: old_price was always the higher (pre-discount) figure.
    op.execute(
        """
        UPDATE products
        SET discount = trim_scale(old_price - price)::text || '$'
        WHERE old_price IS NOT NULL AND old_price > price
        """
    )

    op.drop_column('products', 'old_price')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('products', sa.Column('old_price', sa.Numeric(precision=10, scale=2), nullable=True))

    op.execute(
        """
        UPDATE products
        SET old_price = price + NULLIF(regexp_replace(discount, '[^0-9.]', '', 'g'), '')::numeric
        WHERE discount LIKE '%$'
        """
    )

    op.drop_column('products', 'discount')
