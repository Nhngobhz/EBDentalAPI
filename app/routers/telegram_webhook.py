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
from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging_conf import get_logger
from app.database import get_db
from app.models import Order
from app.services.telegram import answer_callback_query, clear_order_alert_buttons

router = APIRouter(prefix="/telegram", tags=["Telegram"])
logger = get_logger("telegram_webhook")

_VALID_STATUSES = {"delivered", "cancelled"}


@router.post("/webhook/{secret}")
async def telegram_webhook(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not settings.TELEGRAM_WEBHOOK_SECRET or secret != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    update = await request.json()
    callback_query = update.get("callback_query")
    if not callback_query:
        # Telegram also posts other update types (plain messages, etc.) to the same
        # webhook - nothing else is handled, just acknowledge so it isn't retried.
        return {"ok": True}

    data = callback_query.get("data") or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "order" or parts[2] not in _VALID_STATUSES:
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
            original_caption = message.get("caption") or ""
            await clear_order_alert_buttons(chat_id, message_id, f"{original_caption}\n\nStatus: {label}")
    finally:
        db.close()

    return {"ok": True}
