"""
Reading and writing the site-wide settings declared in app/core/settings_spec.py.

Everything goes through `get_all()`, which merges the spec's defaults with whatever
override rows exist in `app_settings`. A caller never has to think about whether a key
has been customized - it just gets a complete dict with every key present.

**Caching.** These values are read on nearly every request (the storefront footer) and
change roughly never, so the merged dict is memoized process-wide for `_CACHE_TTL`
seconds. Writes invalidate immediately, so an admin who saves a change sees it on the
next page load. Other worker processes / the second container pick it up within the TTL -
which is the deliberate trade: a settings change is not a transaction that has to be
globally visible the instant it commits, and paying a query per request forever to
pretend otherwise isn't worth it.

`build_invoice_pdf` runs outside any request (it's called from the Telegram service), so
`get_all()` opens its own short-lived session when it isn't handed one. That's why the
`db` parameter is optional everywhere in this module.
"""
import threading
import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.activity import record_event
from app.core.settings_spec import DEFAULTS, PUBLIC_KEYS, SETTINGS, SettingError, coerce
from app.database import SessionLocal
from app.models import AppSetting

_CACHE_TTL = 30  # seconds

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_expires_at: float = 0.0


def _load(db: Session) -> dict[str, Any]:
    values = dict(DEFAULTS)
    for row in db.query(AppSetting).all():
        # A row for a key the spec no longer declares is ignored rather than deleted:
        # removing a setting from the spec shouldn't destroy the value in case it comes
        # back, and a stale row is harmless because nothing reads it.
        if row.key in SETTINGS:
            values[row.key] = row.value
    return values


def get_all(db: Session | None = None, *, fresh: bool = False) -> dict[str, Any]:
    """Every setting, defaults merged with overrides. Pass `fresh=True` to bypass the
    cache - used right after a write so the response echoes what was actually saved."""
    global _cache, _cache_expires_at

    now = time.monotonic()
    if not fresh:
        cached = _cache
        if cached is not None and _cache_expires_at > now:
            return cached

    if db is not None:
        values = _load(db)
    else:
        own = SessionLocal()
        try:
            values = _load(own)
        finally:
            own.close()

    with _lock:
        _cache = values
        _cache_expires_at = time.monotonic() + _CACHE_TTL
    return values


def get(key: str, db: Session | None = None) -> Any:
    return get_all(db).get(key, DEFAULTS.get(key))


def get_public(db: Session | None = None) -> dict[str, Any]:
    """The subset safe to serve to an anonymous caller - see `public` in the spec."""
    values = get_all(db)
    return {key: values[key] for key in PUBLIC_KEYS if key in values}


def invalidate() -> None:
    global _cache, _cache_expires_at
    with _lock:
        _cache = None
        _cache_expires_at = 0.0


def save(db: Session, updates: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    """Validate and persist a partial dict of settings. Returns the full merged result.

    Every value is coerced *before* anything is written, so a form with one bad field
    fails cleanly instead of saving half of itself. A value equal to its default deletes
    the override row rather than storing a duplicate of the default - which keeps "reset
    to default" and "typed the default value back in" from behaving differently later if
    the default ever changes.
    """
    coerced: dict[str, Any] = {}
    for key, raw in updates.items():
        if key not in SETTINGS:
            raise SettingError(f"Unknown setting '{key}'")
        coerced[key] = coerce(key, raw)

    existing = {row.key: row for row in db.query(AppSetting).filter(AppSetting.key.in_(coerced))}

    for key, value in coerced.items():
        row = existing.get(key)
        if value == DEFAULTS[key]:
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            row = AppSetting(key=key)
            db.add(row)
        row.value = value
        row.updated_by_user_id = user_id

    db.commit()
    invalidate()
    return get_all(db, fresh=True)


def reset(db: Session, keys: list[str], user_id: int | None = None) -> dict[str, Any]:
    """Drop the override rows for `keys`, putting them back on their spec defaults."""
    unknown = [key for key in keys if key not in SETTINGS]
    if unknown:
        raise SettingError(f"Unknown setting '{unknown[0]}'")
    if keys:
        # Logged by hand because the delete below is a bulk statement: it never loads
        # the rows, so the flush listener in app/core/activity.py has nothing to see.
        # The sibling `save()` needs no such help - it deletes through the ORM.
        for key in keys:
            record_event(
                db,
                action="settings_reset",
                entity_type="app_settings",
                entity_label=key,
                note="Reset to default",
            )
        db.query(AppSetting).filter(AppSetting.key.in_(keys)).delete(synchronize_session=False)
        db.commit()
    invalidate()
    return get_all(db, fresh=True)
