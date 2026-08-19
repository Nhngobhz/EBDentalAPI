"""add products.is_purchasable

Revision ID: e4c1a97b25f0
Revises: d3b7f1c5a92e
Create Date: 2026-08-18 00:00:00.000000

Marks a product that exists only as somebody else's freebie. It still expands into a $0
component line under its parent (routers/orders.py::_component_items), but ordering it
on its own is refused in _build_order_lines and it is dropped from the public catalog
listing.

The prompt for it: the SCAN11 gift items (a scanner stand and a set of zirconia teeth)
were added to the catalog at $0 so they would print on the quote, which also left them
sitting on the storefront as products anyone could add to a cart for nothing.

Why a stored flag rather than a derived one - the two obvious derivations are both
wrong:

  * "it appears in product_free_items" - most freebies are also ordinary products. The
    water distiller ($114) and SEAL120 ($152) come free with every autoclave AND sell
    on their own, as do the trolley, laptop, two combos and contra-angle bundled with
    LX16-PLUS. 7 of 9 freebie rows at the time of writing. Deriving would delete them
    from the storefront.
  * "its price is 0" - conflates a consequence with the rule. It silently reverses the
    moment a gift item is priced at $1, and it blocks a genuine giveaway that IS meant
    to be orderable.

Backfill is the whole table: every product that existed before this column was sellable,
which is what the server_default already says, so no UPDATE is needed here. The two
gift-only rows are flipped afterwards through the admin screen / seed rather than in the
migration - which products are gifts is catalog data, not schema.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4c1a97b25f0'
down_revision: Union[str, Sequence[str], None] = 'd3b7f1c5a92e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default is required, not cosmetic: the column is NOT NULL and the table
    # already has rows, so without it the ALTER fails outright. It also happens to be
    # the correct backfill - see the module docstring.
    op.add_column(
        'products',
        sa.Column('is_purchasable', sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('products', 'is_purchasable')
