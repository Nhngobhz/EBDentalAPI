"""
Telegram bot notifications.

Two things get pushed to the configured Telegram chat:
  1. Every successful staff (User) login - see notify_admin_login().
     "Admin-level" logins (user_management=True) are flagged distinctly
     from regular staff logins so admins stand out in the chat.
  2. Unhandled application errors - see notify_error() and, for anything
     logged with logger.error()/logger.critical() anywhere in the app,
     app/core/logging_conf.py's TelegramErrorHandler.

If TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not configured, these
functions are no-ops (logged at debug level) so the app works fully
without a Telegram bot connected.

To set this up: message @BotFather on Telegram to create a bot and get a
token, then message your bot (or add it to a group) and use
https://api.telegram.org/bot<token>/getUpdates to find the chat_id.
"""
from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger("telegram")

_TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram_message(text: str, topic_id: str | None = None) -> None:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured - skipping notification.")
        return

    import httpx  # local import keeps startup fast when Telegram is unused

    url = _TELEGRAM_URL.format(token=settings.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if topic_id:
        payload["message_thread_id"] = int(topic_id)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to send Telegram notification: %s", exc)


async def notify_admin_login(
    user_name: str, email: str, role_title: str, is_admin_level: bool
) -> None:
    icon = "\U0001F6E1" if is_admin_level else "\U0001F464"
    text = (
        f"{icon} <b>Staff login</b>\n"
        f"User: {user_name} ({email})\n"
        f"Role: {role_title}\n"
        f"Admin-level (user_management): {'Yes' if is_admin_level else 'No'}"
    )
    await send_telegram_message(text, topic_id=settings.TELEGRAM_LOGIN_TOPIC_ID)


async def notify_error(error_type: str, message: str, path: str | None = None) -> None:
    text = (
        f"\U0001F6A8 <b>Application error</b>\n"
        f"Type: {error_type}\n"
        f"Message: {message}\n" + (f"Path: {path}" if path else "")
    )
    await send_telegram_message(text, topic_id=settings.TELEGRAM_ERROR_TOPIC_ID)
