"""add sets.brand_id

Revision ID: c9d4b1a70f52
Revises: a4f2c7d91b58
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d4b1a70f52'
down_revision: Union[str, Sequence[str], None] = 'a4f2c7d91b58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Which brand a set belongs to, so the Promotions page can be browsed by
    # brand. Nullable - unlike products.brand_id - because every existing set
    # predates the column and a mixed-brand bundle has no single brand.
    op.add_column('sets', sa.Column('brand_id', sa.Integer(), nullable=True))
    # Indexed: the storefront's brand strip filters on exactly this column.
    op.create_index(op.f('ix_sets_brand_id'), 'sets', ['brand_id'], unique=False)
    # RESTRICT, same as products.brand_id: a brand still used by a set can't be
    # deleted out from under it (the brands router turns that into a 400).
    op.create_foreign_key(
        'fk_sets_brand_id_brands',
        'sets', 'brands',
        ['brand_id'], ['id'],
        ondelete='RESTRICT',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_sets_brand_id_brands', 'sets', type_='foreignkey')
    op.drop_index(op.f('ix_sets_brand_id'), table_name='sets')
    op.drop_column('sets', 'brand_id')
