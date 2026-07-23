"""add promotion_id to order_items

Revision ID: c3f7a1e94d5b
Revises: b7e2a940c5d1
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f7a1e94d5b'
down_revision: Union[str, Sequence[str], None] = 'b7e2a940c5d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Lets a customer buy a Promotion the same way they buy a Product - exactly one of
    # product_id/promotion_id is set per order_items row (product_id stays required-ish
    # in practice, just no longer the only option). SET NULL, same as product_id: a
    # deleted promotion must never corrupt a historical order.
    op.add_column('order_items', sa.Column('promotion_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_order_items_promotion_id_promotions',
        'order_items', 'promotions',
        ['promotion_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_order_items_promotion_id_promotions', 'order_items', type_='foreignkey')
    op.drop_column('order_items', 'promotion_id')
