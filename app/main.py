import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.logging_conf import get_logger, setup_logging
from app.database import Base, engine
from app.routers import (
    auth,
    brands,
    categories,
    customer_auth,
    customers,
    manuals,
    orders,
    products,
    promotions,
    users,
)

setup_logging()
logger = get_logger("main")

os.makedirs(settings.UPLOAD_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AUTO_CREATE_TABLES:
        logger.info("AUTO_CREATE_TABLES=true - running Base.metadata.create_all()")
        Base.metadata.create_all(bind=engine)
    logger.info("%s started (environment=%s)", settings.APP_NAME, settings.ENVIRONMENT)
    yield


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(customer_auth.router)
app.include_router(users.router)
app.include_router(customers.router)
app.include_router(brands.router)
app.include_router(categories.router)
app.include_router(products.router)
app.include_router(manuals.router)
app.include_router(promotions.router)
app.include_router(orders.router)


@app.get("/health", tags=["Health"])
def health_check(x_telegram_bot_token: str | None = Header(default=None)):
    # Only the Telegram bot's /check command should be able to hit this -
    # it sends the bot token back as a header. Anyone else gets a 404 so the
    # endpoint's existence isn't even revealed.
    if not settings.TELEGRAM_BOT_TOKEN or x_telegram_bot_token != settings.TELEGRAM_BOT_TOKEN:
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
