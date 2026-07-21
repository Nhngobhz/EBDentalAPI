# Store Management API

A FastAPI + PostgreSQL backend for managing staff accounts, customers, and a
product catalog (brands, categories, products, manuals, promotions), with:

- Password hashing (bcrypt)
- JWT authentication
- Permission-based access control (4 explicit permission flags per staff user)
- Email confirmation for new accounts
- Telegram bot notifications for staff logins and application errors
- Image/PDF uploads for the various `*_image` / `pdf` fields
- Alembic migrations

This has been built and tested end-to-end against a real PostgreSQL 16
instance (registration → email confirmation → login → permission-gated
CRUD → file upload → error handling), not just written from memory - see
`tests/test_api.py`.

---

## 1. Design decision

**Real design decisions** (please double check these):

1. **`Product.brand_name` → `brand_id`, `Manual.product_name` → `product_id`.**
   Both were changed from a free-text string to a foreign key into the
   related table. Storing `brand_name` as raw text on every product risks
   typos and mismatches (rename a brand and every product silently
   disagrees with the Brand table). The API still *returns* `brand_name`
   /`product_name` in responses (nested), you just create/update products
   and manuals by passing `brand_id` / `product_id`. The same reasoning
   was later applied to `Product.category` (free text) → `category_id`:
   categories are now a real `Category` table (`category_name`, optional
   `category_image`), created/managed the same way as brands via
   `/categories`. Unlike `brand_id`, `category_id` is optional (`null`
   allowed) since not every product has been sorted into one.

2. **Customers can now log in themselves, and `access_permission` gates
   product prices.** `POST /auth/customer/register` is public self-service
   registration (mirrors `POST /auth/register` for staff: password,
   email confirmation, login, forgot/reset-password - see
   `app/routers/customer_auth.py`). A newly registered customer starts
   with `access_permission=False`; only a `customer_management` staff
   member flipping it (via `PUT /customers/{id}`) unlocks prices for them.
   Concretely: `GET /products` and `GET /products/{id}` mask `price` as
   the literal string `"XXXX"`, and omit `discount` entirely (`null`),
   for anyone who isn't (a) an active staff user or (b) a customer with
   `access_permission=True` - see `get_price_visibility` in
   `app/core/deps.py`. `GET /promotions` and `GET /promotions/{id}` apply
   the exact same masking to `price`/`old_price` (Promotion keeps its own
   separate `old_price` column - see point 11). Customer records
   created directly by staff (`POST /customers/`) still have no password
   and can't log in - only self-registration sets one. Both principals
   share the JWT scheme but carry a `"type": "user"` / `"type":
   "customer"` claim so one can't be used as the other, since `User` and
   `Customer` ids overlap. `POST /auth/login` accepts credentials for
   either kind of account (staff match first, then customer) - see
   `app/routers/auth.py`; `POST /auth/customer/login` remains as a
   customer-only alternative.

3. **`role_title` is a label, not a permission source.** Authorization is
   driven entirely by the four boolean columns
   (`user_management`, `price_listing`, `product_management`,
   `customer_management`). `role_title` (e.g. "Sales Manager") is free
   text for display only. A user with all four booleans `True` is a
   de-facto super-admin; there's no separate `is_superuser` flag since it
   wasn't in your schema.

4. **Permission → resource mapping** (since you listed 4 permissions but
   6 resources):
   - `user_management` → manage User accounts (create/edit/permissions/deactivate)
   - `customer_management` → manage Customers
   - `product_management` → manage Brands, Categories, Products (non-price fields), Manuals
   - `price_listing` → change `price`/`discount` on Products, and full CRUD on Promotions
   - Editing a product's price specifically requires **both**
     `product_management` AND `price_listing` via the general update
     endpoint, OR just `price_listing` via the dedicated
     `PATCH /products/{id}/price` endpoint - useful if you want a
     "pricing only" role that can't touch anything else.

5. **Staff (User) accounts can only be created by an existing admin**, via
   `POST /users/` (requires `user_management`) with a role/permissions set
   up front. There is no public staff self-registration endpoint - an
   open signup route that mints rows in the staff table (even with zero
   permissions) is unnecessary attack surface for an internal admin
   system, so it was removed. The account still needs email confirmation
   before it can log in. Customer self-registration
   (`POST /auth/customer/register`) remains public, since that's normal
   for a storefront - see point 2.

6. **Product catalog reads are public**; only writes need a staff account.
   `GET` on `/brands`, `/products`, `/manuals`, `/promotions` needs no
   token, since these presumably power a public storefront. Everything
   under `/users` and `/customers` requires authentication.

7. **Added fields not in your original list**, because the auth/audit
   system doesn't work without them: `id`, `hashed_password`, `is_active`,
   `is_verified`, `verification_token(_expires)`, `reset_token(_expires)`,
   `last_login` on `User`; `created_at` on Brand/Product/Manual/Promotion.

8. **"SQL server"** was interpreted as "set up the PostgreSQL database
   this API runs on" (schema + migrations), not literally Microsoft SQL
   Server. `docker-compose.yml` gives you a real Postgres instance
   alongside the API with one command.

9. **Deleting things:** deleting a `User` is a soft-delete
   (`is_active=False`, preserves login history). Deleting a `Brand` (or
   `Category`) that still has `Product`s attached is rejected (400) rather
   than cascading. Deleting a `Product` cascades to its `Manual`s (a
   manual without its product is meaningless).

10. **`Product.product_type`**: lets the catalog be sorted/filtered into
    `"single"` items vs. `"combo"` (bundle) products, independent of
    category - e.g. a "Portable X-ray" category contains both standalone
    units and `"combo"` bundles that add a laptop/trolley/sensor. New
    products default to `"single"`. Stored as a plain string rather than a
    native Postgres enum (consistent with `badge`/`role_title` elsewhere
    in this schema) so adding a third value later (e.g. `"bundle"`) is a
    one-line change to `ProductType` in `app/schemas.py`, not a migration.
    `GET /products` accepts `?product_type=combo` to filter on it.

11. **`Product.old_price` → `discount`.** Rather than storing a second
    absolute price to derive a markdown from, `Product` stores `price`
    (the real price) plus a `discount` percentage (integer `0`-`100`,
    defaults to `0`), validated as a numeric range in `app/schemas.py`.
    `discount` follows the same permission/masking rules `old_price` had
    (`price_listing` to change it, hidden alongside `price` for viewers
    without price access). Promotion's own `price`/`old_price` pair is
    unchanged - it's a separate, unrelated feature.

12. **`Product.product_code` / `Product.uom`** (added on request): 
    `product_code` is the product's SKU, `String(50)`, **unique** once set
    (same nullable+unique shape as `reset_token`/`verification_token`
    elsewhere in this schema - any number of `null`s are allowed through a
    unique index, but two products can't share a code). Creating or
    updating a product with a `product_code` already in use gets a `400`
    (`"product_code already in use"`), checked in `app/routers/products.py`
    before the insert/update rather than left to surface as a raw
    constraint-violation `500`. `uom` (unit of measure - e.g. `"pcs"`,
    `"box"`, `"set"`) is free text, `String(20)`, no fixed vocabulary
    since none was specified. Both are optional (`null` allowed) since all
    existing products predate the fields, same reasoning as `category_id`.
    Neither is masked for price visibility (see point 2) since neither is
    pricing data.

---

## 2. Project structure

```
app/
  main.py            FastAPI app, middleware, routers, global error handler
  config.py          Settings, read from .env
  database.py        SQLAlchemy engine/session
  models.py           All 7 SQLAlchemy models
  schemas.py          All Pydantic request/response schemas
  core/
    security.py       Password hashing (bcrypt) + JWT
    deps.py            get_current_user / require_permission(...)
    email.py           Verification + password-reset emails
    files.py           Shared image/PDF upload helper
    logging_conf.py    Logging setup + TelegramErrorHandler
  services/
    telegram.py        Telegram bot notification helpers
  routers/
    auth.py, customer_auth.py, users.py, customers.py, brands.py,
    categories.py, products.py, manuals.py, promotions.py
alembic/               Migrations (env.py wired to app.models / .env)
scripts/create_admin.py   Bootstrap script for the first admin account
scripts/seed_catalog.py   Inserts a few sample brands/products for local testing
tests/                  pytest suite (run against a real Postgres db)
schema.sql              Reference DDL dump (informational only)
docker-compose.yml, Dockerfile, entrypoint.sh
```

---

## 3. Quick start with Docker (recommended)

```bash
cp .env.example .env
# at minimum, change SECRET_KEY in .env

docker compose up --build

```

This starts Postgres, waits for it to be healthy, runs Alembic migrations
automatically, and starts the API on **http://localhost:8000**.

Then, in a separate terminal, create your first admin account:

```bash
docker compose exec api python -m scripts.create_admin

```

Optionally, seed a few sample brands/products to have something to look
at (safe to re-run - it skips anything that already exists):

```bash
docker compose exec api python -m scripts.seed_catalog
```

Swagger UI / ReDoc / the raw OpenAPI schema (`/docs`, `/redoc`,
`/openapi.json`) are disabled - see [`AI_AGENT_GUIDE.md`](AI_AGENT_GUIDE.md)
for a full hand-written endpoint reference instead.

---

## 4. Quick start without Docker

Requires Python 3.11+ and a running PostgreSQL server.

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt   # Windows: venv\Scripts\pip install -r requirements.txt

# Create the database/user in psql (adjust to taste):
#   CREATE USER store_user WITH PASSWORD 'store_password';
#   CREATE DATABASE store_db OWNER store_user;

cp .env.example .env
# edit .env: set DATABASE_URL and SECRET_KEY at minimum

./venv/bin/alembic upgrade head
./venv/bin/python -m scripts.create_admin
./venv/bin/uvicorn app.main:app --reload
```

---

## 5. Configuration reference

All settings live in `.env` (see `.env.example` for the full list with
comments). The important ones:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLAlchemy Postgres connection string |
| `SECRET_KEY` | JWT signing key - **change this**, it's a placeholder |
| `BASE_URL` | Used to build links inside confirmation/reset emails |
| `MAIL_USERNAME` / `MAIL_PASSWORD` / `MAIL_SERVER` / `MAIL_PORT` | SMTP creds. Leave `MAIL_USERNAME` empty to run in "dry run" mode (emails are logged, not sent) - useful while developing |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Leave empty to disable Telegram notifications entirely |
| `CORS_ORIGINS` | Comma-separated allowed origins, or `*` |

### Setting up the Telegram bot
1. Message **@BotFather** on Telegram → `/newbot` → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot (or add it to a group).
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the `chat.id` value into `TELEGRAM_CHAT_ID`.

Once configured, you'll get a Telegram message on every staff login
(flagged distinctly if they have `user_management`), and on any unhandled
server error (via `logger.error(...)`, which happens automatically inside
the global exception handler in `main.py`).

### Setting up real email
Any SMTP provider works (Gmail with an App Password, SendGrid, Mailgun,
your company's SMTP relay, etc.) - fill in `MAIL_USERNAME`, `MAIL_PASSWORD`,
`MAIL_SERVER`, `MAIL_PORT` accordingly.

**Note:** email addresses using reserved test TLDs (`.test`, `.example`,
`.invalid`, `.localhost`) are correctly rejected by validation - use a
real-looking domain (even a fake one like `you@yourcompany.com`) when
testing.

---

## 6. API overview

There's no interactive `/docs` (disabled - see section 3). For the full
endpoint-by-endpoint reference (request/response shapes, error format,
auth flows), see [`AI_AGENT_GUIDE.md`](AI_AGENT_GUIDE.md). Summary:

| Endpoint | Auth | Notes |
|---|---|---|
| `GET /auth/verify-email?token=` | Public | Confirms email (staff or self-changed staff email). Returns a small self-closing HTML page (opened directly from the email link), not JSON |
| `POST /auth/resend-verification` | Public | |
| `POST /auth/login` | Public | Combined login - OAuth2 form; `username` = email. Tries a staff (User) match first, then falls back to Customer, so either kind of account can log in here. Response includes `account_type` ("user"/"customer") and the matching `user`/`customer` object. Sends Telegram notice on staff login |
| `POST /auth/forgot-password` / `reset-password` | Public | |
| `POST /auth/customer/register` | Public | Customer self-registration; `access_permission=False` by default |
| `GET /auth/customer/verify-email?token=` | Public | Confirms customer email (registration or self-changed email) |
| `POST /auth/customer/resend-verification` | Public | |
| `POST /auth/customer/login` | Public | OAuth2 form; `username` = email. Customer-only (kept for callers that want to restrict login to customers) - same effect as `/auth/login` when the account is a Customer |
| `POST /auth/customer/forgot-password` / `reset-password` | Public | |
| `GET/PUT /users/me` | Any logged-in user | Self profile. Changing `email` here immediately flips `is_verified=False` and sends a confirmation link to the *new* address - the account can't do anything requiring `get_verified_user` again until it's re-confirmed |
| `POST /users/me/change-password` | Any logged-in user | Requires `current_password` + `new_password` |
| `GET/POST/PUT/DELETE /users/...` | `user_management` | Staff management. No public staff self-registration - staff accounts are only created here |
| `GET/PUT /customers/me` | Any logged-in customer | Self profile. Same email re-verification behavior as `/users/me` |
| `POST /customers/me/change-password` | Any logged-in customer | Requires `current_password` + `new_password` (only works for self-registered customers, i.e. ones with a password) |
| `GET/POST/PUT/DELETE /customers/...` | `customer_management` | Staff-side customer management, incl. toggling `access_permission` |
| `GET /brands`, `/categories`, `/products`, `/manuals`, `/promotions` | **Public** | Catalog browsing. `products` `price` is masked as `"XXXX"` and `discount` is omitted (`null`) unless the caller is staff or a customer with `access_permission=True`. `GET /products` also accepts `category_id` and `product_type` filters |
| `POST /brands/`, `POST /categories/` | `product_management` | `multipart/form-data`: `brand_name`/`category_name` plus an optional `file` to set the image in the same request |
| `POST /manuals/` | `product_management` | `multipart/form-data`: `product_id`, optional `description`, plus an optional `file` to set the PDF in the same request |
| `PUT/DELETE /brands/...`, `PUT/DELETE /categories/...`, `PUT/DELETE /manuals/...` | `product_management` | Deleting a `Category` that still has `Product`s assigned is rejected (400), same as `Brand` |
| `POST/DELETE /products/...` | `product_management` (+`price_listing` if price included) | `product_type` (`"single"`/`"combo"`, default `"single"`) and optional `category_id` are set here |
| `PATCH /products/{id}/price` | `price_listing` | |
| `POST/PUT/DELETE /promotions/...` | `price_listing` | |
| `POST .../{id}/image`, `.../{id}/pdf` | Same permission as editing that resource | File uploads (still available for setting/replacing an image after creation) |

---

## 7. Running the tests

```bash
./venv/bin/pip install pytest
# create a scratch database first, e.g. store_db_test (tests/conftest.py
# points at postgresql+psycopg2://store_user:store_password@localhost:5432/store_db_test)
./venv/bin/python -m pytest tests/ -v
```

The suite covers: registration → email confirmation → login, permission
denial, the price_listing/product_management split, brand/product/manual
FK integrity (restrict + cascade), customer CRUD, promotion date
validation, file upload validation, and the global error handler.

---

## 8. Possible future improvements (not implemented, to keep scope focused)

- Refresh tokens (currently a single access token with a configurable expiry)
- Rate limiting / lockout on repeated failed logins
- Cloud storage (S3/GCS) instead of local disk for uploads
- Row-level/territory-based access instead of global permissions
