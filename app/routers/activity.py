"""
Reading the activity log.

Read-only, and that is enforced by there being no other kind of endpoint here: the
table is append-only by design (see ActivityLog in app/models.py), so there is no
update, no delete, and no "clear log" button for someone to reach for after a bad
afternoon. Rows are written by the flush listener in app/core/activity.py, never by a
request to this router.

Gated on `admin` alone, not the price_listing-or-admin pair the rest of the Reports
screen uses. The log spans every table at once - staff accounts, permissions, prices,
customers' details - so "who changed what across the whole store" is an owner's
question, and answering it for a sales account would hand them a feed of everyone
else's work. The same rule covers the per-record History panel, deliberately: one rule
is one thing to reason about, and a panel that showed what the list withheld would be
the same leak through a smaller window.
"""
from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.core.query import Limit, OptionalDate, OptionalInt, Skip
from app.database import get_db
from app.models import ActivityLog, User
from app.schemas import ActivityLogOut, ActivityLogPage

router = APIRouter(prefix="/activity", tags=["Activity"])

_admin = Depends(require_permission("admin"))

# How many entries a record's History panel shows. Small on purpose: the panel is a
# sidebar answering "what happened to this lately", and anyone who wants the whole
# story follows the link into the filtered list.
ENTITY_HISTORY_LIMIT = 25

# The clock the screen prints and therefore the clock its date filters bound. Same
# constant and same reasoning as app/services/telegram_format.py: Cambodia is UTC+7
# all year, so a fixed offset is exact and needs no tzdata on the Windows server.
ICT = timezone(timedelta(hours=7))


def _apply_filters(query, actor_type, actor_user_id, action, entity_type, date_from, date_to, q):
    if actor_type:
        query = query.filter(ActivityLog.actor_type == actor_type)
    if actor_user_id is not None:
        query = query.filter(ActivityLog.actor_user_id == actor_user_id)
    if action:
        query = query.filter(ActivityLog.action == action)
    if entity_type:
        query = query.filter(ActivityLog.entity_type == entity_type)
    if date_from:
        # The admin picks a day, not an instant - and they pick it while reading a
        # screen that prints every timestamp on the Cambodia clock, so the day has to
        # be bounded by Cambodian midnight or the filter disagrees with the column it
        # is filtering. UTC midnight put the boundary at 7am local: "what happened on
        # the 2nd" silently dropped everything before breakfast and pulled in the tail
        # of the 1st. A fixed offset, not the host's local time, so the answer is still
        # the same on any machine that runs this.
        query = query.filter(
            ActivityLog.occurred_at >= datetime.combine(date_from, time.min, tzinfo=ICT)
        )
    if date_to:
        # Inclusive of the chosen day, which is what picking the same date in both
        # boxes has to mean - `<= date_to` alone would match only its first instant.
        query = query.filter(
            ActivityLog.occurred_at <= datetime.combine(date_to, time.max, tzinfo=ICT)
        )
    if q:
        term = f"%{q.strip()}%"
        # The three human-readable columns. `changes` is cast rather than joined into
        # because it is JSON: searching it as text is the difference between finding
        # "who touched the 95.00 price" and not being able to ask at all, and the log
        # is small enough (and the screen rare enough) that a scan is acceptable.
        query = query.filter(
            or_(
                ActivityLog.actor_label.ilike(term),
                ActivityLog.entity_label.ilike(term),
                ActivityLog.note.ilike(term),
                cast(ActivityLog.changes, String).ilike(term),
            )
        )
    return query


@router.get("/", response_model=ActivityLogPage)
def list_activity(
    actor_type: str | None = Query(default=None),
    actor_user_id: OptionalInt = None,
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    q: str | None = Query(default=None),
    skip: Skip = 0,
    limit: Limit = 50,
    db: Session = Depends(get_db),
    current_user: User = _admin,
):
    """The Reports screen's Activity Log, newest first.

    Every filter is optional and they combine, which is the whole interface: "what did
    Sopheak do on Tuesday" is actor + date, "who has been changing prices" is
    entity_type + a search for the number.
    """
    query = _apply_filters(
        db.query(ActivityLog), actor_type, actor_user_id, action, entity_type,
        date_from, date_to, q,
    )
    # Counted before the pager is applied, over the same filters - that is the number
    # the screen needs to say "1-50 of 812".
    total = query.with_entities(func.count(ActivityLog.id)).scalar() or 0
    items = (
        query.order_by(ActivityLog.occurred_at.desc(), ActivityLog.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return ActivityLogPage(items=items, total=total)


@router.get("/filters", response_model=dict)
def activity_filters(db: Session = Depends(get_db), current_user: User = _admin):
    """The values actually present in the log, for the filter dropdowns.

    Derived from the log rather than from a hard-coded list of tables and actions, so
    the dropdowns can't offer a filter that matches nothing - and so a new entity type
    or a new action needs no change here to become filterable.
    """
    actors = (
        db.query(ActivityLog.actor_user_id, func.max(ActivityLog.actor_label))
        .filter(ActivityLog.actor_user_id.isnot(None))
        .group_by(ActivityLog.actor_user_id)
        .all()
    )
    entity_types = [
        row[0] for row in db.query(ActivityLog.entity_type).distinct().order_by(ActivityLog.entity_type)
    ]
    actions = [row[0] for row in db.query(ActivityLog.action).distinct().order_by(ActivityLog.action)]
    return {
        # max() over the labels, not the current user_name: a renamed account should
        # still be findable, and this at least offers its most recent spelling.
        "actors": [{"id": uid, "label": label} for uid, label in actors if uid],
        "entity_types": entity_types,
        "actions": actions,
    }


@router.get("/entity/{entity_type}/{entity_id}", response_model=list[ActivityLogOut])
def entity_history(
    entity_type: str,
    entity_id: int,
    limit: int = Query(default=ENTITY_HISTORY_LIMIT, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = _admin,
):
    """One record's history, for the panel on its admin screen.

    `entity_type` is the __tablename__ ("products", "orders"), matching what the
    listener writes - the caller already knows which screen it is on, so nothing has
    to be looked up to ask this.

    An unknown type or a record with no history returns an empty list rather than a
    404: "nothing has happened to this yet" is a real and common answer, including for
    every row that existed before this table did.
    """
    return (
        db.query(ActivityLog)
        .filter(ActivityLog.entity_type == entity_type, ActivityLog.entity_id == entity_id)
        .order_by(ActivityLog.occurred_at.desc(), ActivityLog.id.desc())
        .limit(limit)
        .all()
    )
