"""
Standalone Telegram bot process: handles the /check command by calling this
API's own /health endpoint and replying with the result.

This is separate from app/services/telegram.py (which only sends outbound
notifications). Answering a command requires *receiving* updates from
Telegram, which the FastAPI app itself doesn't do - so this runs as its own
long-running process, polling Telegram via long polling (getUpdates). No
inbound webhook / public port is needed for it.

Setup:
  1. In BotFather, run /setcommands and add:
       check - Check API health
  2. Make sure TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and BASE_URL are set in
     .env (BASE_URL must be reachable from wherever this script runs).
  3. Run it as its own process, e.g.:
       python -m app.services.telegram_bot
     (keep it running - under systemd, a Docker service, pm2, etc.)

Only /check messages coming from TELEGRAM_CHAT_ID are honored; commands from
any other chat are ignored.
"""
import asyncio

import httpx

from app.config import settings
from app.core.logging_conf import get_logger, setup_logging

logger = get_logger("telegram_bot")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT = 30


async def _get_updates(client: httpx.AsyncClient, offset: int | None) -> list[dict]:
    params = {"timeout": _POLL_TIMEOUT}
    if offset is not None:
        params["offset"] = offset
    resp = await client.get(
        _TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN, method="getUpdates"),
        params=params,
        timeout=_POLL_TIMEOUT + 5,
    )
    resp.raise_for_status()
    return resp.json()["result"]


async def _reply(
    client: httpx.AsyncClient, chat_id: int, text: str, message_thread_id: int | None
) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    resp = await client.post(
        _TELEGRAM_API.format(token=settings.TELEGRAM_BOT_TOKEN, method="sendMessage"),
        json=payload,
    )
    resp.raise_for_status()


async def _handle_check(
    client: httpx.AsyncClient, chat_id: int, message_thread_id: int | None
) -> None:
    try:
        resp = await client.get(
            f"{settings.BASE_URL}/health",
            headers={"X-Telegram-Bot-Token": settings.TELEGRAM_BOT_TOKEN},
            timeout=10,
        )
        text = (
            "✅ API is healthy"
            if resp.status_code == 200
            else f"❌ API check failed (HTTP {resp.status_code})"
        )
    except Exception as exc:
        logger.warning("Health check request failed: %s", exc)
        text = f"❌ API check failed ({exc})"
    await _reply(client, chat_id, text, message_thread_id)


def _is_check_command(message: dict) -> bool:
    text = message.get("text", "")
    if not text:
        return False
    command = text.split()[0].split("@")[0]
    return command == "/check"


async def main() -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not configured - cannot start the bot.")

    setup_logging()
    logger.info("Telegram bot started, polling for /check commands...")

    offset: int | None = None
    async with httpx.AsyncClient() as client:
        while True:
            try:
                updates = await _get_updates(client, offset)
            except Exception as exc:
                logger.warning("getUpdates failed: %s", exc)
                await asyncio.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or update.get("channel_post")
                if not message or not _is_check_command(message):
                    continue

                chat_id = message["chat"]["id"]
                if settings.TELEGRAM_CHAT_ID and str(chat_id) != str(settings.TELEGRAM_CHAT_ID):
                    logger.info("Ignoring /check from unrecognized chat %s", chat_id)
                    continue

                await _handle_check(client, chat_id, message.get("message_thread_id"))


if __name__ == "__main__":
    asyncio.run(main())
