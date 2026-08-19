"""add manuals.title

Revision ID: d1f6b83a45c9
Revises: b8c3f5079e14
Create Date: 2026-08-19 00:00:00.000000

A product has always been able to carry several manuals - `manuals.product_id` is a
plain many-to-one and `Product.manuals` is a collection - but there was no way to tell
two of them apart: the storefront fetched one row, captioned it "Product Manual" and
ignored the rest. `title` is what makes a set of documents ("User Manual", "Quick Start
Guide", "Service Manual") legible, so a product can usefully have more than one.

Nullable, with no backfill. Every manual that predates this column has no title, and
the storefront falls back to "Product Manual" for those - inventing a title here would
be guessing at what a document is called, which is exactly the thing only a human
knows. Staff can name them as they touch them.

Note for whoever adds the next field here: `POST /manuals/` is multipart/form-data and
builds its Manual from an EXPLICIT argument list, not `model_dump()`. A column added
only to the schema is accepted by validation and then silently dropped.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1f6b83a45c9'
down_revision: Union[str, Sequence[str], None] = 'b8c3f5079e14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('manuals', sa.Column('title', sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('manuals', 'title')
