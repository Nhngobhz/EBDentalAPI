"""
Outbound email: account verification + password reset.

If MAIL_USERNAME / MAIL_PASSWORD are not set, we run in "dry run" mode:
the email content is logged instead of sent. This lets the whole
registration/verification flow be exercised locally without a real SMTP
account, and it means a missing/broken SMTP config degrades gracefully
instead of crashing registration.
"""
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger("email")

_ACCENT_START = "#095799"
_ACCENT_END = "#45d3f7"
_ACCENT_GRADIENT = f"linear-gradient(135deg, {_ACCENT_START}, {_ACCENT_END})"

_conf = ConnectionConfig(
    MAIL_USERNAME=settings.MAIL_USERNAME or "no-reply@example.com",
    MAIL_PASSWORD=settings.MAIL_PASSWORD or "placeholder",
    MAIL_FROM=settings.MAIL_FROM,
    MAIL_FROM_NAME=settings.MAIL_FROM_NAME,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_STARTTLS=settings.MAIL_STARTTLS,
    MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
    USE_CREDENTIALS=bool(settings.MAIL_USERNAME),
    VALIDATE_CERTS=True,
)

_fast_mail = FastMail(_conf)


async def send_email(subject: str, recipients: list[str], body: str) -> None:
    if not settings.MAIL_USERNAME or not settings.MAIL_PASSWORD:
        logger.warning(
            "SMTP not configured - DRY RUN. Would send '%s' to %s", subject, recipients
        )
        logger.info("Dry-run email body:\n%s", body)
        return
    message = MessageSchema(
        subject=subject, recipients=recipients, body=body, subtype=MessageType.html
    )
    try:
        await _fast_mail.send_message(message)
    except Exception as exc:  # never let a mail failure break the request
        logger.error("Failed to send email to %s: %s", recipients, exc)


def _button_email(heading: str, intro: str, button_text: str, link: str, footnote: str) -> str:
    return f"""
    <div style="background-color:#f4f5f7;padding:32px 16px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
      <div style="max-width:480px;margin:0 auto;background-color:#ffffff;border-radius:12px;
                  overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
        <div style="background-color:{_ACCENT_START};background:{_ACCENT_GRADIENT};padding:28px 24px;text-align:center;">
          <span style="color:#ffffff;font-size:20px;font-weight:600;letter-spacing:0.2px;">
            {settings.APP_NAME}
          </span>
        </div>
        <div style="padding:32px 28px;text-align:center;">
          <h1 style="margin:0 0 12px;font-size:22px;color:#1a1a1a;">{heading}</h1>
          <p style="margin:0 0 28px;font-size:15px;line-height:1.5;color:#4a4a4a;">{intro}</p>
          <a href="{link}"
             style="display:inline-block;padding:14px 36px;background-color:{_ACCENT_START};background:{_ACCENT_GRADIENT};color:#ffffff;
                    text-decoration:none;font-size:16px;font-weight:600;border-radius:8px;">
            {button_text}
          </a>
          <p style="margin:28px 0 0;font-size:13px;line-height:1.5;color:#8a8a8a;">{footnote}</p>
        </div>
      </div>
    </div>
    """


async def send_verification_email(to_email: str, token: str) -> None:
    link = f"{settings.BASE_URL}/auth/verify-email?token={token}"
    body = _button_email(
        heading=f"Welcome to {settings.APP_NAME}!",
        intro="Please confirm your email address to activate your account.",
        button_text="Confirm my email",
        link=link,
        footnote=(
            f"This link expires in {settings.EMAIL_VERIFICATION_EXPIRE_MINUTES} minutes. "
            "If you didn't create this account, you can ignore this email."
        ),
    )
    await send_email("Confirm your email address", [to_email], body)


async def send_password_reset_email(to_email: str, token: str) -> None:
    link = f"{settings.BASE_URL}/auth/reset-password?token={token}"
    body = _button_email(
        heading="Reset your password",
        intro=f"We received a request to reset your {settings.APP_NAME} password.",
        button_text="Reset my password",
        link=link,
        footnote=(
            f"This link expires in {settings.PASSWORD_RESET_EXPIRE_MINUTES} minutes. "
            "If you did not request this, you can safely ignore this email."
        ),
    )
    await send_email("Reset your password", [to_email], body)


async def send_customer_verification_email(to_email: str, token: str) -> None:
    link = f"{settings.BASE_URL}/auth/customer/verify-email?token={token}"
    body = _button_email(
        heading=f"Welcome to {settings.APP_NAME}!",
        intro="Please confirm your email address to activate your account.",
        button_text="Confirm my email",
        link=link,
        footnote=(
            f"This link expires in {settings.EMAIL_VERIFICATION_EXPIRE_MINUTES} minutes. "
            "If you didn't create this account, you can ignore this email."
        ),
    )
    await send_email("Confirm your email address", [to_email], body)


async def send_customer_password_reset_email(to_email: str, token: str) -> None:
    link = f"{settings.BASE_URL}/auth/customer/reset-password?token={token}"
    body = _button_email(
        heading="Reset your password",
        intro=f"We received a request to reset your {settings.APP_NAME} password.",
        button_text="Reset my password",
        link=link,
        footnote=(
            f"This link expires in {settings.PASSWORD_RESET_EXPIRE_MINUTES} minutes. "
            "If you did not request this, you can safely ignore this email."
        ),
    )
    await send_email("Reset your password", [to_email], body)
