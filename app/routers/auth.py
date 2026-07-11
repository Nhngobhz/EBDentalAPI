from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.config import settings
from app.core.email import send_password_reset_email, send_verification_email
from app.core.logging_conf import get_logger
from app.core.pages import render_reset_password_form, render_status_page
from app.core.security import (
    create_access_token,
    generate_url_safe_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models import Customer, User
from app.schemas import (
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

        access_token = create_access_token(data={"sub": str(customer.id), "type": "customer"})

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "account_type": "customer",
            "customer": customer,
        }

    raise unauthorized


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
