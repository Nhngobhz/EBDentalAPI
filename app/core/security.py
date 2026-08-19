"""
Password hashing (bcrypt) and JWT access-token helpers.

We call bcrypt directly rather than going through passlib: recent
bcrypt/passlib version combinations are known to raise spurious warnings,
and the direct API is all of five lines.
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt

from app.config import settings

# bcrypt has a hard 72-byte input limit - enforced in schemas too
# (password max_length=72), this is just a defensive second check.
_BCRYPT_MAX_BYTES = 72


def hash_password(plain_password: str) -> str:
    password_bytes = plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes = plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def token_lifetime(account_type: Optional[str]) -> timedelta:
    """How long a token for `account_type` ("user" or "customer") stays valid.

    Anything unrecognised - including None - gets the short staff lifetime on purpose:
    if a new kind of principal ever appears and nobody updates this, it should fail
    towards the safer session length rather than silently inherit the 14-day one."""
    minutes = (
        settings.CUSTOMER_TOKEN_EXPIRE_MINUTES
        if account_type == "customer"
        else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return timedelta(minutes=minutes)


def create_access_token(data: dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """`data` carries the "type" claim every caller already sets, so the right lifetime
    is chosen here rather than at each of the five call sites that mint tokens (login,
    Google sign-in, and the customer-only login router). A call site that genuinely
    needs a different window still passes `expires_delta` explicitly."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta is not None else token_lifetime(data.get("type"))
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError (or a subclass) if the token is invalid/expired."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def generate_url_safe_token() -> str:
    """Used for email-verification and password-reset tokens."""
    return secrets.token_urlsafe(32)
