"""
Application logging.

Every `logger.error(...)` / `logger.critical(...)` call anywhere in the app
(including the global unhandled-exception handler in main.py) automatically
gets forwarded to the configured Telegram chat by `TelegramErrorHandler`,
in addition to going to the console and to app.log.

The Telegram send happens in a short-lived daemon thread so a slow/broken
Telegram API never blocks a request.
"""
import logging
import threading

import httpx

from app.config import settings

_TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramErrorHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            return
        try:
            message = self.format(record)
        except Exception:
            return
        threading.Thread(
            target=self._send, args=(message, record.levelname), daemon=True
        ).start()

    @staticmethod
    def _send(message: str, level: str) -> None:
        try:
            url = _TELEGRAM_URL.format(token=settings.TELEGRAM_BOT_TOKEN)
            text = f"\U0001F6A8 <b>{level}</b>\n<pre>{message[:3500]}</pre>"
            with httpx.Client(timeout=5) as client:
                client.post(
                    url,
                    json={
                        "chat_id": settings.TELEGRAM_CHAT_ID,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                )
        except Exception:
            # A broken notification must never take down the app.
            pass


_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    root_logger = logging.getLogger("app")
    root_logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)

    file_handler = logging.FileHandler("app.log")
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    telegram_handler = TelegramErrorHandler()
    telegram_handler.setLevel(logging.ERROR)
    telegram_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(name)s | %(message)s")
    )
    root_logger.addHandler(telegram_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"app.{name}")
