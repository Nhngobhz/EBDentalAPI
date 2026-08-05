"""
Brute-force throttling for the credential endpoints.

Before this, `POST /auth/login` (and the customer/reset equivalents) could be
called as fast as the network allowed: an 8-character-minimum password with no
attempt limit is guessable given enough requests, and nothing anywhere recorded
that someone was trying. bcrypt makes each guess expensive, which slows an
attacker down but also means a guessing run doubles as a cheap way to saturate
the API's threadpool.

Deliberately small in scope:

* **In-process, in-memory.** store-api runs as a single Uvicorn worker (see
  entrypoint.sh), so one dict is the whole picture. Behind multiple workers or
  replicas this becomes per-worker - still useful, but the honest fix at that
  point is a shared store (Redis) or a rate limit at the reverse proxy, not a
  bigger dict here.
* **Failures only.** A successful login clears the counter, so a legitimate user
  who mistypes a few times and then gets it right is never locked out.
* **Keyed on (client IP, identifier).** Both, not either: keying on IP alone
  would let one office NAT lock out its own staff, and keying on the email alone
  would let anyone lock a known address out at will. An attacker has to be
  throttled per target *and* per source.

The limiter is intentionally not applied to `POST /auth/google` - that endpoint
verifies an RS256 signature from Google rather than a guessable secret, so
there's nothing to brute-force.
"""
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

from app.core.logging_conf import get_logger

logger = get_logger("ratelimit")

# Attempts allowed inside WINDOW_SECONDS before the key is locked out. Generous
# enough that a human fumbling a password never notices it exists.
MAX_ATTEMPTS = 10
WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60

# Housekeeping: prune expired keys when the map grows past this, so a long
# guessing run against many addresses can't grow the dict without bound.
_PRUNE_THRESHOLD = 1024


@dataclass
class _Attempts:
    timestamps: list[float] = field(default_factory=list)
    locked_until: float = 0.0


_attempts: dict[tuple[str, str], _Attempts] = {}


def _client_ip(request: Request) -> str:
    """The caller's address, honouring a single X-Forwarded-For hop.

    store-api sits behind the Flask app / a reverse proxy in every real
    deployment, so request.client.host is usually the proxy. The left-most
    X-Forwarded-For entry is client-controlled and must never be trusted for
    authorization - here it only ever *narrows* who shares a throttle bucket,
    which is why using it is safe: forging it can lock out only yourself.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def _prune(now: float) -> None:
    if len(_attempts) < _PRUNE_THRESHOLD:
        return
    stale = [
        key
        for key, record in _attempts.items()
        if record.locked_until < now
        and (not record.timestamps or record.timestamps[-1] < now - WINDOW_SECONDS)
    ]
    for key in stale:
        _attempts.pop(key, None)


def check_login_allowed(request: Request, identifier: str) -> None:
    """Raise 429 if this (IP, identifier) pair is currently locked out.

    Call this BEFORE verifying the password, so a locked-out caller never
    reaches bcrypt at all - that's the part an attacker would otherwise use to
    burn server CPU.
    """
    now = time.time()
    record = _attempts.get((_client_ip(request), identifier.lower()))
    if record is None or record.locked_until <= now:
        return
    retry_after = int(record.locked_until - now) + 1
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed sign-in attempts. Please try again in a few minutes.",
        headers={"Retry-After": str(retry_after)},
    )


def record_login_failure(request: Request, identifier: str) -> None:
    """Count one failed attempt, locking the pair out once MAX_ATTEMPTS is hit."""
    now = time.time()
    ip = _client_ip(request)
    key = (ip, identifier.lower())
    record = _attempts.setdefault(key, _Attempts())
    record.timestamps = [t for t in record.timestamps if t > now - WINDOW_SECONDS]
    record.timestamps.append(now)
    if len(record.timestamps) >= MAX_ATTEMPTS:
        record.locked_until = now + LOCKOUT_SECONDS
        record.timestamps.clear()
        # Logged at warning (not error) on purpose: this is worth seeing in the
        # log, but routing every scan attempt to the Telegram error topic would
        # make that topic useless.
        logger.warning(
            "Locking out sign-in attempts for %s from %s for %s minutes "
            "(%s consecutive failures).",
            identifier, ip, LOCKOUT_SECONDS // 60, MAX_ATTEMPTS,
        )
    _prune(now)


def record_login_success(request: Request, identifier: str) -> None:
    """Clear the counter - a correct password proves this wasn't a guessing run."""
    _attempts.pop((_client_ip(request), identifier.lower()), None)


def reset() -> None:
    """Drop all state. For tests, which would otherwise carry one test's failed
    logins into the next."""
    _attempts.clear()
