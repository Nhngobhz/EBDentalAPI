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
- **All 7 "physical" entities**: `User` (staff), `Customer`, `Brand`,
  `Category`, `Product`, `Manual`, `Promotion`. There is no `Order`/`Cart`
  model in this API - it's a catalog + account management backend, not a
  checkout/POS system.

---

## 1. Authentication

### 1.1 Token endpoints (OAuth2 "password" flow)

Three endpoints issue a JWT access token. All three expect
`application/x-www-form-urlencoded` (the standard OAuth2 password-grant
shape), **not JSON** - field names are fixed by the OAuth2 spec:

| Endpoint | Who it authenticates |
|---|---|
| `POST /auth/login` | Either staff or customer - tries `User` first, falls back to `Customer` |
| `POST /auth/customer/login` | Customer only |

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

---

## 2. Authorization model (permissions)

Staff (`User`) authorization is **not** role-based despite the
`role_title` field existing - `role_title` (e.g. "Sales Manager") is a
free-text display label only and is never checked by any endpoint. Actual
authorization comes from four independent boolean flags on the `User`
row, checked directly:

| Permission | Grants |
|---|---|
| `user_management` | Create/edit/deactivate staff (`User`) accounts, view the staff list |
| `customer_management` | Full CRUD on `Customer` records, including toggling `access_permission` |
| `product_management` | CRUD on `Brand`, `Category`, `Product` (non-price fields), `Manual` |
| `price_listing` | Set `price`/`old_price` on `Product`, full CRUD on `Promotion` |

Notes an agent should know before assuming a 403 is a bug:

- These flags are **independent**, not a hierarchy - `user_management`
  does not imply the other three. A user with all four `true` is a
  de-facto super-admin; there is no separate `is_superuser` flag.
- Changing an **existing** product's `price`/`old_price` via the general
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

Everything else - including `PUT /brands/{id}` / `PUT /categories/{id}`
(metadata-only update, image unchanged) - is plain JSON.

File upload constraints (`app/core/files.py`): images must be
`image/jpeg`, `image/png`, `image/webp`, or `image/gif` and ≤5MB;
PDFs must be `application/pdf` and ≤20MB. A rejected upload returns `400`
with the reason in `detail`, not a generic validation error - check the
file's actual `Content-Type` header if this happens unexpectedly.

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
  `old_price` is omitted entirely (`null`), so an unauthorized viewer
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
`404` not found. Unhandled server exceptions always come back as a
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
| `GET /auth/verify-email?token=` | Public | returns HTML, not JSON (opened from an email link) |
| `POST /auth/resend-verification` | Public | JSON `{"email": "..."}` |
| `POST /auth/forgot-password` | Public | JSON `{"email": "..."}`; always returns the same generic message whether or not the email exists (no account enumeration) |
| `POST /auth/reset-password` | Public | JSON `{"token": "...", "new_password": "..."}` |

### Customer auth - `/auth/customer`
| Method & path | Auth | Body / notes |
|---|---|---|
| `POST /auth/customer/register` | Public | JSON `CustomerRegister` (name/email/address/phone/password); starts `access_permission=false`, `is_verified=false` |
| `POST /auth/customer/login` | Public | form-encoded `username`+`password`; customer-only |
| `GET /auth/customer/verify-email?token=` | Public | returns HTML |
| `POST /auth/customer/resend-verification` | Public | JSON `{"email": "..."}` |
| `POST /auth/customer/forgot-password` | Public | JSON `{"email": "..."}` |
| `POST /auth/customer/reset-password` | Public | JSON `{"token": "...", "new_password": "..."}` |

### Staff self-service - `/users/me`
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /users/me` | Any user | - |
| `PUT /users/me` | Any user (verified) | JSON `UserUpdateSelf`, all fields optional; changing `email` flips `is_verified` back to `false` and re-sends a confirmation link - the account then can't hit verified-only endpoints (including this one again) until re-confirmed |
| `POST /users/me/change-password` | Any user (verified) | JSON `{"current_password", "new_password"}` |
| `POST /users/me/image` | Any user (verified) | multipart `file` |

### Staff management - `/users` (all require `user_management` except `/me` above)
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /users/` | `user_management` | query `skip`, `limit` (default 0/50) |
| `GET /users/{id}` | `user_management` | - |
| `POST /users/` | `user_management` | JSON `UserCreateByAdmin` (name/email/address/phone/password/role_title + 4 permission booleans); new account still needs email confirmation before login |
| `PUT /users/{id}` | `user_management` | JSON `UserUpdateByAdmin`; you cannot set `user_management: false` on your own account (self-lockout guard) |
| `DELETE /users/{id}` | `user_management` | soft-delete (`is_active=false`), not a real row deletion; you cannot deactivate your own account |

### Customer self-service - `/customers/me`
| Method & path | Auth | Body / notes |
|---|---|---|
| `GET /customers/me` | Any customer | - |
| `PUT /customers/me` | Any customer (verified) | JSON `CustomerSelfUpdate`; changing `email` re-triggers verification, same as staff |
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
| `GET /products/` | Public | query `skip`, `limit`, `brand_id`, `category_id`, `product_type` (`"single"`/`"combo"`), `q` (name substring); price masking applies, see section 4 |
| `GET /products/{id}` | Public | same masking |
| `POST /products/` | `product_management` | JSON `ProductCreate` (`product_name`, `description?`, `badge?`, `product_type?` - `"single"`/`"combo"`, defaults `"single"` -, `price` >0, `old_price?` >0, `brand_id` - must reference an existing brand or `400`, `category_id?` - must reference an existing category or `400`) |
| `PUT /products/{id}` | `product_management`, **+`price_listing` if the body includes `price` or `old_price`** | JSON `ProductUpdate`, all fields optional |
| `PATCH /products/{id}/price` | `price_listing` only | JSON `{"price"?, "old_price"?}` - use this instead of `PUT` if the caller only has `price_listing` |
| `POST /products/{id}/image` | `product_management` | multipart `file` |
| `DELETE /products/{id}` | `product_management` | cascades: deletes the product's `Manual`s too |

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
| `GET /promotions/` | Public | query `skip`, `limit`, `active_only` (bool - filters to `start_date <= now <= end_date`) |
| `GET /promotions/{id}` | Public | - |
| `POST /promotions/` | `price_listing` | JSON `PromotionCreate` (`promotion_name`, `description?`, `price` >0, `old_price?` >0, `start_date`, `end_date` - must be after `start_date` or `422`) |
| `PUT /promotions/{id}` | `price_listing` | JSON `PromotionUpdate`, all optional; if you change only one of `start_date`/`end_date`, the other's current value is still validated against it |
| `DELETE /promotions/{id}` | `price_listing` | - |

**Note**: `Promotion.price`/`old_price` are **not** masked by
`access_permission` the way `Product` prices are - promotions are always
returned with real numbers to any caller, public or not. Don't assume the
same masking rule from section 4 applies here.

### Misc
| Method & path | Auth | Notes |
|---|---|---|
| `GET /health` | Public | `{"status": "ok"}` liveness check, no DB touch |

---

## 7. Field constraints worth knowing before you build a payload

(From `app/schemas.py` - violating these gets a `422`, not a `400`.)

- Passwords (`password`, `new_password`): 8-72 characters.
- `user_name`: 2-100 chars. `customer_name`: 2-150 chars.
- `email` fields: validated as real email syntax (`EmailStr`) - and note
  reserved test TLDs (`.test`, `.example`, `.invalid`, `.localhost`) are
  **rejected** by the validator. Use a realistic-looking domain even for
  throwaway test data.
- `price` / `old_price` on `Product` and `Promotion`: must be `> 0` (not
  `>= 0`) wherever settable - a free/zero-price item isn't representable.
- `Promotion.end_date` must be strictly after `start_date`, enforced both
  in the schema (on create) and again in the router (on update, against
  whichever of the two values ends up in effect).
- IDs (`brand_id`, `category_id`, `product_id`) referenced in create/update
  payloads are checked for existence server-side and rejected with `400`
  if dangling - don't pre-validate them client-side beyond that.
  `category_id` is the exception that's optional (`null` allowed).
- `Product.product_type` only accepts `"single"` or `"combo"` (see
  `ProductType` in `app/schemas.py`) - anything else is a `422`, not a
  `400`, since it's a schema-level literal check rather than a DB lookup.

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
