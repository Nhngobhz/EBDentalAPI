"""add hero_slides table (storefront hero carousel)

Revision ID: b8c3f5079e14
Revises: f7a3e02c8b41
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8c3f5079e14'
down_revision: Union[str, Sequence[str], None] = 'f7a3e02c8b41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The three slides exactly as they were hard-coded in the Flask app's
# templates/partials/hero_slider.html, so the carousel looks unchanged after this runs.
#
#   (heading, heading_highlight, subheading, slide_image,
#    badge_label, badge_icon, button_label, button_url)
_LEGACY_SLIDES = (
    ('Equip Your Practice with', 'Excellence',
     'Discover high-quality dental instruments and equipment from world-class brands'
     ' trusted by professionals.',
     'https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?w=1400&h=900&fit=crop&auto=format',
     'Premium Dental Supply', 'fa-tooth', 'Explore Products', '/products'),
    ('Advanced Technology for', 'Better Outcomes',
     'From endodontic motors to digital X-ray systems - we bring you the latest in'
     ' dental innovation.',
     'https://images.unsplash.com/photo-1584017911766-d451b3d0e0de?w=1400&h=900&fit=crop&auto=format',
     'Precision Instruments', 'fa-microscope', 'View Collection', '/products'),
    ('Partner with', 'Industry Leaders',
     'Woodpecker, NSK, KaVo, Dentsply, and more - curated for performance and'
     ' reliability.',
     'https://images.unsplash.com/photo-1587825140708-dfaf72ae4b04?w=1400&h=900&fit=crop&auto=format',
     'Trusted Brands', 'fa-medal', 'Browse Brands', '/products'),
)


def upgrade() -> None:
    """Upgrade schema.

    The hero carousel becomes rows instead of three <div class="slide"> blocks in a
    Jinja partial: the copy, the artwork and the number of slides were all developer-
    only, so a campaign banner meant a code change and a deploy. This table makes the
    carousel ordinary admin CRUD (Marketing & Sales -> Hero Slider).
    """
    op.create_table(
        'hero_slides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('heading', sa.String(length=200), nullable=False),
        # Nullable: the coloured tail of the heading is optional, as are the badge,
        # the paragraph and the call-to-action - a slide may be artwork and a title.
        sa.Column('heading_highlight', sa.String(length=120), nullable=True),
        sa.Column('subheading', sa.String(length=400), nullable=True),
        # Nullable: a slide may exist before its artwork is uploaded.
        sa.Column('slide_image', sa.String(length=500), nullable=True),
        sa.Column('badge_label', sa.String(length=60), nullable=True),
        sa.Column('badge_icon', sa.String(length=60), nullable=True),
        sa.Column('button_label', sa.String(length=60), nullable=True),
        sa.Column('button_url', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_hero_slides_id'), 'hero_slides', ['id'], unique=False)

    _seed_legacy_slides()


def _seed_legacy_slides() -> None:
    """Recreate the three existing slides so the storefront looks unchanged.

    Their `slide_image` keeps pointing at the same external stock photos the template
    hard-coded - resolve_image_url() in the Flask app passes a full http(s) URL through
    untouched, so nothing has to be copied into this repo's static/ for the seed to
    render. Replacing one with the shop's own photography is now an upload on the Hero
    Slider screen, which is rather the point of this migration.
    """
    conn = op.get_bind()

    insert = sa.text(
        "INSERT INTO hero_slides (heading, heading_highlight, subheading, slide_image,"
        " badge_label, badge_icon, button_label, button_url, is_active, sort_order)"
        " VALUES (:heading, :heading_highlight, :subheading, :slide_image,"
        " :badge_label, :badge_icon, :button_label, :button_url, true, :sort_order)"
    )

    for position, slide in enumerate(_LEGACY_SLIDES, start=1):
        heading, highlight, subheading, image, label, icon, btn_label, btn_url = slide
        conn.execute(
            insert,
            {
                'heading': heading,
                'heading_highlight': highlight,
                'subheading': subheading,
                'slide_image': image,
                'badge_label': label,
                'badge_icon': icon,
                'button_label': btn_label,
                'button_url': btn_url,
                'sort_order': position,
            },
        )


def downgrade() -> None:
    """Downgrade schema.

    Drops the table and its rows. Slides added since the upgrade are lost, which is
    unavoidable - the markup they would go back to has room for exactly three, spelled
    out in the template.
    """
    op.drop_index(op.f('ix_hero_slides_id'), table_name='hero_slides')
    op.drop_table('hero_slides')
