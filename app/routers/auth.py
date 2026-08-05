from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.email import send_password_reset_email, send_verification_email
from app.core.google_auth import GoogleAuthError, verify_google_id_token
from app.core.logging_conf import get_logger
from app.core.pages import render_reset_password_form, render_status_page
from app.core.ratelimit import (
    check_login_allowed,
    record_login_failure,
    record_login_success,
)
from app.core.security import (
    create_access_token,
    generate_url_safe_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models import Customer, User
from app.schemas import (
    GoogleAuthRequest,
    LoginResponse,
    Message,
    PasswordResetConfirm,
    PasswordResetRequest,
)
from app.services.telegram import notify_admin_login

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = get_logger("auth")


def _issue_verification_token(user: User) -> str:
    token = generate_url_safe_token()
    user.verification_token = token
    user.verification_token_expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.EMAIL_VERIFICATION_EXPIRE_MINUTES
    )
    return token


@router.get("/verify-email", response_class=HTMLResponse)
def verify_email(token: str, db: Session = Depends(get_db)):
    """Opened directly by the user's browser from the link in the
    confirmation email, so it returns a small HTML page (that closes
    itself) rather than JSON."""
    user = db.query(User).filter(User.verification_token == token).first()
    if not user:
        return HTMLResponse(
            render_status_page(
                success=False,
                heading="Verification failed",
                message="This verification link is invalid.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if user.verification_token_expires and user.verification_token_expires < datetime.now(timezone.utc):
        return HTMLResponse(
            render_status_page(
                success=False,
                heading="Link expired",
                message="This verification link has expired. Please request a new one.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user.is_verified = True
    user.verification_token = None
    user.verification_token_expires = None
    db.commit()
    return HTMLResponse(
        render_status_page(
            success=True,
            heading="Email confirmed",
            message="Your email has been verified. You can now log in.",
        )
    )


@router.post("/resend-verification", response_model=Message)
async def resend_verification(
    payload: PasswordResetRequest,  # reused: just needs {"email": ...}
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == payload.email).first()
    # Always return the same response whether or not the email exists /
    # is already verified, to avoid leaking which emails are registered.
    if user and not user.is_verified:
        token = _issue_verification_token(user)
        db.commit()
        background_tasks.add_task(send_verification_email, user.email, token)
    return {"detail": "If that email exists and is unverified, a new confirmation link has been sent."}


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    background_tasks: BackgroundTasks,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """OAuth2 password flow. `username` = the account's email. Combined
    login for both staff and customers: tries a User match first, then
    falls back to Customer, so callers no longer need to know in advance
    which one they're authenticating as. POST /auth/customer/login still
    works too (customer-only), for callers that want to restrict to that."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # Checked before any password is hashed, so a locked-out caller can't use this
    # endpoint's bcrypt cost as a CPU-burning tool - see app/core/ratelimit.py.
    check_login_allowed(request, form_data.username)

    user = db.query(User).filter(User.email == form_data.username).first()
    if user and verify_password(form_data.password, user.hashed_password):
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")
        if not user.is_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please confirm your email address before logging in",
            )

        user.last_login = datetime.now(timezone.utc)
        db.commit()
        record_login_success(request, form_data.username)

        access_token = create_access_token(data={"sub": str(user.id), "type": "user"})

        background_tasks.add_task(
            notify_admin_login,
            user.user_name,
            user.email,
            user.role_title,
            bool(user.user_management),
        )

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "account_type": "user",
            "user": user,
        }

    customer = db.query(Customer).filter(Customer.email == form_data.username).first()
    if customer and customer.hashed_password and verify_password(form_data.password, customer.hashed_password):
        if not customer.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")
        if not customer.is_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please confirm your email address before logging in",
            )

        customer.last_login = datetime.now(timezone.utc)
        db.commit()
        record_login_success(request, form_data.username)

        access_token = create_access_token(data={"sub": str(customer.id), "type": "customer"})

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "account_type": "customer",
            "customer": customer,
        }

    # Only a genuinely wrong email/password counts towards the lockout. The
    # deactivated/unverified branches above raise 403 without recording anything -
    # those callers proved they know the password, they just can't use it yet.
    record_login_failure(request, form_data.username)
    raise unauthorized


@router.post("/google", response_model=LoginResponse)
def google_login(
    payload: GoogleAuthRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Sign in - or sign up - with a Google account. Same response shape as
    POST /auth/login, so a caller can treat the two interchangeably.

    `credential` is the ID token Google Identity Services handed the browser;
    app/core/google_auth.py verifies Google signed it, that it was issued for
    this app, and that the email on it is one Google has confirmed. Because
    that email is proven, it's what the account is matched on:

    - an existing staff `User` with that email signs in as staff (there's
      still no way to *create* a staff account this way - section 1.3 of the
      guide holds: only user_management staff can);
    - otherwise an existing `Customer` with that email signs in, including a
      record staff created by hand (`POST /customers/`) that never had a
      password - the person who owns the mailbox is who that record is for;
    - otherwise a new `Customer` is created, starting `access_permission=False`
      exactly like `POST /auth/customer/register` does.

    Either way the account comes out `is_verified=True`: Google confirming the
    address is the same proof our own emailed link asks for, so there's
    nothing left to confirm. Google accounts have no password here
    (`hashed_password` stays NULL for one created this way), so they sign in
    with this button rather than the password form.

    Deliberately a sync `def`: verification can block on fetching Google's
    signing keys, which FastAPI then runs in its threadpool.
    """
    if not settings.google_auth_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google sign-in is not available. Please sign in with your email and password.",
        )

    try:
        claims = verify_google_id_token(payload.credential)
    except GoogleAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    email = claims["email"]  # already lowercased by the verifier
    picture = claims.get("picture")
    now = datetime.now(timezone.utc)

    # Emails are matched case-insensitively: Google always reports a lowercase
    # address, but rows created through the password/admin paths keep whatever
    # casing was typed.
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if user:
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")
        if not user.is_verified:
            user.is_verified = True
            user.verification_token = None
            user.verification_token_expires = None
        if picture and not user.user_image:
            user.user_image = picture
        user.last_login = now
        db.commit()
        db.refresh(user)

        access_token = create_access_token(data={"sub": str(user.id), "type": "user"})
        background_tasks.add_task(
            notify_admin_login,
            user.user_name,
            user.email,
            user.role_title,
            bool(user.user_management),
        )
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "account_type": "user",
            "user": user,
        }

    customer = db.query(Customer).filter(func.lower(Customer.email) == email).first()
    if customer is None:
        # `name` is optional on an ID token (a Workspace account can withhold
        # the profile scope), hence the local-part fallback - customer_name is
        # NOT NULL and the column caps at 150.
        name = (claims.get("name") or "").strip() or email.split("@")[0]
        customer = Customer(
            customer_name=name[:150],
            email=email,
            customer_image=picture,
            access_permission=False,
            is_active=True,
            is_verified=True,
        )
        db.add(customer)
    else:
        if not customer.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")
        if not customer.is_verified:
            customer.is_verified = True
            customer.verification_token = None
            customer.verification_token_expires = None
        if picture and not customer.customer_image:
            customer.customer_image = picture

    customer.last_login = now
    db.commit()
    db.refresh(customer)

    access_token = create_access_token(data={"sub": str(customer.id), "type": "customer"})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "account_type": "customer",
        "customer": customer,
    }


@router.post("/forgot-password", response_model=Message)
async def forgot_password(
    payload: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == payload.email).first()
    if user:
        token = generate_url_safe_token()
        user.reset_token = token
        user.reset_token_expires = datetime.now(timezone.utc) + timedelta(
            minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES
        )
        db.commit()
        background_tasks.add_task(send_password_reset_email, user.email, token)
    return {"detail": "If that email exists, a password reset link has been sent."}


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_form(token: str, db: Session = Depends(get_db)):
    """Opened directly by the user's browser from the link in the reset
    email, so it returns an HTML form (that posts to POST /auth/reset-password)
    rather than JSON."""
    user = db.query(User).filter(User.reset_token == token).first()
    if not user:
        return HTMLResponse(
            render_status_page(
                success=False,
                heading="Reset failed",
                message="This password reset link is invalid.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if user.reset_token_expires and user.reset_token_expires < datetime.now(timezone.utc):
        return HTMLResponse(
            render_status_page(
                success=False,
                heading="Link expired",
                message="This password reset link has expired. Please request a new one.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return HTMLResponse(
        render_reset_password_form(token=token, submit_url=f"{settings.BASE_URL}/auth/reset-password")
    )


@router.post("/reset-password", response_model=Message)
def reset_password(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.reset_token == payload.token).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset token")
    if user.reset_token_expires and user.reset_token_expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token has expired")

    user.hashed_password = hash_password(payload.new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()
    return {"detail": "Password has been reset. You can now log in with your new password."}
