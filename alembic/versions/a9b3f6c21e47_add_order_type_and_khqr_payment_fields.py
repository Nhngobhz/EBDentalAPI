"""add orders.order_type and KHQR payment fields

Revision ID: a9b3f6c21e47
Revises: f1c8b307d92a
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9b3f6c21e47'
down_revision: Union[str, Sequence[str], None] = 'f1c8b307d92a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('orders', sa.Column('order_type', sa.String(length=10), nullable=False, server_default='order'))
    op.add_column('orders', sa.Column('payment_method', sa.String(length=10), nullable=True))
    op.add_column('orders', sa.Column('payment_status', sa.String(length=20), nullable=True))
    op.add_column('orders', sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('khqr_string', sa.String(length=512), nullable=True))
    op.add_column('orders', sa.Column('khqr_md5', sa.String(length=32), nullable=True))
    # Everything placed before this column existed was a quotation (no payment was ever
    # taken through the site) - only KHQR checkouts from here on are real "order" rows.
    op.execute("UPDATE orders SET order_type = 'quote'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'khqr_md5')
    op.drop_column('orders', 'khqr_string')
    op.drop_column('orders', 'paid_at')
    op.drop_column('orders', 'payment_status')
    op.drop_column('orders', 'payment_method')
    op.drop_column('orders', 'order_type')
