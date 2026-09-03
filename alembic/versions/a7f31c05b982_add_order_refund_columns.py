"""add orders.refunded_at and orders.refund_reason

Revision ID: a7f31c05b982
Revises: b3e6d1042f9c
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7f31c05b982'
down_revision: Union[str, Sequence[str], None] = 'b3e6d1042f9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Refunding a paid order (POST /orders/{id}/refund, admin only). The reversal
    # itself is a new payment_status value - "refunded" - which needs no migration:
    # the column is already a plain String(20), exactly so a new state costs nothing
    # (see the eb-migration note about Literals vs DB enums).
    #
    # These two columns are what that value can't carry on its own: when the money
    # went back, and why. Both nullable - every existing row predates them, and the
    # reason is optional even on a fresh refund.
    #
    # paid_at is deliberately untouched by a refund: the payment really happened and
    # its date is what a bank statement gets reconciled against.
    op.add_column('orders', sa.Column('refunded_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('refund_reason', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Any row sitting at payment_status "refunded" goes back to "paid": that is what it
    # was before the refund was recorded, and it is the only value the older code knows
    # how to read. The reason and the date are lost with the columns.
    op.execute("UPDATE orders SET payment_status = 'paid' WHERE payment_status = 'refunded'")
    op.drop_column('orders', 'refund_reason')
    op.drop_column('orders', 'refunded_at')
