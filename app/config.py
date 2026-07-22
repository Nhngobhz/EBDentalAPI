"""
Application configuration.

All settings are read from environment variables / a `.env` file so that no
secret ever needs to be hard-coded. See `.env.example` for the full list of
variables you can set.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- General -----------------------------------------------------------
    APP_NAME: str = "EBDentalSupply"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    # Public base URL of this API, used to build links inside emails
    BASE_URL: str = "http://localhost:8000"

    # --- Database ------------------------------------------------------------
    DATABASE_URL: str = (
        "postgresql+psycopg2://store_user:store_password@localhost:5432/store_db"
    )
    # If true, tables are created automatically at startup with
    # SQLAlchemy's `create_all`. Turn this OFF in production and rely on
    # Alembic migrations instead (see the `alembic/` folder).
    AUTO_CREATE_TABLES: bool = False

    # --- Security / JWT ------------------------------------------------------
    SECRET_KEY: str = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    EMAIL_VERIFICATION_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    PASSWORD_RESET_EXPIRE_MINUTES: int = 30

    # --- CORS ------------------------------------------------------------------
    # Comma-separated list of allowed origins, e.g. "https://app.example.com,https://admin.example.com"
    # A plain string (not List[str]) on purpose: pydantic-settings tries to
    # JSON-decode complex types read from .env, which breaks on "*".
    CORS_ORIGINS: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    # --- Outbound email (SMTP) ------------------------------------------------
    # Leave MAIL_USERNAME empty to run in "dry run" mode: emails are logged
    # instead of actually sent. Useful for local development.
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = "no-reply@example.com"
    MAIL_FROM_NAME: str = "EBDentalSupply"
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_PORT: int = 587
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False

    # --- Telegram bot ------------------------------------------------------
    # Leave empty to disable Telegram notifications entirely.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    # Optional: message_thread_id of a forum topic inside TELEGRAM_CHAT_ID.
    # Leave empty to post to the group's General topic.
    TELEGRAM_ERROR_TOPIC_ID: str = ""
    TELEGRAM_LOGIN_TOPIC_ID: str = ""
    TELEGRAM_ORDER_TOPIC_ID: str = ""
    # Shared secret this app requires in the webhook URL/header Telegram calls back on
    # (see app/routers/telegram_webhook.py) - anyone who doesn't know it gets a 404/403.
    # Generate a random string for this, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
    TELEGRAM_WEBHOOK_SECRET: str = ""

    # --- File uploads ------------------------------------------------------
    UPLOAD_DIR: str = "static/uploads"
    MAX_IMAGE_SIZE_MB: int = 5
    MAX_PDF_SIZE_MB: int = 20

    # --- File storage (Cloudflare R2) ---------------------------------------
    # Leave R2_ACCESS_KEY_ID empty to run in "local disk" mode: uploads are
    # written under UPLOAD_DIR and served via the /static mount instead of
    # R2. Useful for local development without R2 credentials, the same way
    # MAIL_USERNAME being empty puts outbound email in dry-run mode.
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = ""
    # Public URL uploads are served from, e.g. https://pub-xxxx.r2.dev or a
    # custom domain mapped to the bucket. No trailing slash.
    R2_PUBLIC_BASE_URL: str = ""

    @property
    def r2_configured(self) -> bool:
        return bool(
            self.R2_ACCOUNT_ID
            and self.R2_ACCESS_KEY_ID
            and self.R2_SECRET_ACCESS_KEY
            and self.R2_BUCKET_NAME
            and self.R2_PUBLIC_BASE_URL
        )

    @property
    def r2_endpoint_url(self) -> str:
        return f"https://{self.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
