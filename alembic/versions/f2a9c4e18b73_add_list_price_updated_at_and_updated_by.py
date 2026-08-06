"""add list_price, updated_at and updated_by_user_id

Revision ID: f2a9c4e18b73
Revises: d7c4a2f19b60
Create Date: 2026-08-05 00:00:00.000000

Three related changes, in one revision because updated_at and updated_by_user_id
are the same idea and splitting them would mean two passes over nine tables.

1. `products.list_price` / `order_items.list_price` - the price BEFORE the
   discount, stored instead of reconstructed. Until now the only stored figure
   was the discounted `price`, and the "was $X" shown on the catalog and on
   every printed quote was recovered as `price / (1 - discount/100)` in three
   separate implementations. That made the original price a derived number that
   silently MOVED whenever the charged price was edited: repricing an item from
   $90 to $80 while leaving the 10% discount alone changed its "was" price from
   $100.00 to $88.89 with nobody having touched it.

   The backfill below runs that same reconstruction once, so existing rows keep
   exactly the figure they display today - after this migration it just stops
   drifting. Both columns end up NOT NULL, so nothing downstream has to handle a
   missing list price.

2. `updated_at` on every entity table. Only creation was recorded before, so
   "when did this last change" was unanswerable. Maintained by SQLAlchemy's
   `onupdate` (all writes go through the ORM), not a DB trigger.

3. `updated_by_user_id` on the same tables - the staff member who last wrote to
   the row. ON DELETE SET NULL so deactivating/removing a staff account can
   never block an edit or delete a row. NULL is meaningful and common: it's what
   a customer editing their own profile, or a row created before this migration,
   looks like.

   Worth knowing what this column does NOT tell you: it is overwritten by the
   next write of ANY field, so a description fix erases the name attached to
   yesterday's price change, and it never records what the old value was. If
   price accountability specifically matters later, that wants an append-only
   history table; this column is not a substitute for one.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a9c4e18b73'
down_revision: Union[str, Sequence[str], None] = 'd7c4a2f19b60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Every table that represents an editable entity. Deliberately excludes the join
# tables (promotion_items, set_items, product_free_items - replaced wholesale, never
# edited in place) and order_items (immutable snapshots of a placed order; the one
# column added there is list_price, part of the snapshot itself).
_AUDITED_TABLES = (
    'users',
    'customers',
    'brands',
    'categories',
    'products',
    'manuals',
    'promotions',
    'sets',
    'orders',
)


def upgrade() -> None:
    """Upgrade schema."""
    # --- 1. list_price -----------------------------------------------------
    # Added nullable, backfilled, then made NOT NULL - the usual three-step, since
    # existing rows have no value and the column has no sensible constant default.
    op.add_column('products', sa.Column('list_price', sa.Numeric(10, 2), nullable=True))
    op.add_column('order_items', sa.Column('list_price', sa.Numeric(10, 2), nullable=True))

    # The reconstruction being retired, run one last time. The `discount >= 100`
    # branch is not hypothetical: a 100% percent discount is allowed by the schema
    # and would divide by zero here.
    op.execute(
        """
        UPDATE products SET list_price = CASE
            WHEN discount IS NULL OR discount = 0 THEN price
            WHEN discount_type = 'cash' THEN price + discount
            WHEN discount >= 100 THEN price
            ELSE ROUND(price / (1 - discount / 100.0), 2)
        END
        """
    )
    op.execute(
        """
        UPDATE order_items SET list_price = CASE
            WHEN discount IS NULL OR discount = 0 THEN unit_price
            WHEN discount_type = 'cash' THEN unit_price + discount
            WHEN discount >= 100 THEN unit_price
            ELSE ROUND(unit_price / (1 - discount / 100.0), 2)
        END
        """
    )

    op.alter_column('products', 'list_price', nullable=False)
    op.alter_column('order_items', 'list_price', nullable=False)

    # --- 2 + 3. updated_at / updated_by_user_id ----------------------------
    for table in _AUDITED_TABLES:
        # server_default=now() so existing rows get a value and the column can be
        # NOT NULL. It means every pre-existing row reads "updated at migration
        # time", which is honest enough - the real history was never recorded.
        op.add_column(
            table,
            sa.Column(
                'updated_at',
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.add_column(
            table,
            sa.Column('updated_by_user_id', sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            f'fk_{table}_updated_by_user_id',
            table,
            'users',
            ['updated_by_user_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in _AUDITED_TABLES:
        op.drop_constraint(f'fk_{table}_updated_by_user_id', table, type_='foreignkey')
        op.drop_column(table, 'updated_by_user_id')
        op.drop_column(table, 'updated_at')

    op.drop_column('order_items', 'list_price')
    op.drop_column('products', 'list_price')
