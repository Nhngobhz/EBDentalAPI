"""add set option groups and choices

Revision ID: f7a3e02c8b41
Revises: e4c1a97b25f0
Create Date: 2026-08-18 00:00:00.000000

Turns a Set from a fixed bundle into a configurable one: alongside its always-included
contents (set_items) it can now carry swappable slots - "Laptop", "X-ray model" - each
offering several products, where picking a dearer one adds to the set's price.

Shape:
  set_option_groups   one slot per row, ordered, belonging to a set
  set_option_choices  one alternative per row, belonging to a group

Two things worth knowing about the choices table:

  * `price_delta` is NULLABLE and that is the interesting state, not an oversight. NULL
    means "derive the upcharge from the price gap between this product and the group's
    default, at today's prices", which is how it stays correct for free when either
    product is repriced. A stored number is the deliberate override for an upgrade
    priced as a deal rather than at cost. Negative values are allowed - a cheaper
    alternative is a legitimate option.

  * the default choice is enforced by a PARTIAL unique index rather than a constraint:
    `UNIQUE (group_id) WHERE is_default`. Two defaults in one group would make "what
    does this set cost as standard" ambiguous and leave the storefront preselecting
    whichever row happened to come back first.

order_items gains `set_options`, a JSON snapshot of which choice each group landed on.
It is not how the customer learns what they bought - the $0 component lines already name
the chosen products. It exists because update_order re-prices every line from
{set_id, qty} through _build_order_lines, so without the selection travelling with the
line, a saved upgrade would quietly revert to defaults the first time staff corrected a
phone number on the order.

Nothing to backfill: every existing set has no groups, which resolves to no choices and
a delta of zero - i.e. exactly the fixed set it was before this ran.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a3e02c8b41'
down_revision: Union[str, Sequence[str], None] = 'e4c1a97b25f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'set_option_groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('set_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['set_id'], ['sets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_set_option_groups_id'), 'set_option_groups', ['id'])
    op.create_index(op.f('ix_set_option_groups_set_id'), 'set_option_groups', ['set_id'])

    op.create_table(
        'set_option_choices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False, server_default='1'),
        # Nullable on purpose - see the module docstring.
        sa.Column('price_delta', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['group_id'], ['set_option_groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'product_id', name='uq_set_option_choice'),
    )
    op.create_index(op.f('ix_set_option_choices_id'), 'set_option_choices', ['id'])
    op.create_index(op.f('ix_set_option_choices_group_id'), 'set_option_choices', ['group_id'])
    op.create_index(op.f('ix_set_option_choices_product_id'), 'set_option_choices', ['product_id'])
    # At most one default per group.
    op.create_index(
        'uq_set_option_group_default',
        'set_option_choices',
        ['group_id'],
        unique=True,
        postgresql_where=sa.text('is_default'),
    )

    op.add_column('order_items', sa.Column('set_options', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('order_items', 'set_options')
    op.drop_index('uq_set_option_group_default', table_name='set_option_choices')
    op.drop_index(op.f('ix_set_option_choices_product_id'), table_name='set_option_choices')
    op.drop_index(op.f('ix_set_option_choices_group_id'), table_name='set_option_choices')
    op.drop_index(op.f('ix_set_option_choices_id'), table_name='set_option_choices')
    op.drop_table('set_option_choices')
    op.drop_index(op.f('ix_set_option_groups_set_id'), table_name='set_option_groups')
    op.drop_index(op.f('ix_set_option_groups_id'), table_name='set_option_groups')
    op.drop_table('set_option_groups')
