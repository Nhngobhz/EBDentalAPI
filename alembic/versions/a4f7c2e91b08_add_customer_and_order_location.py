"""add customer + order latitude/longitude/map_link

Revision ID: a4f7c2e91b08
Revises: d1f6b83a45c9
Create Date: 2026-08-19 00:00:00.000000

`customers.address` and `orders.address` are free text, which is enough to print on a
quotation and not nearly enough to deliver against - a Phnom Penh address is routinely
a landmark ("behind the pagoda, opposite the pharmacy") rather than anything a map can
resolve. These six columns add the thing that actually gets a driver there: a dropped
pin.

Three columns per table rather than one, because the customer supplies the location in
two ways and neither reliably yields the other:

  * dropping a pin gives coordinates and no link the customer ever typed;
  * pasting a Google Maps SHORT link (maps.app.goo.gl/...) that could not be expanded
    gives a link a human can open and no coordinates at all.

Storing whichever half exists beats forcing one to be derived from the other and
throwing the value away when the derivation fails.

All six are nullable with no backfill and no server_default. There is no defensible
default for "where is this customer" - inventing one (the shop's own coordinates, say)
would put a confident wrong pin on every historical row, which is strictly worse than
an empty one that reads as "not set yet".

On `orders` these are a snapshot, like clinic_name/phone/address beside them: the order
records where the buyer pointed when they bought, not wherever their profile points
today. Pending KHQR checkouts carry the same three values through
`pending_checkouts.snapshot`, which is JSON and needs no migration - see
routers/orders.py::_materialize_checkout, which reads them with .get() precisely
because checkouts issued before this revision have no such keys.

Numeric(9, 6) is deliberate over Float: six decimal places is ~11cm, which is far finer
than any pin a human drops, and Numeric keeps the value exact through the
string-serialized round trip every other Decimal in this schema makes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4f7c2e91b08'
down_revision: Union[str, Sequence[str], None] = 'd1f6b83a45c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    for table in ('customers', 'orders'):
        op.add_column(table, sa.Column('latitude', sa.Numeric(precision=9, scale=6), nullable=True))
        op.add_column(table, sa.Column('longitude', sa.Numeric(precision=9, scale=6), nullable=True))
        op.add_column(table, sa.Column('map_link', sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    for table in ('customers', 'orders'):
        op.drop_column(table, 'map_link')
        op.drop_column(table, 'longitude')
        op.drop_column(table, 'latitude')
