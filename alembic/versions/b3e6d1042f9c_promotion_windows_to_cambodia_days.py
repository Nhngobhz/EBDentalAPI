"""re-anchor promotion start_date/end_date onto Cambodian days

Revision ID: b3e6d1042f9c
Revises: a2f9c7b41e83
Create Date: 2026-09-02 00:00:00.000000

The admin's promotion form has always been two <input type="date"> boxes: pick the day
it starts, pick the day it ends. Those two days were sent to this API as
"YYYY-MM-DDT00:00:00" and "YYYY-MM-DDT23:59:59" with no UTC offset on them, and
`start_date`/`end_date` are DateTime(timezone=True) - so Postgres read them as UTC and
the window landed seven hours late in the only timezone anyone here reads a date in.

A deal set to run the 19th to the 30th was, in Phnom Penh, live from 07:00 on the 19th
until 06:59 on the 1st. Small, but wrong in both directions at once, and wrong in the
direction people notice: a promotion announced for today is not on the site when the
shop opens.

The form now sends "+07:00" (see _date_to_iso in the admin's promotions blueprint), so
new and re-saved promotions are correct. This moves the rows already saved to match,
by subtracting the seven hours they were never meant to carry.

Only rows bearing the form's exact signature are touched: 00:00:00 UTC for a start,
23:59:59 UTC for an end. That is what the form, and nothing else, produced. A window
set through the API directly with a real offset or an arbitrary time of day means what
it says and is left alone - shifting it would be inventing an intent that isn't there.
The two columns are matched independently for the same reason.

downgrade() shifts the same signature back, which is now 17:00:00 / 16:59:59 UTC - the
same instants seen from the other side.
"""
from alembic import op

revision = "b3e6d1042f9c"
down_revision = "a2f9c7b41e83"
branch_labels = None
depends_on = None

# AT TIME ZONE 'UTC' on a timestamptz yields the UTC wall clock, which is the clock the
# old form's values were (mis)recorded against - so this asks "did this row come from
# the form?" and nothing else.
UPGRADE = """
UPDATE promotions
   SET start_date = start_date - INTERVAL '7 hours'
 WHERE (start_date AT TIME ZONE 'UTC')::time = TIME '00:00:00'
"""

UPGRADE_END = """
UPDATE promotions
   SET end_date = end_date - INTERVAL '7 hours'
 WHERE (end_date AT TIME ZONE 'UTC')::time = TIME '23:59:59'
"""

DOWNGRADE = """
UPDATE promotions
   SET start_date = start_date + INTERVAL '7 hours'
 WHERE (start_date AT TIME ZONE 'UTC')::time = TIME '17:00:00'
"""

DOWNGRADE_END = """
UPDATE promotions
   SET end_date = end_date + INTERVAL '7 hours'
 WHERE (end_date AT TIME ZONE 'UTC')::time = TIME '16:59:59'
"""


def upgrade():
    op.execute(UPGRADE)
    op.execute(UPGRADE_END)


def downgrade():
    op.execute(DOWNGRADE)
    op.execute(DOWNGRADE_END)
