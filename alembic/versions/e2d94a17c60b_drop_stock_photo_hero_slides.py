"""drop the stock-photo hero slides

Revision ID: e2d94a17c60b
Revises: c8a1f36b04d7
Create Date: 2026-08-22

b8c3f5079e14 created hero_slides and seeded it with the three slides the
carousel used to have hard-coded in the Flask template. Their artwork is
hot-linked from images.unsplash.com, which was fine while the site was
half-hosted anyway - but the system now runs entirely self-hosted on a Windows
server, with every picture mirrored onto local disk and served from /static
(see scripts/localize_media.py). Three slides that reach out to a third-party
CDN are the one remaining thing that breaks when that box has no outbound
internet, and they are stock photography nobody chose.

So they go. Only rows still carrying their original seeded URL are removed: a
slide someone has since re-pointed at an uploaded picture, or reworded, is
theirs and is left alone. Deleting by image URL rather than by id also means
this does nothing at all on an instance where they were already cleared by
hand, which is the case on the live database this was written against.

Not reversible in the strict sense - downgrade() puts the three stock slides
back, but any that had already been deleted before this ran come back too.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e2d94a17c60b"
down_revision = "c8a1f36b04d7"
branch_labels = None
depends_on = None


# The three rows seeded by b8c3f5079e14, copied verbatim so upgrade() can match
# them and downgrade() can put them back exactly as they were.
#
#   (heading, heading_highlight, subheading, slide_image,
#    badge_label, badge_icon, button_label, button_url)
_STOCK_SLIDES = (
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
    connection = op.get_bind()
    for slide in _STOCK_SLIDES:
        heading, image = slide[0], slide[3]
        connection.execute(
            sa.text(
                "DELETE FROM hero_slides "
                "WHERE heading = :heading AND slide_image = :image"
            ),
            {"heading": heading, "image": image},
        )


def downgrade() -> None:
    connection = op.get_bind()
    for position, slide in enumerate(_STOCK_SLIDES, start=1):
        connection.execute(
            sa.text(
                "INSERT INTO hero_slides ("
                "  heading, heading_highlight, subheading, slide_image,"
                "  badge_label, badge_icon, button_label, button_url,"
                "  is_active, sort_order"
                ") VALUES ("
                "  :heading, :heading_highlight, :subheading, :slide_image,"
                "  :badge_label, :badge_icon, :button_label, :button_url,"
                "  true, :sort_order"
                ")"
            ),
            dict(
                zip(
                    (
                        "heading",
                        "heading_highlight",
                        "subheading",
                        "slide_image",
                        "badge_label",
                        "badge_icon",
                        "button_label",
                        "button_url",
                    ),
                    slide,
                ),
                sort_order=position,
            ),
        )
