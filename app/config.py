"""
Application configuration.

All settings are read from environment variables / a `.env` file so that no
secret ever needs to be hard-coded. See `.env.example` for the full list of
variables you can set.
"""
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The value SECRET_KEY falls back to when nothing is configured. Named so the
# production guard at the bottom of Settings can recognize "never changed".
_PLACEHOLDER_SECRET_KEY = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET"


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
    SECRET_KEY: str = _PLACEHOLDER_SECRET_KEY
    ALGORITHM: str = "HS256"
    # Staff/admin sessions, deliberately short. One of these tokens can reprice the
    # catalogue, read every customer and edit other staff accounts, so a browser left
    # open in a clinic shouldn't stay dangerous for long - expiry is the only thing
    # that ends a session nobody logged out of.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    # Customers get a fortnight instead: the worst a stolen customer token can do is
    # read that person's own orders and place another, and being thrown back to a
    # login form every day is exactly the friction that loses a storefront its repeat
    # buyers. Kept a separate setting so shortening one can never quietly shorten the
    # other - see token_lifetime() in core/security.py, which picks between them off
    # the token's own "type" claim.
    CUSTOMER_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 14  # 14 days
    EMAIL_VERIFICATION_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    PASSWORD_RESET_EXPIRE_MINUTES: int = 30

    # --- Google Sign-In --------------------------------------------------------
    # The OAuth 2.0 *Web application* client ID from Google Cloud Console
    # (APIs & Services -> Credentials), e.g. "1234-abc.apps.googleusercontent.com".
    # Only the client ID is needed, never the client secret: the browser-side
    # Google Identity Services button does the OAuth dance and hands the page a
    # signed ID token, which this server verifies against Google's public keys
    # (app/core/google_auth.py). The same value must be set on the Flask app so
    # it can render the button, and every origin the button is served from
    # (e.g. http://localhost:5000) must be listed as an Authorized JavaScript
    # origin on that same credential.
    # Leave empty to disable Google sign-in - POST /auth/google then 400s.
    GOOGLE_CLIENT_ID: str = ""

    @property
    def google_auth_configured(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID)

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

    # --- KHQR payments -------------------------------------------------------
    # Two interchangeable providers; the frontend/API behave identically either way.
    #
    # 1. ABA PayWay (preferred when configured): ABA Bank's merchant gateway
    #    generates the KHQR and confirms payment automatically. Credentials come from
    #    registering as a PayWay merchant with ABA (their integration team also
    #    whitelists your server's IP/domain - calls fail until they do).
    # 2. Bakong-direct: we build the KHQR payload ourselves from BAKONG_ACCOUNT_ID
    #    (any Bakong-member bank account id, e.g. "yourname@aba" from the ABA app's
    #    KHQR screen). Payments arrive fine without any registration; automatic
    #    confirmation additionally needs a BAKONG_API_TOKEN from
    #    https://api-bakong.nbc.gov.kh - without it staff confirm via "Mark as Paid".
    #
    # If neither is configured, KHQR checkout is disabled (customers are told to
    # choose Cash instead).
    PAYWAY_MERCHANT_ID: str = ""
    # PayWay profile's API key (some PayWay docs call it the "public key").
    PAYWAY_API_KEY: str = ""
    # Sandbox: https://checkout-sandbox.payway.com.kh - switch to
    # https://checkout.payway.com.kh when ABA moves you to production. Only the
    # scheme+host is used (see payway_base_url); pasting a full endpoint URL here
    # by mistake is tolerated rather than silently producing a doubled path.
    PAYWAY_API_BASE: str = "https://checkout-sandbox.payway.com.kh"

    @property
    def payway_base_url(self) -> str:
        """PAYWAY_API_BASE reduced to scheme://host. PayWay's docs list the base URL
        and the endpoint path separately, so it's an easy mistake to paste the whole
        `.../api/payment-gateway/v1/payments/purchase` URL into the base - which
        would otherwise append the path twice and 404."""
        from urllib.parse import urlsplit

        parts = urlsplit(self.PAYWAY_API_BASE.strip())
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
        return self.PAYWAY_API_BASE.strip().rstrip("/")

    # Bakong-direct comes in two flavours, both handled by services/khqr.py.
    # BAKONG_ACCOUNT_ID is the preferred one and wins if both are set:
    #
    #  - BAKONG_ACCOUNT_ID (+ the two fields below): builds a QR to the KHQR spec's
    #    "individual" shape, tag 29 sub-fields 00/01/02. All three values are
    #    printed by `python scripts/decode_khqr.py <your static QR>`. For a bank
    #    account (rather than a Bakong wallet) the account id is the *bank's*
    #    Bakong id and the account number goes in BAKONG_ACCOUNT_INFORMATION -
    #    e.g. ABA issues abaakhppxxx@abaa + your account number.
    #  - KHQR_STATIC_TEMPLATE: legacy fallback. Paste the full payload of your bank
    #    app's static "receive money" QR and its payee routing is copied into every
    #    generated QR verbatim - including any proprietary tags the bank added
    #    (ABA's dual-currency QR carries an `abaP2P` tag 40 that is not part of the
    #    KHQR spec). Kept as a rollback path; prefer BAKONG_ACCOUNT_ID.
    BAKONG_ACCOUNT_ID: str = ""
    # Optional per the KHQR spec, but what actually identifies the destination when
    # BAKONG_ACCOUNT_ID is a bank rather than a personal wallet alias: the bank
    # account number (or phone number) that receives the money.
    BAKONG_ACCOUNT_INFORMATION: str = ""
    # Human-readable acquiring bank name, e.g. "ABA Bank". Shown by some wallets.
    BAKONG_ACQUIRING_BANK: str = ""
    KHQR_STATIC_TEMPLATE: str = ""
    KHQR_MERCHANT_NAME: str = "EB DENTAL"
    KHQR_MERCHANT_CITY: str = "Phnom Penh"
    # How long a generated dynamic QR stays payable. The KHQR spec makes the
    # expiration timestamp mandatory on dynamic codes; an expired QR is refused by
    # the payer's app, and staff can always issue a fresh one against the same
    # order (POST /orders/{id}/khqr).
    KHQR_EXPIRY_MINUTES: int = 60
    BAKONG_API_TOKEN: str = ""
    BAKONG_API_BASE: str = "https://api-bakong.nbc.gov.kh"

    @property
    def payway_configured(self) -> bool:
        return bool(self.PAYWAY_MERCHANT_ID and self.PAYWAY_API_KEY)

    # "auto" (default), "payway", or "bakong". An explicit value pins the provider
    # even when both are configured - e.g. keeping PayWay credentials in place while
    # testing against a personal Bakong/bank QR.
    KHQR_PROVIDER: str = "auto"

    @property
    def qr_provider(self) -> str:
        """"payway", "bakong", or "" (KHQR checkout disabled). On "auto", PayWay wins
        when both are configured - it's the one that can also confirm payments by
        itself. A provider named explicitly is only honoured if it's actually
        configured, so a stale pin can't disable checkout outright."""
        bakong_ready = bool(self.KHQR_STATIC_TEMPLATE or self.BAKONG_ACCOUNT_ID)
        pinned = self.KHQR_PROVIDER.strip().lower()
        if pinned == "payway" and self.payway_configured:
            return "payway"
        if pinned == "bakong" and bakong_ready:
            return "bakong"
        if self.payway_configured:
            return "payway"
        if bakong_ready:
            return "bakong"
        return ""

    @property
    def khqr_configured(self) -> bool:
        return bool(self.qr_provider)

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

    # --- SAP catalogue sync --------------------------------------------------
    # How the Settings screen's "Run sync now" button reaches SQL Server - the same
    # --transport values scripts/sap_db_pull.py accepts (auto | odbc | local | ssh).
    # "local" on the server, where store-api and SQL Server share a machine; "auto"
    # (which resolves to ssh) from a development box. Deployment topology rather than a
    # preference, which is why it is an environment variable and not a Settings row:
    # the value that is right here is wrong there, and the two must not be swapped by
    # copying a database.
    SAP_SYNC_TRANSPORT: str = "auto"
    # How long a run may take before it is killed. A hung sqlcmd, or an SSH session
    # waiting on a host that has gone away, would otherwise leave the run marked
    # "running" for the life of the process and every later run refused as busy. A full
    # two-catalogue sync of ~8,000 items takes a couple of minutes.
    SAP_SYNC_TIMEOUT_SECONDS: int = 1800

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @model_validator(mode="after")
    def _reject_unsafe_production_config(self):
        """Refuse to boot a *production* deployment on the development defaults.

        Every setting above has a permissive default so a fresh checkout runs
        with no .env at all - which is exactly what makes shipping one of those
        defaults by accident easy. This only fires when ENVIRONMENT=production,
        so local development and the test suite are untouched; it turns a silent
        "anyone can forge a JWT" into a startup error naming what to fix.
        """
        if self.ENVIRONMENT.strip().lower() != "production":
            return self

        problems = []
        if self.SECRET_KEY == _PLACEHOLDER_SECRET_KEY:
            problems.append("SECRET_KEY is still the placeholder value")
        elif len(self.SECRET_KEY) < 32:
            problems.append("SECRET_KEY is shorter than 32 characters")
        if self.DEBUG:
            problems.append("DEBUG must be false")
        if "*" in self.cors_origins_list:
            problems.append('CORS_ORIGINS must name real origins instead of "*"')
        if self.AUTO_CREATE_TABLES:
            problems.append("AUTO_CREATE_TABLES must be false (use Alembic migrations)")
        if problems:
            raise ValueError(
                "Refusing to start with ENVIRONMENT=production: " + "; ".join(problems)
            )
        return self


settings = Settings()
