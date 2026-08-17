# AI Agent Guide to the Store Management API

This file exists so another AI (or a developer in a hurry) can call this
API correctly without access to interactive docs - Swagger UI, ReDoc and
`/openapi.json` are intentionally disabled on this server (see
`app/main.py`). Read this file instead of trying to fetch `/docs`.

If you are an AI agent about to call this API on someone's behalf, read
section 0 and section 8 first - they cover the mistakes that produce
confusing (not obviously wrong) results.

---

## 0. Orientation - read this first

- **Base URL**: whatever `BASE_URL` / the deployment host is (default local
  dev: `http://localhost:8000`). All paths below are relative to it.
- **Content type**: every request/response body is JSON
  (`application/json`) **except** three groups of endpoints, which use
  `multipart/form-data` or `application/x-www-form-urlencoded` - see
  section 3.
- **There is no API versioning prefix** (no `/v1/`). Paths are exactly as
  listed in section 6.
- **Two independent principal types share this API**: staff (`User`) and
  storefront `Customer`. They authenticate separately, get separately
  scoped tokens, and a token for one **cannot** be used as the other
  (server checks a `type` claim - see section 1). If you're building a
  request and don't know which kind of account you're representing, ask
  the caller rather than guessing.
- **Trailing slash matters for collection endpoints.** `GET /products/`
  and `POST /products/` use a trailing slash; item-scoped paths like
  `GET /products/{id}` do not. This is standard FastAPI router behavior,
  not a typo - use the exact paths in section 6.
- **10 "physical" entities**: `User` (staff), `Customer`, `Brand`,
  `Category`, `Product`, `Manual`, `Promotion`, `Set`, `Order`,
  `OrderItem`. Three join tables hang off them and are never addressed
  directly (they're edited as a field of their owner - see "Bundle
  contents" in section 6): `PromotionItem`, `SetItem`, `ProductFreeItem`.
  A fourth child table, `ProductImage`, holds a product's extra gallery
  photos and is addressed only via `/products/{id}/gallery` (section 6).
  An `Order` row is either a **quote** or a real **order**
  (`order_type`): staff-placed rows and customer "cash" checkouts are
  quotes (server-priced snapshots, payment happens offline later);
  only a customer KHQR checkout is a real order, which starts
  `payment_status: "unpaid"` and carries a generated KHQR payload.
  Either kind stays editable by staff until `payment_status` is `"paid"`,
  after which it is frozen (`409` on any write, including delete).
  See section 6's Orders table.

---

## 1. Authentication

### 1.1 Token endpoints (OAuth2 "password" flow)

Two endpoints issue a JWT access token from an email + password. Both
expect `application/x-www-form-urlencoded` (the standard OAuth2
password-grant shape), **not JSON** - field names are fixed by the OAuth2
spec:

| Endpoint | Who it authenticates |
|---|---|
| `POST /auth/login` | Either staff or customer - tries `User` first, falls back to `Customer` |
| `POST /auth/customer/login` | Customer only |

(A third endpoint, `POST /auth/google`, issues the same kind of token from
a Google ID token instead of a password - it takes a **JSON** body, see
1.6.)

Form fields required: `username` (the account's **email address** - not a
separate username field, there isn't one) and `password`. Example:

```
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username=someone%40example.com&password=hunter2
```

Response (`POST /auth/login`):
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "account_type": "user",
  "user": { ...UserOut... },
  "customer": null
}
```
`account_type` tells you which branch matched; only the matching one of
`user` / `customer` is populated. `POST /auth/customer/login` returns the
simpler `{access_token, token_type, customer}` shape (no `account_type`).

### 1.2 Using the token

Every authenticated endpoint expects:
```
Authorization: Bearer <access_token>
```
There is no refresh token / refresh endpoint - when the token expires
(`ACCESS_TOKEN_EXPIRE_MINUTES`, default 24h) the caller has to log in
again.

### 1.3 Registration (customers only)

Staff accounts have **no public self-registration endpoint** - only an
existing `user_management` staff member can create one (`POST /users/`,
section 6). If asked to "sign up a new staff user" with no logged-in
admin available, say so rather than looking for a registration route that
doesn't exist.

Customers self-register at `POST /auth/customer/register` (JSON body this
time, not form-encoded - see section 6). New customers start with
`access_permission: false` and `is_verified: false`.

### 1.4 Email verification is a hard gate

New accounts (both kinds) start `is_verified: false`. Most endpoints
require `is_verified: true` and return `403` with
`"Please confirm your email address before continuing"` until the account
clicks the emailed verification link (`GET /auth/verify-email?token=...`
or the customer equivalent). **In local/dev environments with no SMTP
configured** (`MAIL_USERNAME` unset), verification emails are logged to
the server console instead of sent - the token is still in the log line,
just not delivered. If a login/action is unexpectedly blocked with that
403, verification is the first thing to check, and if you don't have
access to the mailbox or server logs, ask the user for the token rather
than assuming registration failed.

### 1.5 Deactivated accounts

`is_active: false` (staff: soft-deleted via `DELETE /users/{id}`;
customers: `access_permission`/`is_active` toggled via `PUT
/customers/{id}`) blocks login and all authenticated calls with `403
"Account is deactivated"`. This is distinct from the verification 403
above - check the message text to tell them apart.

### 1.6 Google sign-in (`POST /auth/google`)

`{"credential": "<Google ID token>"}` in, the same body `POST /auth/login`
returns out (`account_type` + the matching `user`/`customer`). The
credential is the JWT that Google Identity Services hands the storefront
page - **this server never sees a Google password, an authorization code,
or the client secret**; it verifies the token's RS256 signature against
Google's published keys and checks `aud == GOOGLE_CLIENT_ID`
(`app/core/google_auth.py`). Unset `GOOGLE_CLIENT_ID` → `400`, a token
that doesn't verify → `401`.

What it does with a verified token, in order: an existing staff `User`
with that email signs in as staff; otherwise an existing `Customer` does
(**including one staff created by hand with no password** - the person who
owns the mailbox is who that record was for); otherwise a **new
`Customer`** is created. Things worth knowing before assuming a bug:

- **Emails are matched case-insensitively here** (`func.lower(...)`),
  unlike the password endpoints' exact match - Google always reports a
  lowercase address, rows created elsewhere keep whatever casing was typed.
- **It never creates a staff account** - 1.3 still holds. A Google sign-in
  for an unknown email always lands on the customer side.
- The account comes out `is_verified: true` (Google confirming the address
  is the same proof the emailed link asks for) but still
  `access_permission: false` - price visibility remains a
  `customer_management` decision, exactly as with self-registration.
- A customer created this way has **`hashed_password` NULL**, so they can't
  use `POST /auth/customer/login` or the forgot-password flow (both of
  which require a password to exist) - they sign in with the button. This
  is the same NULL-password state as a staff-created record, and
  `POST /customers/me/change-password` still 400s for them.
- `email_verified: false` on the Google token is refused outright - that
  claim is what makes matching an existing account by email safe.
- Google's `picture` URL is copied into `user_image`/`customer_image`
  **only when that field is empty**, so an uploaded avatar is never
  overwritten.

### 1.7 Failed sign-ins are rate limited

The password endpoints (`POST /auth/login` and `POST /auth/customer/login`)
count **failed** attempts per (client IP, email) pair. After 10 within 15
minutes that pair is locked out for 15 minutes and every further attempt gets
`429` with a `Retry-After` header, *including one carrying the correct
password* - so retrying a "wrong password" in a loop makes things worse, not
better. A successful login clears the counter, and 403s for
unverified/deactivated accounts don't count towards it (those callers already
proved they know the password).

`POST /auth/google` is deliberately **not** limited - it verifies a signature
from Google rather than a guessable secret. The limit lives in-process
(`app/core/ratelimit.py`), so behind multiple workers it applies per worker.

---

## 2. Authorization model (permissions)

Staff (`User`) authorization is **not** role-based despite the
`role_title` field existing - `role_title` (e.g. "Sales Manager") is a
free-text display label only and is never checked by any endpoint. Actual
authorization comes from five independent boolean flags on the `User`
row, checked directly:

| Permission | Grants |
|---|---|
| `user_management` | Create/edit/deactivate staff (`User`) accounts, view the staff list |
| `customer_management` | Full CRUD on `Customer` records, including toggling `access_permission` |
| `product_management` | CRUD on `Brand`, `Category`, `Product` (non-price fields), `Manual`, and full CRUD on `Promotion` and `Set` |
| `price_listing` | Set `price`/`discount` on `Product`, and manage `Order`s (list/read/place/edit/delete, mark paid, issue a KHQR) |
| `admin` | Read and write site-wide settings (`/settings`, section 6) - store contact details, printed-quote wording, maintenance mode |

Notes an agent should know before assuming a 403 is a bug:

- These flags are **independent**, not a hierarchy - `user_management`
  does not imply the other three. A user with all four of the original
  flags `true` is a de-facto super-admin; there is no separate
  `is_superuser` flag.
- `admin` was added after the other four and is **not** implied by them
  for new accounts - it has to be granted explicitly. Migration
  `a3d81f6c94e2` backfilled it onto accounts that already held all four,
  so pre-existing super-admins have it, but `POST /users/` defaults it to
  `false` like every other flag.
- A staff member cannot revoke their **own** `admin` when they are the
  last active holder of it (`400`). Settings is the only place the flag
  can be granted from, so the last holder dropping it would make the
  permission ungrantable without a manual database write.
- Changing an **existing** product's `price`/`discount` via the general
  `PUT /products/{id}` requires **both** `product_management` AND
  `price_listing`. A caller with only `price_listing` must instead use
  `PATCH /products/{id}/price`.
- Creating a **new** product (`POST /products/`) only needs
  `product_management`, even though the payload includes `price` - only
  *later changing* the price on an existing product is gated by
  `price_listing` too.
- Customer-facing price visibility (whether `GET /products` shows a real
  number or the masked string, see section 4) is a **separate concept**
  from staff permissions - don't conflate "can this customer see prices"
  with "does this user have `price_listing`".
- Permission denials return `403` with a body like
  `{"detail": "This action requires the 'product_management' permission"}`
  - the missing permission name is always in the message, so parse it
  rather than guessing which of the four is missing.

If you're not sure which permission an action needs, check section 6's
Auth column before making the call - don't trial-and-error against a
production system.

---

## 3. Request body formats - JSON vs. form data

Most endpoints take a plain JSON body. These are the exceptions, and
sending JSON to them will fail:

| Endpoints | Format | Why |
|---|---|---|
| `POST /auth/login`, `POST /auth/customer/login` | `application/x-www-form-urlencoded` | OAuth2 password-grant spec |
| `POST /brands/` | `multipart/form-data` (`brand_name` field + optional `file`) | Lets you set the brand image in the same request that creates it |
| `POST /categories/` | `multipart/form-data` (`category_name` field + optional `file`) | Same reasoning, for the category image |
| `POST /manuals/` | `multipart/form-data` (`product_id`, optional `description`, optional `file`) | Same reasoning, for the manual's PDF |
| `POST .../{id}/image`, `POST .../{id}/pdf` (on users, customers, brands, categories, products, manuals) | `multipart/form-data` (single `file` field) | Direct file upload |
| `POST /products/{id}/gallery` | `multipart/form-data` (**`files`** - repeat the field once per file) | Several extra product photos in one request |

Everything else - including `PUT /brands/{id}` / `PUT /categories/{id}`
(metadata-only update, image unchanged) - is plain JSON.

File upload constraints (`app/core/files.py`): images must be
`image/jpeg`, `image/png`, `image/webp`, or `image/gif` and ≤5MB;
PDFs must be `application/pdf` and ≤20MB. A rejected upload returns `400`
with the reason in `detail`, not a generic validation error - check the
file's actual `Content-Type` header if this happens unexpectedly.

Uploaded files are stored in Cloudflare R2 (`app/core/storage.py`); the
`*_image`/`pdf` fields returned in responses are full `https://` URLs
pointing at the R2 bucket's public domain. If R2 isn't configured (no
`R2_ACCESS_KEY_ID` set, e.g. in local dev), uploads fall back to local disk
under `static/uploads/<category>/` and are returned as `/static/...` paths
instead - either way, treat the field as an opaque URL/path to display or
link to, not something to construct yourself.

---

## 4. Product price masking - read before querying `/products`

`GET /products/` and `GET /products/{id}` are **public** (no auth
required) but mask financial data unless the caller is entitled to see
it:

- **Entitled**: any active, authenticated staff user, OR a customer with
  `access_permission: true`.
- **Not entitled** (anonymous, unverified, or `access_permission: false`
  customer): `price` comes back as the **literal string `"XXXX"`** (not
  `null`, not omitted - a string in a field that's normally a number), and
  `discount` is omitted entirely (`null`), so an unauthorized viewer
  can't even infer a discount exists.

Implication for an agent: **do not treat `"XXXX"` as a parse error or a
real value** - it's the expected shape for a masked price. If you need
real prices, the caller must supply a valid Bearer token for an entitled
account; there's no query parameter to force it. Optional auth on a GET
route is unusual - if you send no `Authorization` header at all here it's
not an error, you just get masked data back with a `200`.

---

## 5. Error response shape

Two shapes appear, and both use the key `detail`:

**Business logic / auth errors** (explicit `HTTPException` in the route,
and the global handler for anything unhandled):
```json
{ "detail": "Human-readable message" }
```
Status code varies by cause: `400` bad input/state (e.g. duplicate
email), `401` bad/missing/expired credentials, `403` authenticated but
not permitted (or unverified/deactivated - check the message text),
`404` not found, `429` too many failed sign-in attempts (see 1.7).
Unhandled server exceptions always come back as a
generic `500 {"detail": "Internal server error"}` - the real exception
never reaches the client (it's logged server-side / forwarded to
Telegram if configured), so don't expect stack traces or specific error
codes from a 500.

**Pydantic validation errors** (malformed/missing/out-of-range request
body - FastAPI's default behavior, unchanged in this API):
```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "user_name"],
      "msg": "String should have at least 2 characters",
      "input": "a"
    }
  ]
}
```
Status `422`. `detail` is a **list of objects** here, not a string -
if you're programmatically reading `detail`, check whether it's a string
or an array before deciding how to display/log it.

---

## 6. Endpoint reference

`Auth` column: **Public** = no token needed · `Any <type>` = any
logged-in, verified account of that type · a permission name = staff with
that boolean flag (see section 2) · `Self` = the acting account's own
record only.

### Staff auth - `/auth`
| Method & path | Auth | Body / notes |
|---|---|---|
| `POST /auth/login` | Public | form-encoded `username`+`password`; combined staff/customer login, see 1.1 |
| `POST /auth/google` | Public | JSON `{"credential": "<Google ID token>"}`; same response shape as `/auth/login`. Signs in an existing staff/customer with that (Google-verified) email, or creates a customer for it. See 1.6 |
| `GET /auth/verify-email?token=` | Public | returns HTML, not JSON (opened from an email link) |
| `POST /auth/resend-verification` | Public | JSON `{"email": "..."}` |
| `POST /auth/forgot-password` | Public | JSON `{"email": "..."}`; always returns the same generic message whether or not the email exists (no account enumeration) |
| `POST /auth/reset-password` | Public | JSON `{"token": "...", "new_password": "..."}` |

### Customer auth - `/auth/customer`
| Method & path | Auth | Body / notes |
|---|---|---|
| `POST /auth/customer/register` | Public | JSON `CustomerRegister` (name/email/address/phone/password, plus optional `date_of_birth`/`gender`); starts `access_permission=false`, `is_verified=false` |
| `POST /auth/customer/login` | Public | form-encoded `username`+`password`; customer-only |
| `GET /auth/customer/verify-email?token=` | Public | returns HTML |
| `POST /auth/customer/resend-verification` | Public | JSON `{"email": "..."}` |
| `POST /auth/customer/forgot-password` | Public | JSON `{"email": "..."}` |
| `POST /auth/customer/reset-password` | Public | JSON `{"token": "...", "new_password": "..."}` |

### Staff self-service - `/users/me`
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /users/me` | Any user | - |
| `PUT /users/me` | Any user (verified) | JSON `UserUpdateSelf` (`user_name`, `email`, `address`, `phone_num`, `date_of_birth`, `gender`), all optional; changing `email` flips `is_verified` back to `false` and re-sends a confirmation link - the account then can't hit verified-only endpoints (including this one again) until re-confirmed |
| `POST /users/me/change-password` | Any user (verified) | JSON `{"current_password", "new_password"}` |
| `POST /users/me/image` | Any user (verified) | multipart `file` |

### Staff management - `/users` (all require `user_management` except `/me` above)
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /users/` | `user_management` | query `skip`, `limit` (default 0/50) |
| `GET /users/{id}` | `user_management` | - |
| `POST /users/` | `user_management` | JSON `UserCreateByAdmin` (name/email/address/phone/password/role_title + 4 permission booleans, plus optional `date_of_birth`/`gender`); new account still needs email confirmation before login. Note this router builds the `User` from an **explicit field list**, not `**payload.model_dump()` - a new column added to `UserBase` won't persist on create until it's added there too |
| `PUT /users/{id}` | `user_management` | JSON `UserUpdateByAdmin` (adds `date_of_birth`/`gender` to the usual fields); you cannot set `user_management: false` on your own account (self-lockout guard) |
| `PUT /users/{id}/password` | `user_management` | JSON `{"new_password"}`; an admin directly setting **another** staff member's password - no `current_password` check (unlike `POST /users/me/change-password`), since the caller isn't the account owner |
| `DELETE /users/{id}` | `user_management` | soft-delete (`is_active=false`), not a real row deletion; you cannot deactivate your own account |

### Customer self-service - `/customers/me`
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /customers/me` | Any customer | - |
| `PUT /customers/me` | Any customer (verified) | JSON `CustomerSelfUpdate` (`customer_name`, `email`, `address`, `phone_num`, `date_of_birth`, `gender`); changing `email` re-triggers verification, same as staff |
| `POST /customers/me/change-password` | Any customer (verified) | JSON `{"current_password", "new_password"}`; fails with 400 if the customer has no password (i.e. was created by staff, never self-registered) |
| `POST /customers/me/image` | Any customer (verified) | multipart `file` |

### Customer management - `/customers` (all require `customer_management`)
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /customers/` | `customer_management` | query `skip`, `limit`, `q` (searches name/email, case-insensitive substring) |
| `GET /customers/{id}` | `customer_management` | - |
| `POST /customers/` | `customer_management` | JSON `CustomerCreate`; **no password field** - this creates a record that cannot log in until the customer separately self-registers, or rather, cannot ever gain login this way at all (self-registration is a distinct email-keyed row check) |
| `PUT /customers/{id}` | `customer_management` | JSON `CustomerUpdate`, all optional including `access_permission` - this is the only way a customer's price visibility gets turned on |
| `POST /customers/{id}/image` | `customer_management` | multipart `file` |
| `DELETE /customers/{id}` | `customer_management` | **hard delete**, unlike users - returns `204` with no body |

**Both** `Customer` and `User` carry the same two optional demographic columns,
`date_of_birth` (ISO `"YYYY-MM-DD"`, or `null`) and `gender` (`"male"` /
`"female"` / `"other"`, or `null`). They're settable on create and on every
update path (`PUT /customers/{id}`, `PUT /customers/me`, `PUT /users/{id}`,
`PUT /users/me`), they're never required, and passing an explicit `null` clears
one. Validation is shared - see the `Gender` / `DateOfBirth` aliases near the
top of `app/schemas.py`, not a per-class `@field_validator`.

### Brands - `/brands`
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /brands/` | Public | query `skip`, `limit` |
| `GET /brands/{id}` | Public | - |
| `POST /brands/` | `product_management` | multipart: `brand_name` (form field) + optional `file` |
| `PUT /brands/{id}` | `product_management` | JSON `{"brand_name": "..."}`; does not touch the image |
| `POST /brands/{id}/image` | `product_management` | multipart `file` |
| `DELETE /brands/{id}` | `product_management` | `400` if any `Product` still references this brand (FK restrict, not cascade) |

### Categories - `/categories`
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /categories/` | Public | query `skip`, `limit` |
| `GET /categories/{id}` | Public | - |
| `POST /categories/` | `product_management` | multipart: `category_name` (form field) + optional `file` |
| `PUT /categories/{id}` | `product_management` | JSON `{"category_name": "..."}`; does not touch the image |
| `POST /categories/{id}/image` | `product_management` | multipart `file` |
| `DELETE /categories/{id}` | `product_management` | `400` if any `Product` still references this category (FK restrict, not cascade) |

### Products - `/products`
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /products/` | Public | query `skip`, `limit`, `brand_id`, `category_id`, `q` (name substring); price masking applies, see section 4 |
| `GET /products/{id}` | Public | same masking |
| `POST /products/` | `product_management` | JSON `ProductCreate` (`product_name`, `description?`, `badge?`, `product_code?` (SKU, must be globally unique or `400`), `uom?` (unit of measure, free text e.g. `"pcs"`/`"box"`), `price` >0, `discount?` (integer percent, `0`-`100`, defaults `0`), `brand_id` - must reference an existing brand or `400`, `category_id?` - must reference an existing category or `400`, `free_items?` - products given away free with this one, see "Bundle contents" below) |
| `PUT /products/{id}` | `product_management`, **+`price_listing` if the body includes `price` or `discount`** | JSON `ProductUpdate`, all fields optional |
| `PATCH /products/{id}/price` | `price_listing` only | JSON `{"price"?, "discount"?}` - use this instead of `PUT` if the caller only has `price_listing` (it can't touch `free_items` - use `PUT` for that) |
| `POST /products/{id}/image` | `product_management` | multipart `file` - the **primary** picture (`product_image`) |
| `POST /products/{id}/gallery` | `product_management` | multipart `files` (repeat the field for each file, ≤12 per request) - **appends** extra photos, see below |
| `DELETE /products/{id}/gallery/{image_id}` | `product_management` | removes one gallery photo; `404` if that image id isn't on that product |
| `DELETE /products/{id}` | `product_management` | cascades: deletes the product's `Manual`s and gallery images too, and drops it from any bundle/free-item list it appears in |

**Product photos come in two kinds.** `product_image` is the single primary
picture - it's what the catalog card, the cart, the printed quote and the
Telegram alert show, and it's the first frame of the storefront gallery.
`images` (read-only on `ProductOut`, `[{"id", "image", "sort_order"}, ...]`) are
the *additional* photos shown on the product detail page, and the primary one is
**not** repeated in that list. Gallery uploads append (posting 3 files to a
product that has 2 leaves it with 5) and are ordered by `sort_order`, assigned
`max(existing) + 1` at upload; deleting one never renumbers the rest. Unlike
`product_image`, gallery files keep uuid names, so re-uploading doesn't
overwrite. Not price-masked - a photo isn't a price.

### Manuals - `/manuals`
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /manuals/` | Public | query `skip`, `limit`, `product_id` |
| `GET /manuals/{id}` | Public | - |
| `POST /manuals/` | `product_management` | multipart: `product_id` (form field, must exist or `400`), optional `description`, optional `file` (PDF) |
| `PUT /manuals/{id}` | `product_management` | JSON `ManualUpdate` (`description?`, `product_id?`) |
| `POST /manuals/{id}/image` | `product_management` | multipart `file` (this is a thumbnail/illustration image, separate from the PDF) |
| `POST /manuals/{id}/pdf` | `product_management` | multipart `file` (must be `application/pdf`) |
| `DELETE /manuals/{id}` | `product_management` | - |

### Promotions - `/promotions`
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /promotions/` | Public | query `skip`, `limit`, `active_only` (bool - filters to `start_date <= now <= end_date`); price masking applies, see section 4 |
| `GET /promotions/{id}` | Public | price masking applies, see section 4 |
| `POST /promotions/` | `product_management` | JSON `PromotionCreate` (`promotion_name`, `description?`, `price` >0, `old_price?` >0, `start_date`, `end_date` - must be after `start_date` or `422`, `items?` - see "Bundle contents" below) |
| `PUT /promotions/{id}` | `product_management` | JSON `PromotionUpdate`, all optional; if you change only one of `start_date`/`end_date`, the other's current value is still validated against it |
| `DELETE /promotions/{id}` | `product_management` | - |

**Note**: `Promotion.price`/`old_price` are masked the same way as
`Product` prices - unauthenticated/unentitled callers get `price` as the
literal string `"XXXX"` and `old_price` as `null`; see section 4.

### Sets - `/sets`
A `Set` is a bundle deal shown on the storefront's Promotions page,
alongside `Promotion`. Same shape as `Promotion` minus `start_date`/
`end_date` - a set is never time-boxed, it's always on sale.

Unlike `Promotion`, a set can be filed under a `Brand` (`brand_id`, added
2026-08-13), which is what the Promotions page's brand strip filters on. It
is **optional** - a mixed-brand bundle has no single brand and `SetOut.brand`
comes back `null` for it. A brand still assigned to a set can't be deleted
(400 from `DELETE /brands/{id}`), same as one still assigned to a product.

| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /sets/` | Public | query `skip`, `limit`, `brand_id`; price masking applies, see section 4 |
| `GET /sets/{id}` | Public | price masking applies, see section 4 |
| `POST /sets/` | `product_management` | JSON `SetCreate` (`set_name`, `description?`, `price` >0, `old_price?` >0, `brand_id?`, `items?` - see "Bundle contents" below) |
| `PUT /sets/{id}` | `product_management` | JSON `SetUpdate`, all optional; `brand_id: null` clears the brand, omitting it leaves it alone |
| `POST /sets/{id}/image` | `product_management` | multipart `file` |
| `POST /sets/{id}/detail-image` | `product_management` | multipart `file` (the optional second image under the name/description) |
| `DELETE /sets/{id}` | `product_management` | - |

### Bundle contents - `Promotion.items` / `Set.items` / `Product.free_items`

**A `Promotion` and a `Set` are collections of products**, and a `Product`
may come with other products for free. All three use the same field shape:

- **Writing** (`POST`/`PUT`): `[{"product_id": 12, "qty": 2}, ...]`.
  `qty` defaults to `1`. A `400` comes back for an unknown `product_id`, the
  same product listed twice, or a product listed as its own freebie.
- **Reading** (`GET`): `[{"product_id", "product_name", "product_code",
  "uom", "qty"}, ...]` - name/code/uom are read from the **live** `Product`
  row, so renaming a product updates every bundle it appears in. Contents
  are **not** price-masked (what a deal contains isn't a price).
- **Update semantics**: omit the field to leave the contents alone; send it
  (**including `[]`**) to replace them wholesale.
- Deleting a `Product` removes it from every bundle (`ON DELETE CASCADE` on
  the join row) - it never blocks the delete, and never touches an
  already-placed order, which carries its own snapshot.
- The bundle's `price` is always the admin-entered bundle price. It is
  **never** summed from its members, and the members are never charged for -
  see "component lines" under Orders.
- **`old_price` on a bundle WITH contents is computed, not stored**: it's the
  members priced separately (each member's current `price` × its `qty`), which
  is what the customer would otherwise have paid. It therefore moves on its own
  whenever a member is repriced, and whatever is in the `old_price` column is
  ignored - that column is the fallback only for a bundle with no contents.
  This holds even when the contents add up to LESS than the bundle price; the
  figure stays truthful about what's inside, and it can't become a negative
  discount, because an order line only treats a positive
  `old_price - price` difference as one. The same computed figure is what an
  order line snapshots as its cash `discount`, so the printed quote's "UP
  before discount" shows it too.

### Orders - `/orders`
An `Order` row is a **quote or a real order** (`order_type`) - it only ever accepts
`{product_id|promotion_id|set_id}`+`qty` per line (exactly one id per line);
every other value (price, discount, `salesperson`, `quoted_by_name`,
`quote_code`, the computed discount, the KHQR payload) is derived/priced
server-side and never trusted from the request body.

| Method & path | Auth | Body / notes |
|---|---|---|
| `POST /orders/` | `Any user` with `price_listing` or `product_management`, OR `Any customer` with `access_permission` | JSON `OrderCreate` (`clinic_name`, `phone`, `address` - all **required**; `contact_person?`, `payment_term?`, `install_term?`; `payment_method`: `"cash"`\|`"khqr"` - **required for customers, ignored for staff**; `discount_type`: `"percent"`\|`"cash"`, default `"cash"`; `discount_value` ≥0, default `0`; `items`: list of `{product_id\|promotion_id\|set_id, qty}`, at least 1). See notes below. |
| `GET /orders/` | `price_listing` | query `skip`, `limit`, `status`, `customer_id` |
| `GET /orders/mine` | Same bar as `POST /orders/` (staff `price_listing`/`product_management`, or customer `access_permission`) | query `skip`, `limit`. The caller's OWN orders, scoped from the token (customer → `customer_id`, staff → `created_by_user_id`) - never from a query param. Exists because a customer has no `price_listing` and so can't use `GET /orders/` to see even their own history; powers the storefront's account drawer. Declared above `GET /{order_id}` so "mine" isn't parsed as an id. |
| `GET /orders/mine/{id}` | Same, plus must own the order | The caller's own order in full (line items included) - what the account drawer's detail view shows and re-prints its PDF from. **404, not 403**, on an order the caller doesn't own, so it can't be used to probe which ids exist. |
| `GET /orders/{id}` | `price_listing` | - |
| `GET /orders/{id}/payment-status` | The principal who placed the order, **or any `price_listing` staff** | KHQR orders only (`400` otherwise). Returns `{"payment_status": "unpaid"\|"paid"}`; while unpaid, each call asks the order's own provider about the transaction (PayWay by `tran_id`, or Bakong by `khqr_md5` when `BAKONG_API_TOKEN` is configured) and the first confirmed one flips the order to paid (stamping `paid_at`, firing the paid-order Telegram alert). Polled by the paying customer's browser, and by the admin Orders page's QR dialog. |
| `POST /orders/{id}/khqr` | `price_listing` | No body. Issues a scannable KHQR against an **existing** order (counter/phone sale) for its current `grand_total`, setting `payment_method: "khqr"`, `payment_status: "unpaid"`; returns the whole `OrderOut`. Idempotent - returns the stored payload if one exists. `400` if already paid or KHQR isn't configured. `order_type` is deliberately **not** changed. |
| `PUT /orders/{id}` | `price_listing` | JSON `OrderUpdate` - `status`, `payment_status`, `clinic_name`, `contact_person`, `phone`, `address`, `payment_term`, `install_term`, `discount_type`, `discount_value`, `items`. **`409` on a paid order** (see below). `items` REPLACES the line list and is re-priced server-side; a discount needs `product_management`; `payment_status: "paid"` stamps `paid_at` and fires the paid-order alert. |
| `DELETE /orders/{id}` | `price_listing` | hard delete, cascades to `OrderItem` rows. **`409` on a paid order.** |
| `POST /orders/{id}/quotation-pdf` | Same principal who placed the order | `multipart/form-data`, field `file` (a PDF) - see notes below |

Notes an agent should know before calling this:
- **`order_type` is derived, never sent**: a staff caller always creates a
  `"quote"` (their cart IS the quotation tool - `payment_method` is ignored
  for them and stored as `null`); a customer must send `payment_method` -
  `"cash"` also creates a `"quote"` (payment is collected offline later).
  **`"khqr"` creates nothing at all** - see the next bullet.
- **A customer never holds an unpaid order (changed 2026-08-11).**
  `POST /orders/` is the document endpoint and now **refuses**
  `payment_method: "khqr"` with a `400`. A pay-by-QR purchase goes to
  **`POST /orders/checkout`**, which prices the cart, issues the QR and
  writes a **`PendingCheckout`** - no `orders` row, no `order_items` rows,
  nothing in `GET /orders/mine`. The order is created, already `"paid"`, by
  whichever of these first sees the payment confirmed:
  **`GET /orders/checkout/{id}/payment-status`** (the browser poll, which
  returns the new order so it can render the receipt) or the background
  **reconciliation sweep** (`services/checkout_sweep.py`, every 60s, started
  from `main.py`'s lifespan). The sweep is not optional: the browser poll is
  the only client-side trigger, so without it a customer who pays and closes
  the tab would leave money received against no order. Both take a
  `SELECT ... FOR UPDATE` on the pending row and check `order_id IS NULL`
  before writing - one payment, one order. `_materialize_checkout` writes the
  order from the stored snapshot and never re-prices it: the customer paid a
  specific total for specific lines, and a product's price may have moved
  since. `orders.payment_reference` records which checkout it came from,
  because `order_number` is only assigned at payment time and is therefore
  *not* what the bank knows the transaction by.
- Two interchangeable providers produce the QR (see `settings.qr_provider`):
  **ABA PayWay** (`PAYWAY_MERCHANT_ID`/`PAYWAY_API_KEY`, wins when both are
  configured - generates the QR upstream, keeps `khqr_md5` NULL, and payment
  is checked by `tran_id` = the checkout's `reference`) or **Bakong-direct**
  (payload built locally by `services/khqr.py`, `khqr_md5` stored for Bakong's
  check_transaction_by_md5). `KHQR_PROVIDER` pins one explicitly; "auto"
  (default) prefers PayWay. The stored `khqr_md5`'s presence is how a
  checkout's (or a historical order's) provider is recognized. If neither is
  configured, a checkout request gets a `400` telling the customer to choose
  Cash.
- **Bakong-direct has two configuration shapes, and `BAKONG_ACCOUNT_ID` wins
  (changed 2026-08-11).** The payee lives in tag 29's three sub-fields, which
  map onto `BAKONG_ACCOUNT_ID` / `BAKONG_ACCOUNT_INFORMATION` /
  `BAKONG_ACQUIRING_BANK` - for a *bank account* (not a Bakong wallet alias)
  sub-00 holds the institution id and sub-01 the account number, so ABA is
  `abaakhppxxx@abaa` + `004613623`; building from sub-00 alone yields a QR
  naming the bank but no account. `KHQR_STATIC_TEMPLATE` (paste the bank app's
  whole static "receive money" payload; every tag in the EMV merchant-account
  range 26-51 is copied verbatim) is now only the fallback for when no account
  id is set: it also copies bank-proprietary tags - ABA's `abaP2P` tag 40 -
  which can route payment over the bank's own rail, where Bakong's
  check_transaction_by_md5 will never see it. `scripts/decode_khqr.py` dumps
  any KHQR (image or payload string) field-by-field and prints the three
  settings ready to paste; it's the fastest way to inspect either side.
- **Bakong has no QR-generation API.** NBC's SDKs assemble the EMV payload
  locally, which is exactly what `services/khqr.py` does; the open API at
  `api-bakong.nbc.gov.kh` only *verifies*. So `BAKONG_API_TOKEN` is needed by
  `check_bakong_payment()` alone - QR generation works without it, payment just
  has to be confirmed by hand. Dynamic QRs must carry an expiry (tag 99 sub-01,
  `KHQR_EXPIRY_MINUTES`); an expired one is refused by the payer's app and staff
  re-issue with `POST /orders/{id}/khqr`.
- **An order is editable until it is paid, then frozen.** `PUT /orders/{id}`
  accepts the clinic details, terms, order-level discount and the line list
  itself (`items` REPLACES the lines and is re-priced through the same code
  path a new order goes through - only ids and quantities are ever accepted).
  The moment `payment_status` is `"paid"`, every `PUT` and the `DELETE`
  return **`409`** - including a `status` change and setting `payment_status`
  back to `"unpaid"`. A receipt has been issued against those exact figures,
  so correcting a paid order means issuing a new document alongside it. An
  edit that moves `grand_total` clears any `khqr_string`/`khqr_md5` on the
  row, since the old QR would collect the wrong amount.
- **Any order can be marked paid**, not just KHQR ones - staff take cash at
  the counter against a quote. `payment_status` on a row with no
  `payment_method` is normal and means exactly that. (The payment-status
  *poll* still 400s on a non-KHQR row: there is no QR to ask about.)
- **Receipts vs. quotations**: the printed document is a Receipt whenever
  `payment_status == "paid"` - a confirmed KHQR payment or a quote staff
  marked paid - and a Quotation otherwise. Derived from that one field alone
  in all four places that print or announce it (the frontend's
  `buildPrintTemplate`, the account drawer, the admin reprint button, and
  `services/invoice_pdf.py`); `order_type`/`payment_method` only pick the
  wording of the paid note ("Paid via KHQR" vs "Paid in full").
- **`salesperson`/`quoted_by_name` are never accepted from the client** -
  `OrderCreate` doesn't even have those fields. They're derived from
  whoever's bearer token is calling: a staff `User` → their `user_name`
  for both; a `Customer` → `"Website"` for `salesperson`, but their own
  `customer_name` for `quoted_by_name` (that one's never overridden).
- **`quote_code`** ("C. Code" on the paper quotation form) is a readable
  `yymmddhhmmss` UTC timestamp generated server-side on every create (e.g.
  `"260722070145"`), "-N" suffixed on the rare same-second collision (the
  column is UNIQUE) - distinct from the sequential `order_number`.
- **A cash discount (`discount_type: "cash"`, `discount_value > 0`)
  requires `product_management` specifically**, not just
  `price_listing`/`access_permission` - a `403` here doesn't mean the
  caller can't place the order at all, just that they can't apply a cash
  discount to it (a percent discount is fine for anyone who can already
  place an order). Read the `detail` message to tell the two apart.
- **One submitted line can produce several `OrderItem` rows.** A
  `Promotion`/`Set` line expands into its member products, and a `Product`
  line into whatever that product comes with for free (see "Bundle
  contents" above). Those extra rows are **component lines**: they carry
  `parent_item_id` (the id of the paid line they belong to),
  `unit_price`/`discount`/`line_amount` of `0`, and `qty` multiplied by the
  parent's qty (2 sets containing 3 gloves → one component row with
  `qty: 6`). Because they're zero-priced by construction, they can't move
  `subtotal`, the discount base, or `grand_total` - and a client cannot
  fabricate one, since expansion happens entirely server-side from the
  bundle's current contents.
  `items` in the response is a **flat** list of both kinds, ordered for
  display: each paid line immediately followed by its own components. Group
  or indent by `parent_item_id`; a line with `parent_item_id: null` is a
  real, charged line.
- **`Promotion`/`Set` lines are excluded from the discount calculation
  entirely** - both percent and cash discounts are computed only against
  the subtotal of `Product` lines, then subtracted from the full order
  subtotal. A quote mixing a promotion/set and a regular product will show
  a smaller discount than `discount_value`% of the full subtotal would
  suggest - that's expected, not a bug. (Prior to 2026-07-27, this
  exemption was instead driven by a now-removed `Product.product_type ==
  "promotional"` value - `Product` lines have no such exemption anymore,
  every `Product` line participates in the discount base.)
- `discount_amount` in the response is the actual computed $ figure
  already subtracted (`grand_total = subtotal - discount_amount`) - don't
  recompute it client-side from `discount_type`/`discount_value`, the
  server-persisted value is authoritative (and is what a re-print of an
  old order should always display).
- **`POST /orders/{id}/quotation-pdf` is how the browser hands over the real,
  client-rendered quotation PDF** (built by `main.js`'s
  `QuoteCart.buildPrintTemplate`/`exportPDF` via html2canvas, right after `POST
  /orders/` succeeds) so the Telegram order alert can carry the exact document the
  customer received. `create_order`'s background task
  (`deliver_order_alert` in `services/telegram.py`) waits up to ~20s for this call
  before falling back to a server-rendered approximation
  (`services/invoice_pdf.py`) - so this endpoint is best-effort: a slow/missing call
  never fails the purchase itself, it just means the Telegram alert uses the
  fallback PDF instead of the real one.

### Site settings - `/settings`

Admin-editable, site-wide configuration. The catalogue of what exists - key, type,
default, group, label - is declared in `app/core/settings_spec.py`, which is the single
source of truth: the API validates against it and the Flask admin form is generated from
it. Adding a setting is a change to that file only, with **no migration**.

| Method & path | Auth | Notes |
|---|---|---|
| `GET /settings/public` | **Public** | Only the keys the spec marks `public`. The storefront footer, contact page and printed-quote letterhead read from this, and an anonymous visitor has no token |
| `GET /settings/` | `admin` | `{values, defaults, groups, status}` - `groups` is the form spec, `status` is the read-only integrations panel |
| `PUT /settings/` | `admin` | Body `{"values": {key: value}}`, partial. Coerced to the declared type (`"45"` → `45`, `"on"` → `true`) |
| `POST /settings/reset` | `admin` | Body `{"group": "store"}` and/or `{"keys": [...]}`. Deletes the overrides so those keys read as their defaults again |

Things worth knowing:

- **Only overrides are stored.** `app_settings` is a key/value table that starts empty;
  a key with no row reads as its spec default. So "reset" is a `DELETE`, and saving a
  value that *equals* the default also deletes the row rather than storing a copy.
- **A rejected value saves nothing.** Every value in the payload is coerced before
  anything is written, so one bad field fails the whole request with `400` - no
  half-saved form. The message names the field by its human label
  (`"Quote validity (days) must be at least 1"`), not by key.
- **No secrets live here.** PayWay / Bakong / Telegram / SMTP / R2 / Google credentials
  stay in the environment (`app/config.py`). `GET /settings/` reports whether each is
  configured, under `status`, and never returns a value.
- **Values are cached** for 30s process-wide (`app/services/app_settings.py`), and
  invalidated immediately on write. The Flask app caches `GET /settings/public` for 60s
  on its side and clears that on save too.
- Changing anything in the `document` group affects **both** printed-document engines -
  `services/invoice_pdf.py` here and `buildPrintTemplate()` in the website's `main.js`.
  They must stay in step.

### Misc
| Method & path | Auth | Notes |
|---|---|---|
| `GET /health` | Public | `{"status": "ok"}` liveness check, no DB touch |

---

## 7. Field constraints worth knowing before you build a payload

(From `app/schemas.py` - violating these gets a `422`, not a `400`.)

- **`Product.list_price` is the price BEFORE the discount; `price` is what's
  actually charged.** Both are stored. `list_price` is optional on create/update:
  omit it and the server derives one from `price` + `discount`, send it and it's
  stored verbatim (it must be `>= price`, or `400`). Two rules to know before
  writing a price:
  - **Updating `price` alone does NOT move `list_price`** - it stays where it is,
    and `discount` is re-derived from the gap between the two. That's deliberate:
    repricing an item says what it now sells for, not what it used to be worth.
    Send `list_price` explicitly whenever you mean to change it.
  - An explicitly sent `discount` always wins over the derived one.

  `list_price` is **price-masked exactly like `price`** - unentitled viewers get
  `null` (see section 4), so don't treat its absence as a missing field.
- **`OrderItem.list_price`** is the same figure snapshotted at order time. Use it
  for a printed "UP before discount" column; don't recompute it from
  `unit_price`/`discount`.
- **`updated_at` / `updated_by`** appear on every entity (`users`, `customers`,
  `brands`, `categories`, `products`, `manuals`, `promotions`, `sets`, `orders`).
  `updated_by` is `{id, user_name}` or `null`. It records the staff member who
  last wrote to the row - **overwritten by the next write to any field**, and it
  never says what changed, so don't read it as a price-change audit trail. `null`
  is normal: a customer editing their own profile isn't a `User`, and rows
  predating the column have nobody recorded.
- **Pagination**: every list endpoint's `skip` must be `>= 0` and `limit` must
  be `1`-`500` (`MAX_PAGE_SIZE` in `app/core/query.py`). To walk a table larger
  than 500 rows, page with `skip`; there is no way to ask for it all at once.
- **`qty`** on an order line and inside a bundle's `items`/`free_items` is
  capped at `100000` (`MAX_QTY`). The ceiling exists because every money column
  is `Numeric(10, 2)`, so a larger quantity would overflow `price x qty` in the
  database rather than fail cleanly.
- Passwords (`password`, `new_password`): 8-72 characters.
- `user_name`: 2-100 chars. `customer_name`: 2-150 chars.
- `date_of_birth` (on both `User` and `Customer`): a plain date, not a
  datetime. Must not be in the future and must be on or after `1900-01-01`
  (both bounds only exist to catch a mistyped year). `gender` must be exactly
  one of `"male"`, `"female"`, `"other"` - lowercase.
- `email` fields: validated as real email syntax (`EmailStr`) - and note
  reserved test TLDs (`.test`, `.example`, `.invalid`, `.localhost`) are
  **rejected** by the validator. Use a realistic-looking domain even for
  throwaway test data.
- `price` on `Product`, `Promotion`, and `Set`, and `Promotion.old_price`/
  `Set.old_price`: must be `> 0` (not `>= 0`) wherever settable - a
  free/zero-price item isn't representable. `Product.discount` is a
  `0`-`100` integer percent instead, not a price.
- `Promotion.end_date` must be strictly after `start_date`, enforced both
  in the schema (on create) and again in the router (on update, against
  whichever of the two values ends up in effect). `Set` has no dates at
  all - it's never time-boxed.
- IDs (`brand_id`, `category_id`, `product_id`, `promotion_id`, `set_id`)
  referenced in create/update payloads are checked for existence
  server-side and rejected with `400` if dangling - don't pre-validate them
  client-side beyond that. `category_id` is the exception that's optional
  (`null` allowed).
- `OrderCreate.discount_value` must be ≤100 when `discount_type ==
  "percent"` (checked by a `field_validator`, so this is also a `422` not
  a `400`) - there's no such cap when `discount_type == "cash"`, since a
  cash amount just gets clamped to the discountable subtotal instead of
  rejected (see the Orders section).

---

## 8. Common agent mistakes to avoid

1. **Sending JSON to `/auth/login`.** It's form-encoded. This is the
   single most common integration mistake - a JSON body there gets a
   `422` complaining about missing `username`/`password` fields, which
   reads like a bug in the request rather than the encoding.
2. **Treating `"XXXX"` as an error.** It's the intentional masked-price
   sentinel for unauthorized viewers - see section 4.
3. **Assuming `role_title` drives permissions.** It doesn't - check the
   four boolean flags (section 2).
4. **Assuming a 403 always means "wrong permission."** It can also mean
   unverified email or a deactivated account - read `detail`.
5. **Forgetting the trailing slash** on collection routes
   (`POST /products/`, not `POST /products`) - FastAPI will otherwise
   redirect or 404 depending on client redirect handling.
6. **Trying to fetch `/docs`, `/redoc`, or `/openapi.json`.** They're
   disabled on this server; use this file instead.
7. **Assuming `POST /customers/` gives the customer login access.** It
   creates a passwordless record; only `POST /auth/customer/register`
   creates a login-capable customer account.
8. **Using `PUT /products/{id}` to only change price with just
   `price_listing`.** That path requires `product_management` too - use
   `PATCH /products/{id}/price` instead.

If a request behaves unexpectedly and none of the above explains it, the
most useful next step is usually to read the `detail` field of the error
response verbatim rather than guessing - the messages in this API are
written to be specific (e.g. naming the exact missing permission or the
exact reason a field was rejected).
