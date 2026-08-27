"""add activity_log table (who changed what, and what it was before)

Revision ID: f4b7d16c9e30
Revises: e2d94a17c60b
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b7d16c9e30'
down_revision: Union[str, Sequence[str], None] = 'e2d94a17c60b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Adds the history the `updated_at` / `updated_by_user_id` pair from f2a9c4e18b73
    could never be. Those columns say who wrote a row last and nothing more: they are
    overwritten by the next write, they keep no previous value, and they are deleted
    along with the row they describe. This table keeps one append-only entry per
    change, old value beside new, outliving both the row and the account that changed
    it.

    There is NO backfill, and there can't be: every change made before this migration
    ran left no record of itself anywhere. The log starts empty and starts now, so the
    per-record History panel reads "nothing recorded yet" for existing rows until
    somebody touches them - which is honest, and better than inventing a first entry
    from `updated_at` that would claim more than that column knows.
    """
    op.create_table(
        'activity_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column(
            'occurred_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        # "user" / "customer" / "system". String rather than an enum, like every other
        # small value set here (discount_type, order_type) - a fourth kind of actor
        # should be a code change, not a migration.
        sa.Column('actor_type', sa.String(length=20), server_default='system', nullable=False),
        # SET NULL on both: deleting a staff member or a customer must not erase what
        # they did. actor_label is what keeps the entry readable afterwards.
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('actor_customer_id', sa.Integer(), nullable=True),
        sa.Column('actor_label', sa.String(length=150), nullable=True),
        sa.Column('action', sa.String(length=30), nullable=False),
        sa.Column('entity_type', sa.String(length=60), nullable=False),
        # Nullable: a failed sign-in changed no row, and app_settings is keyed by a
        # string whose key goes in entity_label instead.
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('entity_label', sa.String(length=300), nullable=True),
        # {"price": ["120.00", "95.00"]} - old first, new second.
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['actor_customer_id'], ['customers.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_activity_log_id'), 'activity_log', ['id'], unique=False)
    # Newest-first is the only order this table is ever read in, and a plain
    # single-column btree serves it - Postgres scans one backwards as cheaply as
    # forwards, so there is no separate DESC index here.
    op.create_index(op.f('ix_activity_log_occurred_at'), 'activity_log', ['occurred_at'], unique=False)
    # The per-record History panel's whole query.
    op.create_index('ix_activity_log_entity', 'activity_log', ['entity_type', 'entity_id'], unique=False)
    # "what did this person do", the second filter the Reports screen offers.
    op.create_index('ix_activity_log_actor_user', 'activity_log', ['actor_user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    Drops the table and every entry in it. Unlike most downgrades here that is
    genuinely lossy - the history exists nowhere else, by definition - so this is a
    "we decided not to do this after all" path, not a routine one.
    """
    op.drop_index('ix_activity_log_actor_user', table_name='activity_log')
    op.drop_index('ix_activity_log_entity', table_name='activity_log')
    op.drop_index(op.f('ix_activity_log_occurred_at'), table_name='activity_log')
    op.drop_index(op.f('ix_activity_log_id'), table_name='activity_log')
    op.drop_table('activity_log')
