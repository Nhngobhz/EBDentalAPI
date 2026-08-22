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
import logging.handlers
import threading

import httpx

from app.config import settings

# app.log is written on every request path and nothing ever truncated it, so it
# grew without bound for the lifetime of the container. Five 2 MB generations is
# plenty to debug a recent incident with, and the ceiling is now fixed at 10 MB.
_LOG_FILE_MAX_BYTES = 2 * 1024 * 1024
_LOG_FILE_BACKUP_COUNT = 5

_TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramErrorHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            return
        # The record is taken apart here rather than run through self.format(): the
        # cause and the traceback are formatted very differently for a phone screen
        # (see telegram_format.render_error), and only this side still has them as
        # separate things.
        try:
            summary = record.getMessage()
            traceback_text = (
                self.formatter.formatException(record.exc_info)
                if record.exc_info and self.formatter
                else None
            )
        except Exception:
            return
        threading.Thread(
            target=self._send,
            args=(record.levelname, record.name, summary, traceback_text),
            daemon=True,
        ).start()

    @staticmethod
    def _send(level: str, logger_name: str, summary: str, traceback_text: str | None) -> None:
        try:
            # Imported here, not at module scope: this module is imported by
            # app.config's consumers before most of the app exists, and the renderer
            # has no business being on that path until something actually fails.
            from app.services.telegram_format import render_error

            url = _TELEGRAM_URL.format(token=settings.TELEGRAM_BOT_TOKEN)
            # render_error escapes everything it interpolates: this is sent with
            # parse_mode=HTML, and a log line routinely contains "<" (repr of an
            # object, a generic type, a snippet of user input). Unescaped, Telegram
            # rejected the whole message - so exactly the errors most worth seeing
            # were the ones that never arrived.
            text = render_error(level, logger_name, summary, traceback_text)
            payload = {
                "chat_id": settings.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            }
            if settings.TELEGRAM_ERROR_TOPIC_ID:
                payload["message_thread_id"] = int(settings.TELEGRAM_ERROR_TOPIC_ID)
            with httpx.Client(timeout=5) as client:
                client.post(url, json=payload)
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

    file_handler = logging.handlers.RotatingFileHandler(
        "app.log",
        maxBytes=_LOG_FILE_MAX_BYTES,
        backupCount=_LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
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
