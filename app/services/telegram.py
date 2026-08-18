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
     routers/telegram_webhook.py. If the PDF upload itself fails, the alert still
     goes out as text - see _send_order_alert_without_pdf().

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
import html
import json
from typing import TYPE_CHECKING

from app.config import settings
from app.core.logging_conf import get_logger

if TYPE_CHECKING:
    from decimal import Decimal

    from app.schemas import OrderOut

logger = get_logger("telegram")

# How long deliver_order_alert() waits for the browser's real PDF upload before giving
# up and falling back to the server-rendered approximation. Long enough for a normal
# html2canvas render + upload round-trip, short enough that staff never wait long for
# their notification.
_QUOTATION_PDF_WAIT_SECONDS = 20.0

# The client quotation PDF is a full-page html2canvas PNG snapshot embedded by jsPDF
# (main.js exportPDF) - routinely several MB, and up to MAX_PDF_SIZE_MB. A flat 15s
# budget for the whole sendDocument round-trip was not enough for that on a normal
# connection, so uploads died partway and the alert was lost. Split out per phase:
# connecting stays fast-fail, but writing the body and waiting for Telegram to finish
# ingesting a multi-MB document get real headroom. Kept as plain numbers rather than an
# httpx.Timeout so this module still doesn't import httpx until something is actually
# sent (see the local imports below).
_UPLOAD_TIMEOUT_KWARGS = {"connect": 10.0, "write": 120.0, "read": 120.0, "pool": 10.0}

# Transport-level failures reaching api.telegram.org (connection reset mid-upload, TLS
# hiccup, timeout) are transient, and the previous version dropped the whole alert on the
# first one - silently, because it logged str(exc), which for those exception types is
# empty. Retried a couple of times, with a text-only alert as the final fallback.
_UPLOAD_ATTEMPTS = 3
_UPLOAD_RETRY_DELAY_SECONDS = 2.0

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


def _esc(value: object) -> str:
    """Escape a value before it goes into a parse_mode=HTML message.

    Every caption/message below is sent with parse_mode="HTML", and several
    interpolate free text the *customer* typed (clinic_name, address) or that a
    staff member chose (user_name, role_title). Telegram rejects a message whose
    entities don't parse - so a clinic literally named "Smith <Dental>" didn't
    produce a mangled alert, it produced NO alert (and the text-only fallback,
    built from the same caption, failed identically). Escaping keeps the alert
    deliverable whatever anyone types, and closes the matching injection of
    arbitrary markup into the staff chat."""
    return html.escape(str(value if value is not None else ""), quote=False)


def _describe(exc: BaseException) -> str:
    """Renders an exception for a log line.

    Plain `%s` is not enough here: the network-layer exceptions this module actually
    hits (httpx.ReadError/WriteError/ConnectTimeout wrapping an httpcore or anyio
    error, asyncio.CancelledError) very often have an EMPTY str(), which logged as
    "Failed to send Telegram order alert: " with nothing after the colon and left
    outages undiagnosable. The class name always carries the useful signal."""
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _describe_response(resp) -> str:
    """Telegram puts the actionable reason in the JSON body's `description`
    ("message thread not found", "file is too big", "bot was kicked from the
    supergroup chat", ...). raise_for_status() throws that body away, so failures are
    read out of the response explicitly instead."""
    try:
        description = resp.json().get("description") or resp.text[:300]
    except Exception:
        description = resp.text[:300]
    return f"HTTP {resp.status_code} - {description}"


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
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_api_url("sendMessage"), json=payload)
            if resp.status_code >= 400:
                logger.warning("Telegram rejected a notification: %s", _describe_response(resp))
    except Exception as exc:
        logger.warning("Failed to send Telegram notification: %s", _describe(exc))


async def notify_admin_login(
    user_name: str, email: str, role_title: str, is_admin_level: bool
) -> None:
    icon = "\U0001F6E1" if is_admin_level else "\U0001F464"
    text = (
        f"{icon} <b>Staff login</b>\n"
        f"User: {_esc(user_name)} ({_esc(email)})\n"
        f"Role: {_esc(role_title)}\n"
        f"Admin-level (user_management): {'Yes' if is_admin_level else 'No'}"
    )
    await send_telegram_message(text, topic_id=settings.TELEGRAM_LOGIN_TOPIC_ID)


async def notify_error(error_type: str, message: str, path: str | None = None) -> None:
    text = (
        f"\U0001F6A8 <b>Application error</b>\n"
        f"Type: {_esc(error_type)}\n"
        f"Message: {_esc(message)}\n" + (f"Path: {_esc(path)}" if path else "")
    )
    await send_telegram_message(text, topic_id=settings.TELEGRAM_ERROR_TOPIC_ID)


def _order_alert_caption(order: "OrderOut") -> tuple[str, str]:
    """Returns (caption, document_word) for an order's Telegram alert. Three shapes:
    a PAID row (from the payment-status check or a staff Mark-as-Paid, carrying the
    invoice), an unpaid QUOTE (staff cart, or a customer who chose Cash - explicitly
    flagged so staff never mistake it for a paid sale), and the plain new-order shape
    kept as a fallback for anything else.

    Paid is checked FIRST, and on payment_status alone: a quote whose payment staff
    recorded at the counter is a completed sale, and announcing it as "no payment has
    been made" because order_type still says "quote" would be exactly backwards."""
    if order.payment_status == "paid":
        via = " via KHQR" if order.payment_method == "khqr" else ""
        label = "QUOTE" if order.order_type == "quote" else "Order"
        caption = (
            f"✅ <b>{label} PAID{via}</b>\n"
            f"No: {_esc(order.order_number)} (Code: {_esc(order.quote_code)})\n"
            f"Clinic: {_esc(order.clinic_name)}\n"
            f"Salesperson: {_esc(order.salesperson or '-')}\n"
            f"Amount received: $ {order.grand_total:.2f}"
        )
        # Taken from the PDF builder rather than spelled again here: the filename staff
        # see in Telegram has to match the title inside the document they open. That
        # word became "Invoice" (from "Receipt") on 2026-08-17. Imported inside the
        # function like build_invoice_pdf below - invoice_pdf pulls in fpdf2 and both
        # bundled fonts, which nothing needs until an alert actually carries a document.
        from app.services.invoice_pdf import document_title

        return caption, document_title(order)
    if order.order_type == "quote":
        payment_line = (
            "Payment: Cash (to be collected)\n" if order.payment_method == "cash" else ""
        )
        caption = (
            f"\U0001F4C4 <b>New QUOTE</b>\n"
            f"Quote No: {_esc(order.order_number)} (Code: {_esc(order.quote_code)})\n"
            f"Clinic: {_esc(order.clinic_name)}\n"
            f"Salesperson: {_esc(order.salesperson or '-')}\n"
            f"{payment_line}"
            f"Grand Total: $ {order.grand_total:.2f}\n"
            f"ℹ️ This is a quotation - no payment has been made."
        )
        return caption, "Quotation"
    caption = (
        f"\U0001F6CD <b>New order</b>\n"
        f"Order No: {_esc(order.order_number)} (Code: {_esc(order.quote_code)})\n"
        f"Clinic: {_esc(order.clinic_name)}\n"
        f"Salesperson: {_esc(order.salesperson or '-')}\n"
        f"Grand Total: $ {order.grand_total:.2f}"
    )
    return caption, "Quotation"


async def send_khqr_pending_alert_for_checkout(
    reference: str, grand_total: "Decimal", customer_name: str
) -> None:
    """Text-only heads-up that a customer is at the QR - no PDF, no Delivered/Cancelled
    buttons, and deliberately no order number, because at this point **there is no
    order**: a customer KHQR purchase writes nothing until the payment is confirmed (see
    routers/orders.py::create_checkout). `reference` is the QR's bill number, which is
    what the payment will show up as at the bank if staff need to reconcile it by hand.

    The full PDF-carrying alert with buttons goes out via deliver_order_alert() at the
    moment the order is actually created, i.e. once the money has arrived."""
    text = (
        f"\U0001F551 <b>Customer is paying by KHQR</b>\n"
        f"Ref: {_esc(reference)}\n"
        f"Customer: {_esc(customer_name)}\n"
        f"Amount due: $ {grand_total:.2f}\n"
        f"No order exists yet - one is created, with its invoice, once the payment lands."
    )
    await send_telegram_message(text, topic_id=settings.TELEGRAM_ORDER_TOPIC_ID)


async def send_order_alert(order: "OrderOut", pdf_bytes: bytes | None = None) -> None:
    """Posts a new quote/paid order to Telegram with its PDF attached and
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

    caption, doc_word = _order_alert_caption(order)
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
        from_client = pdf_bytes is not None
        if pdf_bytes is None:
            from app.services.invoice_pdf import build_invoice_pdf

            pdf_bytes = build_invoice_pdf(order)
        logger.info(
            "Sending Telegram order alert for order %s (%s PDF, %.1f MB).",
            order.id, "client" if from_client else "server-rendered", len(pdf_bytes) / 1024 / 1024,
        )
        files = {"document": (f"EB-Dental-{doc_word}-{order.quote_code}.pdf", pdf_bytes, "application/pdf")}
        async with httpx.AsyncClient(timeout=httpx.Timeout(**_UPLOAD_TIMEOUT_KWARGS)) as client:
            for attempt in range(1, _UPLOAD_ATTEMPTS + 1):
                try:
                    resp = await client.post(_api_url("sendDocument"), data=data, files=files)
                except Exception as exc:
                    # Transport-level failure (connection reset mid-upload, timeout, DNS
                    # blip). Retried, because these are transient by nature and this is
                    # the one notification staff actually act on - the previous version
                    # gave up on the first one and the order went unnoticed.
                    logger.warning(
                        "Telegram order alert for order %s failed on attempt %s/%s: %s",
                        order.id, attempt, _UPLOAD_ATTEMPTS, _describe(exc),
                    )
                    if attempt == _UPLOAD_ATTEMPTS:
                        raise
                    await asyncio.sleep(_UPLOAD_RETRY_DELAY_SECONDS * attempt)
                    continue
                if resp.status_code >= 400:
                    # Telegram answered and said no. Not retried: a rejection is about the
                    # request itself (bad topic, file too big, bot removed from the chat)
                    # and resending an identical one just gets rejected identically.
                    logger.warning(
                        "Telegram rejected the order alert for order %s: %s",
                        order.id, _describe_response(resp),
                    )
                    await _send_order_alert_without_pdf(order, caption, reply_markup)
                    return
                logger.info("Telegram order alert sent for order %s.", order.id)
                return
    except Exception as exc:
        logger.warning(
            "Giving up on the Telegram order alert with PDF for order %s: %s",
            order.id, _describe(exc),
        )
        await _send_order_alert_without_pdf(order, caption, reply_markup)


async def _send_order_alert_without_pdf(order: "OrderOut", caption: str, reply_markup: dict) -> None:
    """Last-resort alert when sendDocument fails (upload timed out, connection dropped,
    Telegram rejected the file...). A text message is small enough to get through almost
    anything the document upload can't, so a placed order is never silently invisible -
    staff still get the order number, clinic and total, plus the same Delivered/Cancelled
    buttons, and can pull the PDF from the admin Orders page. Deliberately not retried
    beyond this: one alert, degraded, beats none."""
    import httpx

    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": caption + "\n\n⚠️ <i>The quotation PDF could not be attached - open the order in the admin Orders page to print it.</i>",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": json.dumps(reply_markup),
    }
    if settings.TELEGRAM_ORDER_TOPIC_ID:
        payload["message_thread_id"] = int(settings.TELEGRAM_ORDER_TOPIC_ID)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_api_url("sendMessage"), json=payload)
            if resp.status_code >= 400:
                logger.error(
                    "Text-only fallback alert for order %s also failed: %s",
                    order.id, _describe_response(resp),
                )
            else:
                logger.info("Sent text-only fallback alert for order %s.", order.id)
    except Exception as exc:
        logger.error(
            "Text-only fallback alert for order %s also failed: %s", order.id, _describe(exc)
        )


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
