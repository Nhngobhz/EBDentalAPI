"""
The activity log: recording every change, automatically.

`stamp_updated_by` (app/core/audit.py) answers "who wrote this row last" and nothing
else. This module keeps the history that pair can't: one append-only `activity_log`
row per change, with the old value beside the new one, surviving the deletion of
whatever it describes.

It is a SQLAlchemy session listener rather than `record_event(...)` calls in the
routers, and that is the whole design. Fifty call sites would be fifty chances to
forget one, and the writes easiest to forget are the ones that matter most - nine of
the twenty tables in app/models.py carry no audit columns at all, including an order's
line items and a set's contents. A listener on the flush sees every ORM write there
is, including the ones nobody remembered to instrument.

Two things it deliberately does NOT see:

* `query(...).update()` / `.delete()`, which go straight to SQL without loading rows.
  There are two in this codebase: the settings reset (logged explicitly, see
  app/services/app_settings.py) and the sweep's prune of expired checkouts
  (deliberately not logged - housekeeping nobody asked for isn't activity).
* Anything written outside the app - psql, a migration. The same caveat AuditedMixin
  carries, for the same reason.

## How the actor gets here

`session.info`, not a ContextVar. FastAPI runs sync dependencies in a threadpool with a
*copy* of the context, so a ContextVar set inside `get_current_user` would be invisible
to the route handler that follows it. The Session object, on the other hand, is one
shared instance for the whole request (FastAPI caches `get_db` per request), so hanging
the actor off it works from anywhere and cleans itself up when the session closes.
"""
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.models import ActivityLog, Customer, User

# ---------------------------------------------------------------------------
# What not to record
# ---------------------------------------------------------------------------

# Bookkeeping that changes on every write and describes no decision anyone made.
# `last_login` is the important one: without it, every sign-in would file an
# "updated user" entry, and the log's whole value is that scrolling it is useful.
#
# `stock_synced_at` is the same trap one size larger. scripts/sap_sync.py stamps it
# on all ~8,000 materials every run to record that the figure was re-confirmed, so
# without this a nightly sync would file 8,000 "updated product" entries a night -
# each one saying only that a timestamp moved, and between them burying the handful
# of rows that say a price changed. Note that `stock_qty` is NOT here: the quantity
# moving IS worth recording, and only the confirmation of it is noise.
IGNORED_FIELDS = frozenset(
    {"updated_at", "updated_by_user_id", "created_at", "last_login", "stock_synced_at"}
)

# Matched as substrings against the column name, case-insensitively. The row still
# records that the field changed - only the values are replaced. A log of who changed
# a password must never become a log of what it was changed to, and the same goes for
# the reset/confirmation tokens sitting beside it.
REDACTED_FIELDS = ("password", "token", "secret", "hash")
REDACTED_PLACEHOLDER = "***"

# Columns too big to be worth keeping. `snapshot` is an entire order-to-be as JSON;
# storing a copy per checkout would make the log larger than the data it describes,
# and the checkout row itself is still there to read.
IGNORED_COLUMNS = {"pending_checkouts": {"snapshot"}}

# The log doesn't log itself.
IGNORED_TABLES = frozenset({"activity_log"})

# Long text (a product description, a terms paragraph) is cut here. The point of a
# recorded value is recognising the change, not reproducing the document.
MAX_VALUE_CHARS = 300


# ---------------------------------------------------------------------------
# Rolling child rows up onto their parent
# ---------------------------------------------------------------------------

# "Someone changed line 3 of order 41" belongs in order 41's history, not in a
# history of its own that nothing links to. Each entry maps a child table to its
# parent table and a way of getting the parent's id off the child row.
#
# set_option_choices reaches its Set through its group, which is why these are
# callables rather than column names.
#
# The third element is what the child is CALLED in the resulting note ("Added photo").
# A callable there when one table holds more than one kind of thing - product_images
# carries both, and "Removed photo" against a deleted video is simply wrong.
CHILD_ROLLUP = {
    "order_items": ("orders", lambda o: getattr(o, "order_id", None), "line item"),
    "product_images": (
        "products",
        lambda o: getattr(o, "product_id", None),
        lambda o: "video" if getattr(o, "media_type", "image") == "video" else "photo",
    ),
    "product_free_items": (
        "products",
        lambda o: getattr(o, "parent_product_id", None),
        "free item",
    ),
    "promotion_items": (
        "promotions",
        lambda o: getattr(o, "promotion_id", None),
        "included product",
    ),
    "set_items": ("sets", lambda o: getattr(o, "set_id", None), "included product"),
    "set_option_groups": ("sets", lambda o: getattr(o, "set_id", None), "option group"),
    "set_option_choices": (
        "sets",
        lambda o: getattr(getattr(o, "group", None), "set_id", None),
        "option choice",
    ),
}

# How a rolled-up child's own create/update/delete reads once it is filed under the
# parent, which is always an "update" from the parent's point of view.
CHILD_VERB = {"create": "Added", "update": "Edited", "delete": "Removed"}

# First match wins. `key` is last because app_settings has nothing else to call
# itself, and generic names are tried after specific ones so a Product named by both
# `product_name` and `name` uses the former.
LABEL_FIELDS = (
    "order_number",
    "product_name",
    "user_name",
    "customer_name",
    "title",
    "heading",
    "name",
    "reference",
    "key",
)


# ---------------------------------------------------------------------------
# Actor
# ---------------------------------------------------------------------------

def set_actor(
    db: Session,
    user: Optional[User] = None,
    customer: Optional[Customer] = None,
) -> None:
    """Record who is behind the writes on this session.

    Called from the auth dependencies, so any request that identified itself is
    attributed without a router doing anything. A session nobody claims logs as
    "system", which is correct for the background sweep and honest for anything else
    that reaches the database without a signed-in principal.
    """
    if user is not None:
        db.info["activity_actor"] = ("user", user.id, user.user_name)
    elif customer is not None:
        db.info["activity_actor"] = (
            "customer",
            customer.id,
            customer.customer_name or customer.email,
        )


def _actor_columns(db: Session) -> dict:
    actor_type, actor_id, label = db.info.get("activity_actor", ("system", None, None))
    return {
        "actor_type": actor_type,
        "actor_user_id": actor_id if actor_type == "user" else None,
        "actor_customer_id": actor_id if actor_type == "customer" else None,
        "actor_label": _truncate(label) if label else None,
    }


# ---------------------------------------------------------------------------
# Value handling
# ---------------------------------------------------------------------------

def _truncate(value: str) -> str:
    value = str(value)
    return value if len(value) <= MAX_VALUE_CHARS else value[: MAX_VALUE_CHARS - 1] + "…"


def _jsonable(value: Any) -> Any:
    """Turn a column value into something JSON can hold and a human can read.

    Decimal becomes a string rather than a float on purpose: these are prices, and
    "95.00" is what the admin typed and what the screen should echo back. A float
    would show 95.0 and invite the question of whether something rounded.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, (dict, list)):
        return _truncate(json.dumps(value, default=str, separators=(",", ":")))
    return _truncate(value)


def _redacted(field: str) -> bool:
    lowered = field.lower()
    return any(marker in lowered for marker in REDACTED_FIELDS)


def _pair(field: str, old: Any, new: Any) -> list:
    if _redacted(field):
        return [REDACTED_PLACEHOLDER, REDACTED_PLACEHOLDER]
    if isinstance(old, Decimal) or isinstance(new, Decimal):
        return _decimal_pair(old, new)
    return [_jsonable(old), _jsonable(new)]


MONEY_PLACES = 2


def _decimal_pair(old: Any, new: Any) -> list:
    """Both sides of a numeric change written to the same, sensible number of places.

    Two separate problems, one fix. Postgres hands back a column's full scale
    (`Decimal("3705.00")`) while the value arriving from JSON carries only what was
    typed (`Decimal("3705.0")`), so plain `str()` reads as "3705.00 -> 3705.0" - which
    looks like a change and isn't one. And `grand_total` is stored at scale 4, so it
    would print "3499.5000" directly beneath `subtotal`'s "3499.50" in the same entry.

    So: enough places for the values to be exact, never fewer than two, and the same
    count on both sides. Trailing zeros the column happens to carry are not
    information; a third decimal that is actually there always survives.
    """
    try:
        places = max(
            # normalize() strips the zeros the column's scale padded on, leaving the
            # places the number genuinely needs.
            max(-value.normalize().as_tuple().exponent, 0)
            for value in (old, new)
            if isinstance(value, Decimal)
        )
    except (TypeError, ValueError):
        # NaN/Infinity normalize to a symbolic exponent. Not reachable from a money
        # column, but a log entry is never worth an exception.
        return [_jsonable(old), _jsonable(new)]

    places = max(places, MONEY_PLACES)
    return [
        format(value, f".{places}f") if isinstance(value, Decimal) else _jsonable(value)
        for value in (old, new)
    ]


# ---------------------------------------------------------------------------
# Reading changes off an object
# ---------------------------------------------------------------------------

def _columns(obj) -> list:
    table = obj.__tablename__
    skip = IGNORED_FIELDS | IGNORED_COLUMNS.get(table, set())
    return [attr.key for attr in inspect(obj).mapper.column_attrs if attr.key not in skip]


def _insert_changes(obj) -> dict:
    """Every column a new row was actually given. Columns left at their default are
    omitted - "sort_order: null -> null" is not information."""
    changes = {}
    for key in _columns(obj):
        value = getattr(obj, key, None)
        if value is None:
            continue
        changes[key] = _pair(key, None, value)
    return changes


def _update_changes(session: Session, obj) -> dict:
    """Only the columns this flush is actually rewriting, old value included.

    Attribute history alone is not enough for the old value, and this is the subtlest
    thing in the file. Assigning to an attribute that isn't currently loaded records
    the previous value as "there wasn't one" - SQLAlchemy can't invent what it never
    read - and an attribute is unloaded after any commit expires its object. So a
    router that commits and then edits the same row again (orders.py does exactly
    this) would file "price: null -> 95" and silently lose the 95 it replaced.

    `_missing_old` closes that by reading the row itself. Inside `before_flush` the
    UPDATE hasn't been emitted yet, so the database still holds the pre-change values
    - one extra SELECT, only for the columns that actually need it, and only when
    history came up short.
    """
    state = inspect(obj)
    changed: dict[str, Any] = {}
    unknown_old = []
    for key in _columns(obj):
        history = state.attrs[key].load_history()
        if not history.has_changes():
            continue
        new = history.added[0] if history.added else None
        if history.deleted:
            old = history.deleted[0]
            if old == new:
                continue
        else:
            old = None
            unknown_old.append(key)
        changed[key] = (old, new)

    if unknown_old:
        for key, old in _missing_old(session, obj, unknown_old).items():
            changed[key] = (old, changed[key][1])

    # Compared after the fetch, not before: a "change" that restored the value already
    # in the database isn't one, and until the old value is known there is no way to
    # tell.
    return {
        key: _pair(key, old, new)
        for key, (old, new) in changed.items()
        if old != new
    }


def _missing_old(session: Session, obj, keys: list) -> dict:
    """The row's current (pre-flush) values for `keys`, straight from the database.

    `no_autoflush` is not optional here: a SELECT inside `before_flush` would
    otherwise trigger the very flush being prepared, and the row would already carry
    the new values by the time it was read - turning every diff into "x -> x".
    """
    mapper = inspect(obj).mapper
    pk = mapper.primary_key
    if len(pk) != 1:
        return {}
    pk_value = getattr(obj, pk[0].key, None)
    if pk_value is None:
        return {}

    table = mapper.local_table
    columns = [table.c[key] for key in keys if key in table.c]
    if not columns:
        return {}

    with session.no_autoflush:
        row = session.execute(
            select(*columns).where(pk[0] == pk_value)
        ).first()
    if row is None:
        return {}
    return dict(zip([column.key for column in columns], row))


def _delete_changes(obj) -> dict:
    """The whole row as it stood, since after this flush there is nowhere else to read
    it. This is the case the `updated_by` columns cannot cover at all: they go with the
    row."""
    changes = {}
    for key in _columns(obj):
        try:
            value = getattr(obj, key, None)
        except Exception:
            # An orphan collected after its DELETE has already gone out can no longer
            # refresh an unloaded attribute - the row it would read is gone. Whatever
            # is still in memory is worth keeping; one missing column is not worth
            # losing the entry over.
            continue
        if value is None:
            continue
        changes[key] = _pair(key, value, None)
    return changes


def _identity(obj) -> Optional[int]:
    """The row's integer primary key, or None for the one table keyed by a string.

    app_settings is keyed by `key`, so it has no integer id to file under; its key
    lands in entity_label instead, which is what the screens show anyway.
    """
    pk = inspect(obj).mapper.primary_key
    if len(pk) != 1:
        return None
    value = getattr(obj, pk[0].key, None)
    return value if isinstance(value, int) else None


def _label(obj) -> Optional[str]:
    """What to call this row in a sentence a person reads.

    The explicit list first, then any `*_name` column - which is what catches
    `brand_name`, `category_name` and every table that follows the same convention
    without having to be listed here one by one. A row with nothing name-shaped on it
    (a join row) gets None, and the screens fall back to its type and id.
    """
    for field in LABEL_FIELDS:
        value = getattr(obj, field, None)
        if isinstance(value, str) and value.strip():
            return _truncate(value.strip())
    for key in (attr.key for attr in inspect(obj).mapper.column_attrs):
        if not key.endswith("_name"):
            continue
        value = getattr(obj, key, None)
        if isinstance(value, str) and value.strip():
            return _truncate(value.strip())
    return None


# ---------------------------------------------------------------------------
# The listener
# ---------------------------------------------------------------------------

def _collect(session: Session, obj, action: str) -> Optional[dict]:
    table = obj.__tablename__
    if table in IGNORED_TABLES:
        return None

    if action == "create":
        changes = _insert_changes(obj)
    elif action == "delete":
        changes = _delete_changes(obj)
    else:
        changes = _update_changes(session, obj)
        # A flush that only moved `updated_at` isn't a change anyone made.
        if not changes:
            return None

    return {"obj": obj, "table": table, "action": action, "changes": changes}


def _orphaned_children(session: Session, obj) -> list:
    """Children about to be deleted because they were dropped from a delete-orphan
    collection - the case neither half of the flush would otherwise catch.

    This is how an order's lines are replaced: `order.items = built` in
    app/routers/orders.py leaves the cascade to remove whatever is no longer in the
    list. Those rows never pass through `session.deleted` at all - orphan detection
    registers them straight with the flush context - so without this, replacing an
    order's lines would log every line added and none removed.

    Read with the non-loading `history` property, NOT `load_history()`, and that is
    load-bearing in the other direction from `_update_changes`: a collection that was
    never touched must stay untouched. Loading it would mean fetching every line of an
    order to discover that a phone number changed. A collection that WAS replaced is
    in memory already, which is exactly the case this needs to see.
    """
    state = inspect(obj)
    removed = []
    for relationship in state.mapper.relationships:
        if not relationship.cascade.delete_orphan:
            continue
        history = state.attrs[relationship.key].history
        for child in history.deleted:
            entry = _collect(session, child, "delete")
            if entry:
                removed.append(entry)
    return removed


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, flush_context, instances) -> None:
    """Read the pending changes while they are still readable.

    Everything here has to happen before the flush: after it, an update's previous
    value is gone and a deleted object's columns are no longer reachable. Only the
    entity *ids* have to wait for `after_flush`, which is why this half stashes
    objects rather than finished rows.
    """
    pending = []
    for obj in session.new:
        entry = _collect(session, obj, "create")
        if entry:
            pending.append(entry)
    for obj in session.dirty:
        if session.is_modified(obj, include_collections=False):
            entry = _collect(session, obj, "update")
            if entry:
                pending.append(entry)
        # Checked whether or not any column changed: replacing an order's lines
        # without touching a field of the order itself is an ordinary edit, and
        # `is_modified(..., include_collections=False)` says no to it by design.
        pending.extend(_orphaned_children(session, obj))
    for obj in session.deleted:
        entry = _collect(session, obj, "delete")
        if entry:
            pending.append(entry)

    if pending:
        session.info.setdefault("activity_pending", []).extend(pending)


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, flush_context) -> None:
    """Turn the stashed changes into rows, now that new objects have their ids.

    Written with a Core insert rather than by adding ORM objects, and that matters:
    adding to the session here would need another flush, which would re-enter this
    listener. `session.execute` joins the same transaction without touching the unit
    of work - so if the request goes on to fail, the log entries roll back with the
    changes they describe, which is the behaviour you want.
    """
    pending = session.info.pop("activity_pending", None)
    if not pending:
        return

    # A parent's own create/delete already describes its children; logging "added line
    # item" fifteen times underneath "created order 41" would bury it.
    structural = {
        (entry["table"], _identity(entry["obj"]))
        for entry in pending
        if entry["action"] in ("create", "delete")
    }

    actor = _actor_columns(session)
    rows = []
    for entry in pending:
        row = _row_for(session, entry, structural)
        if row is not None:
            rows.append({**actor, **row})

    if rows:
        session.execute(ActivityLog.__table__.insert(), rows)


def _row_for(session: Session, entry: dict, structural: set) -> Optional[dict]:
    obj, table = entry["obj"], entry["table"]
    action, changes = entry["action"], entry["changes"]
    rollup = CHILD_ROLLUP.get(table)

    if rollup is None:
        return {
            "action": action,
            "entity_type": table,
            "entity_id": _identity(obj),
            "entity_label": _label(obj),
            "changes": changes or None,
            "note": None,
        }

    parent_table, parent_id_of, child_name = rollup
    if callable(child_name):
        child_name = child_name(obj)
    parent_id = parent_id_of(obj)
    if parent_id is None or (parent_table, parent_id) in structural:
        return None

    return {
        # Always an update: from the parent's side, its contents changed.
        "action": "update",
        "entity_type": parent_table,
        "entity_id": parent_id,
        "entity_label": _parent_label(session, parent_table, parent_id),
        "changes": changes or None,
        "note": f"{CHILD_VERB[action]} {child_name}",
    }


def _parent_label(session: Session, table: str, parent_id: int) -> Optional[str]:
    """Name the parent without going back to the database if it is already loaded.

    It usually is - you cannot edit an order's lines without having loaded the order -
    so this is an identity-map hit rather than a query. `no_autoflush` guards the case
    where it isn't: a lazy load inside a flush listener would otherwise try to flush
    the very changes being logged.
    """
    mapper = _mapper_for(table)
    if mapper is None:
        return None
    with session.no_autoflush:
        parent = session.get(mapper.class_, parent_id)
    return _label(parent) if parent is not None else None


def _mapper_for(table: str):
    for mapper in ActivityLog.registry.mappers:
        if mapper.local_table is not None and mapper.local_table.name == table:
            return mapper
    return None


# ---------------------------------------------------------------------------
# Events that aren't row changes
# ---------------------------------------------------------------------------

def record_event(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    entity_label: Optional[str] = None,
    changes: Optional[dict] = None,
    note: Optional[str] = None,
    actor_user: Optional[User] = None,
) -> None:
    """File an entry the flush listener can't see.

    Three kinds of thing need this: something that changed no row at all (a sign-in, a
    rejected sign-in), something written with a bulk `query(...).delete()` that never
    loads its rows (the settings reset), and something where the diff is true but says
    nothing useful on its own ("marked paid" beside `payment_status`).

    `actor_user` exists for the sign-in case specifically, where the actor is being
    established by this very request and the session doesn't know them yet.

    Does NOT commit. The caller decides, and one caller has to: a failed sign-in ends
    in a 401, and a request that raises never reaches a commit - so auth.py commits
    that one itself, on purpose.
    """
    actor = _actor_columns(db)
    if actor_user is not None:
        actor = {
            "actor_type": "user",
            "actor_user_id": actor_user.id,
            "actor_customer_id": None,
            "actor_label": _truncate(actor_user.user_name),
        }
    db.execute(
        ActivityLog.__table__.insert(),
        [
            {
                **actor,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "entity_label": _truncate(entity_label) if entity_label else None,
                "changes": changes or None,
                "note": _truncate(note) if note else None,
            }
        ],
    )
