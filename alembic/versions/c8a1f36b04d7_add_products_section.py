"""add products.section

Revision ID: c8a1f36b04d7
Revises: a4f7c2e91b08
Create Date: 2026-08-20 00:00:00.000000

Splits the product table into the two halves the storefront already shows: machinery
and materials.

Until now that split was purely presentational - site_section() in the Flask app
(app.py) decides which logo to render by looking at request.endpoint, and nothing in
the database distinguished one kind of product from another. That was correct while
every product WAS machinery and /materials was a coming-soon placeholder.

It stops being correct the moment materials arrive. They come from SAP Business One,
which holds that item master and only that one - machinery is never going into SAP -
so the two sets are about to share a table while needing to stay out of each other's
catalog pages. A column is what lets every read path filter.

Backfill is the whole table: everything that existed before this column is machinery
by definition, which is exactly what the server_default says, so no UPDATE is needed.

Deliberately no index on this column yet. It has two values, and on a few hundred rows
Postgres will seq-scan regardless. It becomes worth revisiting only if the imported
materials catalog turns out to be large enough that machinery is a small fraction of
the table - and the SAP discovery report (scripts/sap_discover.py) is what will say
whether that is true, so the decision is cheap to defer and guessy to make now.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8a1f36b04d7'
down_revision: Union[str, Sequence[str], None] = 'a4f7c2e91b08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default is required rather than cosmetic: the column is NOT NULL and the
    # table already has rows, so without it the ALTER fails outright. It is also the
    # correct backfill - see the module docstring.
    op.add_column(
        'products',
        sa.Column('section', sa.String(length=20), nullable=False, server_default='machinery'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('products', 'section')
