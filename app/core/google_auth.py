"""
Google Sign-In: verifying the ID token the browser hands us.

The flow deliberately keeps this server out of the OAuth dance entirely.
Google Identity Services renders its own button in the storefront page
(see the Flask app's templates/partials/google_signin.html); when the
visitor picks an account, Google hands that page a **signed ID token** (a
JWT). The page POSTs that token to `POST /auth/google`, and this module is
what decides whether to believe it. No password, no authorization code and
no client *secret* ever passes through here - only the public client id
(`GOOGLE_CLIENT_ID`), which is what the token's `aud` claim must match.

Verification is done locally against Google's published signing keys
rather than by calling Google's `tokeninfo` endpoint per sign-in: same
guarantee, no extra round trip on every login. PyJWT's PyJWKClient caches
the fetched key set (`lifespan` below), so in practice Google's certs are
fetched once an hour, not once a login.
"""
from typing import Any, Optional

import jwt
from jwt import PyJWKClient

from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger("google_auth")

# Google's public signing keys (JWKS) for ID tokens.
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
# Google mints ID tokens with either spelling of the issuer; both are valid,
# so the check is a membership test rather than PyJWT's single-value
# `issuer=` argument.
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

_jwk_client: Optional[PyJWKClient] = None


class GoogleAuthError(Exception):
    """The credential could not be verified. The message is safe to show a
    visitor - it never contains the raw token or a crypto-level detail (those
    go to the log instead)."""


def _client() -> PyJWKClient:
    """One process-wide client so the JWK set is cached across sign-ins."""
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(GOOGLE_JWKS_URI, cache_keys=True, lifespan=3600)
    return _jwk_client


def verify_google_id_token(credential: str) -> dict[str, Any]:
    """Return the token's claims, or raise GoogleAuthError.

    Checks, in order: Google actually signed it (signature against the JWKS),
    it was issued for *this* app (`aud` == GOOGLE_CLIENT_ID), it hasn't
    expired (PyJWT does this), Google itself issued it (`iss`), and the
    account's email is one Google has confirmed (`email_verified`) - that
    last one is what makes matching an existing account by email address
    safe.

    Note this is a blocking call the first time each hour (it fetches
    Google's certs), so callers should be sync `def` routes - FastAPI then
    runs them in its threadpool instead of stalling the event loop.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise GoogleAuthError("Google sign-in is not configured on this server")

    try:
        signing_key = _client().get_signing_key_from_jwt(credential)
    except Exception as exc:  # PyJWKClientError, malformed header, network failure
        logger.warning("Could not resolve the Google signing key: %s", exc)
        raise GoogleAuthError("Could not verify your Google account. Please try again.") from exc

    try:
        claims = jwt.decode(
            credential,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.GOOGLE_CLIENT_ID,
        )
    except jwt.PyJWTError as exc:
        # Expired/replayed/wrong-audience tokens all land here. Logged (not
        # returned) because the reason is only useful to whoever configured
        # GOOGLE_CLIENT_ID, and telling a caller which check failed helps
        # nobody but an attacker.
        logger.warning("Rejected a Google ID token: %s", exc)
        raise GoogleAuthError("Your Google sign-in could not be verified. Please try again.") from exc

    if claims.get("iss") not in GOOGLE_ISSUERS:
        logger.warning("Rejected a Google ID token with issuer %r", claims.get("iss"))
        raise GoogleAuthError("Your Google sign-in could not be verified. Please try again.")

    email = (claims.get("email") or "").strip().lower()
    if not email or not claims.get("email_verified"):
        raise GoogleAuthError(
            "Your Google account doesn't have a confirmed email address, so it can't be used to sign in."
        )
    # Normalized here so every caller matches accounts against the same form.
    claims["email"] = email
    return claims
