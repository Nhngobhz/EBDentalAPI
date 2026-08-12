"""add pending_checkouts table and orders.payment_reference

Revision ID: a4f2c7d91b58
Revises: e6b4d09f1a25
Create Date: 2026-08-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4f2c7d91b58'
down_revision: Union[str, Sequence[str], None] = 'e6b4d09f1a25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    A customer's KHQR checkout no longer writes an order up front - an order (and its
    order_items) is only created once the payment is confirmed, so a customer can never
    hold an unpaid order. The priced, QR-issued checkout lives here in the meantime.

    `orders.payment_reference` records which checkout an order came from: order_number
    is only assigned at payment time, so the checkout's reference is what the bank knows
    the transaction by.
    """
    op.create_table(
        "pending_checkouts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=30), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("grand_total", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("khqr_string", sa.String(length=512), nullable=False),
        sa.Column("khqr_md5", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pending_checkouts_id"), "pending_checkouts", ["id"])
    op.create_index(
        op.f("ix_pending_checkouts_reference"), "pending_checkouts", ["reference"], unique=True
    )

    op.add_column("orders", sa.Column("payment_reference", sa.String(length=30), nullable=True))
    op.create_index(op.f("ix_orders_payment_reference"), "orders", ["payment_reference"])


def downgrade() -> None:
    """Downgrade schema.

    Dropping pending_checkouts discards any checkout that was awaiting payment. Those
    have no order behind them by definition, so nothing already sold is lost - but a
    customer mid-payment would have to check out again.
    """
    op.drop_index(op.f("ix_orders_payment_reference"), table_name="orders")
    op.drop_column("orders", "payment_reference")

    op.drop_index(op.f("ix_pending_checkouts_reference"), table_name="pending_checkouts")
    op.drop_index(op.f("ix_pending_checkouts_id"), table_name="pending_checkouts")
    op.drop_table("pending_checkouts")
