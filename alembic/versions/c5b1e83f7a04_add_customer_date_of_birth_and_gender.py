"""add customers.date_of_birth and customers.gender

Revision ID: c5b1e83f7a04
Revises: b2e7f45a91c6
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5b1e83f7a04'
down_revision: Union[str, Sequence[str], None] = 'b2e7f45a91c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Both nullable with no backfill: every customer that predates these columns
    # simply has them empty until they fill them in on their profile page (or a
    # customer_management staff member does via PUT /customers/{id}).
    op.add_column('customers', sa.Column('date_of_birth', sa.Date(), nullable=True))
    op.add_column('customers', sa.Column('gender', sa.String(length=10), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('customers', 'gender')
    op.drop_column('customers', 'date_of_birth')
