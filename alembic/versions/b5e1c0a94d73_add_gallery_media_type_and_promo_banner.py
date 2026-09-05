"""add product_images.media_type and promotions.banner_image

Revision ID: b5e1c0a94d73
Revises: a7f31c05b982
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5e1c0a94d73'
down_revision: Union[str, Sequence[str], None] = 'a7f31c05b982'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Whether a gallery row is a photo or a video. The two share `product_images`
    # because the product page shows ONE ordered rail and a second table would need a
    # second sort_order with no defined interleaving - see models.ProductImage.
    #
    # NOT NULL with server_default 'image': every row that exists is a photo, so the
    # backfill is "all of them" and the default handles it without a separate UPDATE.
    # The server_default stays on the column afterwards (rather than being dropped once
    # the table is filled) so that a plain INSERT from psql or a seed script that
    # predates videos still produces a valid row.
    op.add_column(
        'product_images',
        sa.Column(
            'media_type',
            sa.String(length=10),
            nullable=False,
            server_default='image',
        ),
    )

    # The wide artwork for the storefront hero slide, kept apart from the square card
    # image because they are different pictures rather than two crops of one.
    #
    # Nullable with NO backfill, deliberately: copying promotion_image in here would
    # make every existing deal look like someone had chosen a banner for it, and the
    # storefront falls back to promotion_image on NULL anyway. So the rendered result
    # is identical either way, and leaving it NULL keeps "nobody has uploaded a banner
    # for this deal" a fact the admin screen can still tell you.
    op.add_column('promotions', sa.Column('banner_image', sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Dropping banner_image loses the uploaded banners' paths (the files themselves
    # stay in the bucket / upload dir, orphaned). Nothing else to undo: the hero slide
    # simply goes back to reading promotion_image for every deal, which is what it did
    # before this revision.
    op.drop_column('promotions', 'banner_image')
    # Any row that was a video becomes indistinguishable from a photo, so the
    # storefront would try to render it in an <img>. Delete them rather than leave
    # broken rows behind - the stored files are orphaned either way by this point.
    op.execute("DELETE FROM product_images WHERE media_type = 'video'")
    op.drop_column('product_images', 'media_type')
