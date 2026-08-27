import asyncio
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.logging_conf import get_logger, setup_logging
from app.database import Base, engine
from app.routers import (
    activity,
    auth,
    brands,
    categories,
    customer_auth,
    customers,
    hero_slides,
    manuals,
    orders,
    products,
    promotions,
    qr_codes,
    reports,
    sets,
    settings as settings_router,
    telegram_webhook,
    users,
)
from app.services.checkout_sweep import run_checkout_sweep
from app.services.telegram import register_webhook

setup_logging()
logger = get_logger("main")

os.makedirs(settings.UPLOAD_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AUTO_CREATE_TABLES:
        logger.info("AUTO_CREATE_TABLES=true - running Base.metadata.create_all()")
        Base.metadata.create_all(bind=engine)
    logger.info("%s started (environment=%s)", settings.APP_NAME, settings.ENVIRONMENT)
    await register_webhook()

    # A pay-by-QR purchase only becomes an order once the payment is confirmed, and the
    # customer's browser is normally what notices. This is the server-side backstop for
    # when it doesn't (tab closed mid-payment) - without it, a real payment could arrive
    # and never produce an order. See services/checkout_sweep.py.
    sweep = asyncio.create_task(run_checkout_sweep())
    try:
        yield
    finally:
        sweep.cancel()


app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Store management API: role-based staff accounts, customers, "
        "brands, products, manuals and promotions."
    ),
    version="1.0.0",
    lifespan=lifespan,
    # Swagger UI / ReDoc / the raw OpenAPI schema are intentionally disabled -
    # this API isn't meant to expose interactive/self-describing docs
    # publicly. See AI_AGENT_GUIDE.md for a hand-written reference instead.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# allow_origins=["*"] together with allow_credentials=True is the classic CORS
# footgun: Starlette then echoes the *caller's own* Origin back with
# Access-Control-Allow-Credentials, so any website on the internet can make
# credentialed cross-origin calls here. Nothing in this system needs that -
# the browser talks to the Flask app, which holds the bearer token server-side
# and calls this API server-to-server (no CORS involved at all). So credentials
# are only offered once CORS_ORIGINS actually names the origins allowed to use
# them; a wildcard stays anonymous-only.
_cors_origins = settings.cors_origins_list
_cors_allows_any_origin = "*" in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=not _cors_allows_any_origin,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def add_static_cache_headers(request: Request, call_next):
    """Let browsers cache uploaded images/PDFs for an hour instead of re-downloading
    them on every page view. Kept moderate (not immutable) because save_named_image()
    reuses the same filename when a product's image is replaced - after the hour,
    StaticFiles' own ETag/Last-Modified handling turns re-checks into cheap 304s."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") and response.status_code == 200:
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
    return response

app.include_router(activity.router)
app.include_router(auth.router)
app.include_router(customer_auth.router)
app.include_router(users.router)
app.include_router(customers.router)
app.include_router(brands.router)
app.include_router(categories.router)
app.include_router(products.router)
app.include_router(manuals.router)
app.include_router(promotions.router)
app.include_router(sets.router)
app.include_router(orders.router)
app.include_router(qr_codes.router)
app.include_router(hero_slides.router)
app.include_router(reports.router)
# Imported as `settings_router` because `settings` in this module is the config object.
app.include_router(settings_router.router)
app.include_router(telegram_webhook.router)


@app.get("/health", tags=["Health"])
def health_check(x_telegram_bot_token: str | None = Header(default=None)):
    # Only the Telegram bot's /check command should be able to hit this -
    # it sends the bot token back as a header. Anyone else gets a 404 so the
    # endpoint's existence isn't even revealed. compare_digest, not ==, so the
    # comparison can't be probed character-by-character through response timing.
    if not settings.TELEGRAM_BOT_TOKEN or not secrets.compare_digest(
        x_telegram_bot_token or "", settings.TELEGRAM_BOT_TOKEN
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Global error handling: any exception that isn't a normal HTTPException
# (i.e. everything unexpected) is logged with logger.error(), which - via
# app/core/logging_conf.py's TelegramErrorHandler - automatically forwards
# it to the configured Telegram chat too. The client only ever sees a
# generic 500 message, never internal details.
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )
