"""
Recording who last wrote to a row (see AuditedMixin in app/models.py).

One helper rather than `obj.updated_by_user_id = current_user.id` sprinkled
through fifteen routers, so the "is there actually a staff user here?" question
is answered in one place. It's a real question: the same shape of update arrives
from a customer editing their own profile (`PUT /customers/me`), where there is
no `User` at all and the column must stay NULL rather than be filled in with
whoever happened to touch the row last.
"""
from typing import Optional, TypeVar

from app.models import User

T = TypeVar("T")


def stamp_updated_by(obj: T, user: Optional[User]) -> T:
    """Record `user` as the last editor of `obj`, if there is one.

    Returns the object so it can be used inline. Does NOT touch `updated_at` -
    that's `AuditedMixin`'s `onupdate`, which fires on flush whether or not a
    staff user was involved.

    Passing None is normal and explicitly supported (a customer self-service
    edit): the column is simply left as it was.
    """
    if user is not None:
        obj.updated_by_user_id = user.id
    return obj
