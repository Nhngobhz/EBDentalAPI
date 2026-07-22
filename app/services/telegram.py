"""
Telegram bot notifications.

Things get pushed to the configured Telegram chat:
  1. Every successful staff (User) login - see notify_admin_login().
     "Admin-level" logins (user_management=True) are flagged distinctly
     from regular staff logins so admins stand out in the chat.
  2. Unhandled application errors - see notify_error() and, for anything
     logged with logger.error()/logger.critical() anywhere in the app,
     app/core/logging_conf.py's TelegramErrorHandler.
  3. Every new order - see deliver_order_alert(), called from
     routers/orders.py::create_order. Includes the quotation PDF and inline
     Delivered/Cancelled buttons; button presses land on the webhook in
     routers/telegram_webhook.py.

If TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not configured, these
functions are no-ops (logged at debug level) so the app works fully
without a Telegram bot connected.

To set this up: message @BotFather on Telegram to create a bot and get a
token, then message your bot (or add it to a group) and use
https://api.telegram.org/bot<token>/getUpdates to find the chat_id.

**Quotation PDF source (added 2026-07-22)**: the document customers actually see is
built client-side (EB Web Project's main.js, QuoteCart.buildPrintTemplate/exportPDF via
html2canvas) - server-side app/services/invoice_pdf.py is only ever an approximation of
that (fpdf2 can't reproduce an arbitrary HTML/CSS layout exactly). Rather than keep
chasing pixel-parity, deliver_order_alert() briefly waits for the browser to hand over
its real rendered PDF (POST /orders/{id}/quotation-pdf, called right after
QuoteCart.confirmPurchase() builds it) and uses that if it arrives in time, falling
back to the fpdf2 approximation only if it doesn't (browser closed/crashed/offline) -
exactly one Telegram alert always goes out, never zero and never two.
"""
import asyncio
import json
from typing import TYPE_CHECKING

from app.config import settings
from app.core.logging_conf import get_logger

if TYPE_CHECKING:
    from app.schemas import OrderOut

logger = get_logger("telegram")

# How long deliver_order_alert() waits for the browser's real PDF upload before giving
# up and falling back to the server-rendered approximation. Long enough for a normal
# html2canvas render + upload round-trip, short enough that staff never wait long for
# their notification.
_QUOTATION_PDF_WAIT_SECONDS = 20.0

# Single-process in-memory handoff between deliver_order_alert() (waiting) and
# routers/orders.py's POST /{id}/quotation-pdf (resolving) - store-api runs as one
# Uvicorn worker (see entrypoint.sh), so this doesn't need to survive a restart or be
# visible across processes; it only needs to live for the few seconds between an order
# being created and its alert being sent.
_pending_quotation_pdfs: dict[int, "asyncio.Future[bytes]"] = {}


def register_pending_quotation_pdf(order_id: int) -> "asyncio.Future[bytes]":
    future = asyncio.get_event_loop().create_future()
    _pending_quotation_pdfs[order_id] = future
    return future


def resolve_pending_quotation_pdf(order_id: int, pdf_bytes: bytes) -> None:
    """Called from the upload endpoint. A no-op if nothing is waiting (the alert
    already timed out and fell back, or already fired for some other reason) - the
    endpoint always reports success to the browser either way, since the upload itself
    genuinely succeeded regardless of whether it made it into the alert."""
    future = _pending_quotation_pdfs.get(order_id)
    if future is not None and not future.done():
        future.set_result(pdf_bytes)


async def deliver_order_alert(order: "OrderOut") -> None:
    future = register_pending_quotation_pdf(order.id)
    try:
        pdf_bytes = await asyncio.wait_for(future, timeout=_QUOTATION_PDF_WAIT_SECONDS)
    except asyncio.TimeoutError:
        pdf_bytes = None
        logger.info(
            "No client quotation PDF uploaded for order %s within %.0fs - "
            "falling back to the server-rendered PDF.", order.id, _QUOTATION_PDF_WAIT_SECONDS,
        )
    finally:
        _pending_quotation_pdfs.pop(order.id, None)
    await send_order_alert(order, pdf_bytes=pdf_bytes)


_API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _api_url(method: str) -> str:
    return _API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method=method)


async def send_telegram_message(text: str, topic_id: str | None = None) -> None:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured - skipping notification.")
        return

    import httpx  # local import keeps startup fast when Telegram is unused

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
            resp = await client.post(_api_url("sendMessage"), json=payload)
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


async def send_order_alert(order: "OrderOut", pdf_bytes: bytes | None = None) -> None:
    """Posts a new order to Telegram with its quotation PDF attached and
    Delivered/Cancelled buttons. Button presses come back on
    routers/telegram_webhook.py, which updates order.status directly and
    edits this message to remove the buttons - see
    edit_order_alert_after_decision() below.

    `pdf_bytes`, when given (see deliver_order_alert), is the real client-rendered PDF
    the customer downloaded - otherwise the server's fpdf2 approximation
    (build_invoice_pdf) is generated and used instead. Exposed as a direct parameter
    (rather than always calling deliver_order_alert) so tests/other callers can still
    send an alert synchronously without the wait."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.debug("Telegram not configured - skipping order alert.")
        return

    import httpx

    caption = (
        f"\U0001F6CD <b>New order</b>\n"
        f"Order No: {order.order_number} (Code: {order.quote_code})\n"
        f"Clinic: {order.clinic_name}\n"
        f"Salesperson: {order.salesperson or '-'}\n"
        f"Grand Total: $ {order.grand_total:.2f}"
    )
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Delivered", "callback_data": f"order:{order.id}:delivered"},
            {"text": "❌ Cancelled", "callback_data": f"order:{order.id}:cancelled"},
        ]]
    }
    data = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(reply_markup),
    }
    if settings.TELEGRAM_ORDER_TOPIC_ID:
        data["message_thread_id"] = settings.TELEGRAM_ORDER_TOPIC_ID

    try:
        if pdf_bytes is None:
            from app.services.invoice_pdf import build_invoice_pdf

            pdf_bytes = build_invoice_pdf(order)
        files = {"document": (f"EB-Dental-Quotation-{order.quote_code}.pdf", pdf_bytes, "application/pdf")}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_api_url("sendDocument"), data=data, files=files)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to send Telegram order alert: %s", exc)


async def answer_callback_query(callback_query_id: str, text: str) -> None:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                _api_url("answerCallbackQuery"),
                json={"callback_query_id": callback_query_id, "text": text},
            )
    except Exception as exc:
        logger.warning("Failed to answer Telegram callback query: %s", exc)


async def register_webhook() -> None:
    """Called once from app/main.py's lifespan on startup. Points Telegram at
    POST /telegram/webhook/<TELEGRAM_WEBHOOK_SECRET> so button presses on order alerts
    reach telegram_webhook.py. Skipped (logged, non-fatal) unless the bot token, webhook
    secret, and a real public BASE_URL are all configured - same "optional integration"
    pattern as the rest of this module. Local dev needs a tunnel (ngrok/cloudflared)
    pointed at this app, with BASE_URL set to that tunnel's URL, for Telegram to
    actually be able to reach it."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_WEBHOOK_SECRET:
        logger.debug("Telegram webhook not configured - skipping setWebhook.")
        return
    if settings.BASE_URL.startswith("http://localhost") or settings.BASE_URL.startswith("http://127.0.0.1"):
        logger.info("BASE_URL is localhost - skipping Telegram setWebhook (use a tunnel for local testing).")
        return

    import httpx

    webhook_url = f"{settings.BASE_URL.rstrip('/')}/telegram/webhook/{settings.TELEGRAM_WEBHOOK_SECRET}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                _api_url("setWebhook"),
                json={
                    "url": webhook_url,
                    "secret_token": settings.TELEGRAM_WEBHOOK_SECRET,
                    "allowed_updates": ["callback_query"],
                },
            )
            resp.raise_for_status()
            body = resp.json()
            if not body.get("ok"):
                logger.warning("Telegram setWebhook responded with an error: %s", body)
            else:
                logger.info("Telegram webhook registered at %s", webhook_url)
    except Exception as exc:
        logger.warning("Failed to register Telegram webhook: %s", exc)


async def clear_order_alert_buttons(chat_id: int, message_id: int, new_caption: str) -> None:
    """Removes the Delivered/Cancelled buttons and updates the caption after one of
    them has been pressed, so the decision can't be made twice."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                _api_url("editMessageCaption"),
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "caption": new_caption,
                    "parse_mode": "HTML",
                },
            )
            await client.post(
                _api_url("editMessageReplyMarkup"),
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": json.dumps({"inline_keyboard": []}),
                },
            )
    except Exception as exc:
        logger.warning("Failed to clear Telegram order alert buttons: %s", exc)
