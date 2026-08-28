"""add hero_slides.section + promotions.section, swap categories.category_image for category_icon

Revision ID: a2f9c7b41e83
Revises: c4f27a8b3d61
Create Date: 2026-08-28 00:00:00.000000

Three changes, all consequences of materials having become a shop of its own rather
than one listing page bolted onto the machinery catalog (see c8a1f36b04d7, which gave
products the same column these two now get).

**hero_slides.section / promotions.section.** The rotating banner and the deals it
advertises existed on the machinery side only, so neither row needed an opinion about
where it belonged. The materials front page now has its own hero and its own promo
strip, and the two shops sell to different buyers on different budgets - a slide about
a $9,000 scanner on the page a clinic buys gloves from is not marketing, it is noise.
Rather than a second hero_slides table and a second promotions table, the same rows
carry a section and each page asks for its own.

Backfill for both is the whole table: every slide and every promotion written before
today is machinery, which is exactly what the server_default says, so no UPDATE is
needed. NOT NULL for the same reason products.section is - "which shop" always has an
answer, and a nullable column would push that decision onto every read path.

**categories.category_image -> categories.category_icon.** The image was never used
outside the admin table. No storefront page ever rendered it: the machinery catalog
shows category *names*, and the materials pages draw a Font Awesome glyph picked by a
keyword map (blueprints/materials.py::category_icon), because 824 of the 855
categories arrived from SAP with no picture and no prospect of one. What was left was
an upload path, a storage folder and a thumbnail column maintained for a 36px circle
only an admin ever saw.

The icon column replaces it as an *override*: null means "keep guessing from the
name", which is the normal case and why the column is nullable. Only categories the
keyword map gets wrong need a value.

Existing image values are dropped with the column and are not migrated into anything -
a URL is not a glyph, and there is nothing to translate one into. The files themselves
stay where they are in storage; nothing in the schema points at them any more, so they
can be swept separately if anyone cares. downgrade() puts an empty column back, which
is honest: the pictures cannot be recovered from a column that no longer holds them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2f9c7b41e83'
down_revision: Union[str, Sequence[str], None] = 'c4f27a8b3d61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default is load-bearing, not cosmetic: both columns are NOT NULL on
    # tables that already have rows, so the ALTER fails outright without it. It is
    # also the correct backfill - see the module docstring.
    op.add_column(
        'hero_slides',
        sa.Column('section', sa.String(length=20), nullable=False, server_default='machinery'),
    )
    op.add_column(
        'promotions',
        sa.Column('section', sa.String(length=20), nullable=False, server_default='machinery'),
    )

    op.add_column('categories', sa.Column('category_icon', sa.String(length=60), nullable=True))
    op.drop_column('categories', 'category_image')


def downgrade() -> None:
    """Downgrade schema."""
    # Nullable, so the rows that survive the round trip come back image-less rather
    # than blocking the ALTER. The pictures are gone either way - see the docstring.
    op.add_column('categories', sa.Column('category_image', sa.String(length=500), nullable=True))
    op.drop_column('categories', 'category_icon')

    op.drop_column('promotions', 'section')
    op.drop_column('hero_slides', 'section')
