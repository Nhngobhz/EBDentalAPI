"""change product discount to percent integer

Revision ID: 1dc22197e9f2
Revises: 8ba8532524f6
Create Date: 2026-07-15 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1dc22197e9f2'
down_revision: Union[str, Sequence[str], None] = '8ba8532524f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # discount drops the free-form "10%"/"5$" label in favor of a plain
    # percentage (0-100). Existing "$" amounts are converted to the
    # equivalent percentage off price (price is the post-discount figure,
    # so price + amount recovers the original pre-discount price); existing
    # "%" values are parsed as-is. NULL/unparseable values become 0.
    op.execute(
        """
        ALTER TABLE products
        ALTER COLUMN discount TYPE integer USING (
            LEAST(GREATEST(
                CASE
                    WHEN discount IS NULL THEN 0
                    WHEN right(discount, 1) = '%' THEN
                        ROUND(NULLIF(regexp_replace(discount, '[^0-9.]', '', 'g'), '')::numeric)
                    WHEN right(discount, 1) = '$' THEN
                        ROUND(
                            NULLIF(regexp_replace(discount, '[^0-9.]', '', 'g'), '')::numeric
                            / NULLIF(price + NULLIF(regexp_replace(discount, '[^0-9.]', '', 'g'), '')::numeric, 0)
                            * 100
                        )
                    ELSE 0
                END,
            0), 100)::integer
        )
        """
    )
    op.execute("ALTER TABLE products ALTER COLUMN discount SET DEFAULT 0")
    op.execute("ALTER TABLE products ALTER COLUMN discount SET NOT NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE products ALTER COLUMN discount DROP NOT NULL")
    op.execute("ALTER TABLE products ALTER COLUMN discount DROP DEFAULT")
    op.execute(
        """
        ALTER TABLE products
        ALTER COLUMN discount TYPE varchar(20) USING (
            CASE WHEN discount = 0 THEN NULL ELSE discount::text || '%' END
        )
        """
    )
