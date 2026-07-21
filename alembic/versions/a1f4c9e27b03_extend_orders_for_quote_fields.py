"""extend orders for quote fields

Revision ID: a1f4c9e27b03
Revises: 790b3a4cfbd2
Create Date: 2026-07-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f4c9e27b03'
down_revision: Union[str, Sequence[str], None] = '790b3a4cfbd2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- "C. Code" - nullable first so existing rows can be backfilled, then tightened. ---
    op.add_column('orders', sa.Column('quote_code', sa.String(length=20), nullable=True))
    op.execute("UPDATE orders SET quote_code = 'LEGACY-' || id WHERE quote_code IS NULL")
    op.alter_column('orders', 'quote_code', nullable=False)
    op.create_index(op.f('ix_orders_quote_code'), 'orders', ['quote_code'], unique=True)

    # --- "User" - who was logged in when the quote was placed (distinct from salesperson,
    # which gets overridden to "Website" for customers). No backfill possible for existing
    # rows, stays nullable. ---
    op.add_column('orders', sa.Column('quoted_by_name', sa.String(length=150), nullable=True))

    # --- discount_type/value/amount replace the old flat cash_discount column. Every
    # existing order's discount was a cash amount, so that's preserved as-is. ---
    op.add_column('orders', sa.Column('discount_type', sa.String(length=10), server_default='cash', nullable=False))
    op.add_column('orders', sa.Column('discount_value', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))
    op.add_column('orders', sa.Column('discount_amount', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))
    op.execute("UPDATE orders SET discount_value = cash_discount, discount_amount = cash_discount")
    op.drop_column('orders', 'cash_discount')

    # --- Clinic/Contact Tel/Address are now required. Backfill blanks so the NOT NULL
    # constraint doesn't fail against pre-existing rows. ---
    op.execute("UPDATE orders SET clinic_name = '' WHERE clinic_name IS NULL")
    op.execute("UPDATE orders SET phone = '' WHERE phone IS NULL")
    op.execute("UPDATE orders SET address = '' WHERE address IS NULL")
    op.alter_column('orders', 'clinic_name', existing_type=sa.String(length=200), nullable=False)
    op.alter_column('orders', 'phone', existing_type=sa.String(length=30), nullable=False)
    op.alter_column('orders', 'address', existing_type=sa.String(length=255), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('orders', 'clinic_name', existing_type=sa.String(length=200), nullable=True)
    op.alter_column('orders', 'phone', existing_type=sa.String(length=30), nullable=True)
    op.alter_column('orders', 'address', existing_type=sa.String(length=255), nullable=True)

    op.add_column('orders', sa.Column('cash_discount', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))
    op.execute("UPDATE orders SET cash_discount = discount_amount")
    op.drop_column('orders', 'discount_amount')
    op.drop_column('orders', 'discount_value')
    op.drop_column('orders', 'discount_type')

    op.drop_column('orders', 'quoted_by_name')

    op.drop_index(op.f('ix_orders_quote_code'), table_name='orders')
    op.drop_column('orders', 'quote_code')
