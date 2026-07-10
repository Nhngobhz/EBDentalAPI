from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.config import settings
from app.core.email import send_customer_password_reset_email, send_customer_verification_email
from app.core.logging_conf import get_logger
from app.core.pages import render_status_page
from app.core.security import (
    create_access_token,
    generate_url_safe_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models import Customer
from app.schemas import (
    CustomerLoginResponse,
    CustomerOut,
    CustomerRegister,
    Message,
    PasswordResetConfirm,
    PasswordResetRequest,
)

router = APIRouter(prefix="/auth/customer", tags=["Customer Auth"])
logger = get_logger("customer_auth")


def _issue_verification_token(customer: Customer) -> str:
    token = generate_url_safe_token()
    customer.verification_token = token
    customer.verification_token_expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.EMAIL_VERIFICATION_EXPIRE_MINUTES
    )
    return token


@router.post("/register", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
async def register(
    payload: CustomerRegister,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Public self-registration. The new account has access_permission=False
    (so product prices stay hidden) until an existing customer_management
    staff member grants it - see PUT /customers/{id}. Email confirmation is
    required before login."""
    existing = db.query(Customer).filter(Customer.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists",
        )

    customer = Customer(
        customer_name=payload.customer_name,
        email=payload.email,
        address=payload.address,
        phone_num=payload.phone_num,
        hashed_password=hash_password(payload.password),
        access_permission=False,
        is_active=True,
        is_verified=False,
    )
    token = _issue_verification_token(customer)
    db.add(customer)
    db.commit()
    db.refresh(customer)

    background_tasks.add_task(send_customer_verification_email, customer.email, token)
    return customer


@router.get("/verify-email", response_class=HTMLResponse)
def verify_email(token: str, db: Session = Depends(get_db)):
    """Opened directly by the user's browser from the link in the
    confirmation email, so it returns a small HTML page (that closes
    itself) rather than JSON."""
    customer = db.query(Customer).filter(Customer.verification_token == token).first()
    if not customer:
        return HTMLResponse(
            render_status_page(
                success=False,
                heading="Verification failed",
                message="This verification link is invalid.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if customer.verification_token_expires and customer.verification_token_expires < datetime.now(timezone.utc):
        return HTMLResponse(
            render_status_page(
                success=False,
                heading="Link expired",
                message="This verification link has expired. Please request a new one.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    customer.is_verified = True
    customer.verification_token = None
    customer.verification_token_expires = None
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
    customer = db.query(Customer).filter(Customer.email == payload.email).first()
    # Always return the same response regardless of whether the email
    # exists / has a password / is already verified, to avoid leaking
    # which emails are registered.
    if customer and customer.hashed_password and not customer.is_verified:
        token = _issue_verification_token(customer)
        db.commit()
        background_tasks.add_task(send_customer_verification_email, customer.email, token)
    return {"detail": "If that email exists and is unverified, a new confirmation link has been sent."}


@router.post("/login", response_model=CustomerLoginResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """OAuth2 password flow. `username` = the customer's email. Customer
    records created directly by staff (POST /customers/) have no password
    and can't log in through here."""
    customer = db.query(Customer).filter(Customer.email == form_data.username).first()
    if (
        not customer
        or not customer.hashed_password
        or not verify_password(form_data.password, customer.hashed_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
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

    return {"access_token": access_token, "token_type": "bearer", "customer": customer}


@router.post("/forgot-password", response_model=Message)
async def forgot_password(
    payload: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    customer = db.query(Customer).filter(Customer.email == payload.email).first()
    if customer and customer.hashed_password:
        token = generate_url_safe_token()
        customer.reset_token = token
        customer.reset_token_expires = datetime.now(timezone.utc) + timedelta(
            minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES
        )
        db.commit()
        background_tasks.add_task(send_customer_password_reset_email, customer.email, token)
    return {"detail": "If that email exists, a password reset link has been sent."}


@router.post("/reset-password", response_model=Message)
def reset_password(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.reset_token == payload.token).first()
    if not customer:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset token")
    if customer.reset_token_expires and customer.reset_token_expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token has expired")

    customer.hashed_password = hash_password(payload.new_password)
    customer.reset_token = None
    customer.reset_token_expires = None
    db.commit()
    return {"detail": "Password has been reset. You can now log in with your new password."}
