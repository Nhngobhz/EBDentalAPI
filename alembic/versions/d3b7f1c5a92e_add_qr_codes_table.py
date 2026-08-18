"""add qr_codes table (contact page department QR cards)

Revision ID: d3b7f1c5a92e
Revises: b6f29d40a71c
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3b7f1c5a92e'
down_revision: Union[str, Sequence[str], None] = 'b6f29d40a71c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The four cards as they were hard-coded: the settings key each caption came from, the
# picture that was dropped in the Flask app's static/images/qr/, and the badge that was
# markup in templates/contact.html.
#
#   (title key, default title, subtitle key, default subtitle, image file,
#    badge label, badge variant, badge icon)
_LEGACY_CARDS = (
    ('qr_technician_title', 'Technician Support',
     'qr_technician_sub', 'Technical Support Team',
     'technician-support.png', 'Support', '', 'fa-wrench'),
    ('qr_machine_title', 'Machine Sale',
     'qr_machine_sub', 'Sales Department - Machinery',
     'machine-sale.png', 'Machinery', 'machinery', 'fa-gear'),
    ('qr_material_title', 'Material Sale',
     'qr_material_sub', 'Sales Department - Materials',
     'material-sale.png', 'Materials', 'materials', 'fa-flask'),
    ('qr_slock_title', 'Slock Implant',
     'qr_slock_sub', 'Implant Department',
     'slock-implant.png', 'Implants', 'implants', 'fa-tooth'),
)


def upgrade() -> None:
    """Upgrade schema.

    The contact page's QR cards become rows instead of a fixed four: their captions
    were settings (spec group "qr", now removed) and their pictures were files an
    admin could only change by dropping a PNG into the Flask app's static folder.
    Neither could grow to a fifth department, which is what this table is for.
    """
    op.create_table(
        'qr_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('subtitle', sa.String(length=200), nullable=True),
        # Nullable: a card may exist before its picture is uploaded - the storefront
        # shows its "QR coming soon" placeholder until then.
        sa.Column('qr_image', sa.String(length=500), nullable=True),
        sa.Column('badge_label', sa.String(length=60), nullable=True),
        sa.Column('badge_variant', sa.String(length=30), nullable=True),
        sa.Column('badge_icon', sa.String(length=60), nullable=True),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_qr_codes_id'), 'qr_codes', ['id'], unique=False)

    _seed_legacy_cards()


def _seed_legacy_cards() -> None:
    """Recreate the four existing cards so the contact page looks unchanged after this
    runs, keeping any caption an admin had already edited on the Settings screen.

    The images are seeded to /static/uploads/qr/<file>.png: the four PNGs were copied
    out of the Flask app's static/images/qr/ into this repo's own static/uploads/ as
    part of this change, and that directory is served by the /static mount whether or
    not R2 is configured (R2 only decides where NEW uploads go - see core/storage.py).
    A deployment that somehow lacks those files just shows the "QR coming soon"
    placeholder until someone re-uploads them through the admin Settings screen.

    The old app_settings override rows are deliberately NOT deleted: nothing reads a
    key the spec no longer declares, and leaving them means `downgrade()` puts the old
    Settings group back with the admin's own wording rather than the defaults.
    """
    conn = op.get_bind()

    # Every override row, filtered in Python rather than with a LIKE 'qr\_%': the table
    # holds a handful of rows at most, and a literal % in raw SQL is a psycopg2
    # parameter-interpolation trap not worth stepping into for this.
    # `value` is a JSON column, so psycopg2 hands back the decoded Python value.
    overrides = {
        row[0]: row[1]
        for row in conn.execute(sa.text("SELECT key, value FROM app_settings"))
    }

    insert = sa.text(
        "INSERT INTO qr_codes (title, subtitle, qr_image, badge_label, badge_variant,"
        " badge_icon, sort_order)"
        " VALUES (:title, :subtitle, :qr_image, :badge_label, :badge_variant,"
        " :badge_icon, :sort_order)"
    )

    position = 0
    for title_key, title_default, sub_key, sub_default, image, label, variant, icon in _LEGACY_CARDS:
        title = str(overrides.get(title_key, title_default) or '').strip()
        # An empty title hid the card entirely under the old rules, so a card the admin
        # had switched off that way stays switched off - as no row at all.
        if not title:
            continue
        subtitle = str(overrides.get(sub_key, sub_default) or '').strip()
        position += 1
        conn.execute(
            insert,
            {
                'title': title,
                'subtitle': subtitle or None,
                'qr_image': f'/static/uploads/qr/{image}',
                'badge_label': label,
                'badge_variant': variant,
                'badge_icon': icon,
                'sort_order': position,
            },
        )


def downgrade() -> None:
    """Downgrade schema.

    Drops the table and its rows. Cards added since the upgrade are lost, which is
    unavoidable - the settings spec they would go back to has room for exactly four.
    The original four reappear from their app_settings rows (see _seed_legacy_cards).
    """
    op.drop_index(op.f('ix_qr_codes_id'), table_name='qr_codes')
    op.drop_table('qr_codes')
