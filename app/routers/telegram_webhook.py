"""
Receives Telegram's callback_query updates for the Delivered/Cancelled buttons
attached to order alerts (see app/services/telegram.py::send_order_alert). Telegram
calls this URL directly over the internet, so there's no bearer token to check - instead
the URL itself contains a random secret (TELEGRAM_WEBHOOK_SECRET) and, as a second layer,
Telegram's own X-Telegram-Bot-Api-Secret-Token header (set when the webhook is
registered - see register_telegram_webhook() in app/main.py's lifespan) is verified too.
Anyone who doesn't present both gets a 404, same "don't even reveal this exists" pattern
as the /health endpoint's bot-token check.
"""
import html
import secrets

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging_conf import get_logger
from app.database import get_db
from app.models import Order
from app.schemas import OrderOut
from app.services.telegram import answer_callback_query, clear_order_alert_buttons
from app.services.telegram_format import render_order_alert, render_status_change

router = APIRouter(prefix="/telegram", tags=["Telegram"])
logger = get_logger("telegram_webhook")

_VALID_STATUSES = {"delivered", "cancelled"}


def _caption_after_decision(order: Order, message: dict, label: str) -> str:
    """The alert's caption once Delivered/Cancelled has been pressed.

    Rendered afresh from the order rather than reused from the message. Telegram hands
    a caption back as PLAIN text - every entity it parsed on the way in is gone - so
    the previous version, which re-escaped that and sent it as HTML, silently stripped
    all the bold, the structure and the Maps link off the alert the moment anyone
    touched a button. Re-rendering also means the caption reflects any edit made to the
    order since it was announced.

    The compact/full choice is re-run because editMessageCaption enforces the same
    1024-character cap that sendDocument does.

    The old plain-text rebuild survives as the fallback: a failure to re-render must
    still clear the buttons, or the decision stays clickable forever."""
    try:
        alert = render_order_alert(OrderOut.model_validate(order))
        return render_status_change(alert.caption(), label)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not re-render the caption for order %s: %s", order.id, exc)
        return render_status_change(html.escape(message.get("caption") or "", quote=False), label)


@router.post("/webhook/{secret}")
async def telegram_webhook(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    # compare_digest, not ==: this endpoint is reachable by anyone on the internet,
    # and a short-circuiting comparison leaks the secret one character at a time
    # to whoever is willing to measure the response.
    if not settings.TELEGRAM_WEBHOOK_SECRET or not secrets.compare_digest(
        secret, settings.TELEGRAM_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not secrets.compare_digest(
        x_telegram_bot_api_secret_token or "", settings.TELEGRAM_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    update = await request.json()
    callback_query = update.get("callback_query")
    if not callback_query:
        # Telegram also posts other update types (plain messages, etc.) to the same
        # webhook - nothing else is handled, just acknowledge so it isn't retried.
        return {"ok": True}

    data = callback_query.get("data") or ""
    parts = data.split(":")
    # The order id is validated as a number here rather than at int() below, so a
    # malformed callback is answered politely instead of raising into a 500 (which
    # would also page the error topic in Telegram for what is just noise).
    if (
        len(parts) != 3
        or parts[0] != "order"
        or not parts[1].isdigit()
        or parts[2] not in _VALID_STATUSES
    ):
        await answer_callback_query(callback_query["id"], "Unrecognized action.")
        return {"ok": True}

    _, order_id_str, new_status = parts
    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")

    db: Session = next(get_db())
    try:
        order = db.query(Order).filter(Order.id == int(order_id_str)).first()
        if not order:
            await answer_callback_query(callback_query["id"], "Order not found.")
            return {"ok": True}

        order.status = new_status
        db.commit()

        label = "Delivered ✅" if new_status == "delivered" else "Cancelled ❌"
        await answer_callback_query(callback_query["id"], f"Order marked {label}.")
        if chat_id is not None and message_id is not None:
            await clear_order_alert_buttons(
                chat_id, message_id, _caption_after_decision(order, message, label)
            )
    finally:
        db.close()

    return {"ok": True}
