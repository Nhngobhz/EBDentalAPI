"""add customer authentication fields

Revision ID: 7f3a9c1d8b2e
Revises: df054ac0cc38
Create Date: 2026-07-09 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f3a9c1d8b2e'
down_revision: Union[str, Sequence[str], None] = 'df054ac0cc38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('customers', sa.Column('hashed_password', sa.String(length=255), nullable=True))
    op.add_column(
        'customers',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.add_column(
        'customers',
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.add_column('customers', sa.Column('verification_token', sa.String(length=255), nullable=True))
    op.add_column(
        'customers', sa.Column('verification_token_expires', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('customers', sa.Column('reset_token', sa.String(length=255), nullable=True))
    op.add_column(
        'customers', sa.Column('reset_token_expires', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('customers', sa.Column('last_login', sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        op.f('ix_customers_verification_token'), 'customers', ['verification_token'], unique=True
    )
    op.create_index(op.f('ix_customers_reset_token'), 'customers', ['reset_token'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_customers_reset_token'), table_name='customers')
    op.drop_index(op.f('ix_customers_verification_token'), table_name='customers')
    op.drop_column('customers', 'last_login')
    op.drop_column('customers', 'reset_token_expires')
    op.drop_column('customers', 'reset_token')
    op.drop_column('customers', 'verification_token_expires')
    op.drop_column('customers', 'verification_token')
    op.drop_column('customers', 'is_verified')
    op.drop_column('customers', 'is_active')
    op.drop_column('customers', 'hashed_password')
