from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from tests.conftest import auth_header, customer_auth_header, make_admin, make_customer


def test_health_requires_telegram_bot_token(client):
    resp = client.get("/health")
    assert resp.status_code == 404


def test_health(client, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    resp = client.get("/health", headers={"X-Telegram-Bot-Token": "test-bot-token"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    resp = client.get("/health", headers={"X-Telegram-Bot-Token": "wrong-token"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Registration / email confirmation / login
# ---------------------------------------------------------------------------
def test_public_staff_registration_is_disabled(client):
    """POST /auth/register no longer exists - staff accounts can only be
    created by an existing admin via POST /users/ (user_management)."""
    resp = client.post(
        "/auth/register",
        json={"user_name": "Jane Staff", "email": "jane@example.com", "password": "supersecret1"},
    )
    assert resp.status_code == 404


def test_admin_created_staff_then_verify_and_login_flow(client, db_session):
    make_admin(db_session, email="staffadmin@example.com", password="password123")
    headers = auth_header(client, "staffadmin@example.com", "password123")

    resp = client.post(
        "/users/",
        json={"user_name": "Jane Staff", "email": "jane@example.com", "password": "supersecret1"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_verified"] is False
    assert body["user_management"] is False  # no permissions by default

    # Cannot log in before verifying email
    resp = client.post("/auth/login", data={"username": "jane@example.com", "password": "supersecret1"})
    assert resp.status_code == 403
    assert "confirm your email" in resp.json()["detail"].lower()

    # Grab the verification token straight from the DB (stand-in for
    # "clicking the link in the email", since no real SMTP is configured)
    from app.models import User

    user = db_session.query(User).filter(User.email == "jane@example.com").first()
    assert user.verification_token is not None

    resp = client.get(f"/auth/verify-email?token={user.verification_token}")
    assert resp.status_code == 200

    # Now login succeeds
    resp = client.post("/auth/login", data={"username": "jane@example.com", "password": "supersecret1"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data
    assert data["user"]["email"] == "jane@example.com"


def test_login_wrong_password(client, db_session):
    make_admin(db_session, email="bob@example.com", password="correctpass1")
    resp = client.post("/auth/login", data={"username": "bob@example.com", "password": "wrongpass"})
    assert resp.status_code == 401


def test_duplicate_staff_email_rejected(client, db_session):
    make_admin(db_session, email="dupadmin@example.com", password="password123")
    headers = auth_header(client, "dupadmin@example.com", "password123")

    payload = {"user_name": "Ann", "email": "dup@example.com", "password": "password123"}
    r1 = client.post("/users/", json=payload, headers=headers)
    assert r1.status_code == 201
    r2 = client.post("/users/", json=payload, headers=headers)
    assert r2.status_code == 400


def test_password_reset_flow(client, db_session):
    make_admin(db_session, email="reset@example.com", password="oldpassword1")
    resp = client.post("/auth/forgot-password", json={"email": "reset@example.com"})
    assert resp.status_code == 200

    from app.models import User

    user = db_session.query(User).filter(User.email == "reset@example.com").first()
    assert user.reset_token is not None

    resp = client.post(
        "/auth/reset-password", json={"token": user.reset_token, "new_password": "newpassword1"}
    )
    assert resp.status_code == 200

    resp = client.post("/auth/login", data={"username": "reset@example.com", "password": "newpassword1"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AuthN/AuthZ
# ---------------------------------------------------------------------------
def test_protected_endpoint_requires_token(client):
    resp = client.get("/users/")
    assert resp.status_code == 401


def test_permission_denied_for_non_admin(client, db_session):
    from app.models import User

    plain_user = User(
        user_name="No Perms",
        email="noperm@example.com",
        hashed_password="x",
        role_title="Staff",
        is_active=True,
        is_verified=True,
    )
    from app.core.security import hash_password

    plain_user.hashed_password = hash_password("password123")
    db_session.add(plain_user)
    db_session.commit()

    headers = auth_header(client, "noperm@example.com", "password123")
    resp = client.get("/users/", headers=headers)
    assert resp.status_code == 403
    assert "user_management" in resp.json()["detail"]


def test_admin_cannot_revoke_own_user_management(client, db_session):
    admin = make_admin(db_session, email="selfadmin@example.com", password="password123")
    headers = auth_header(client, "selfadmin@example.com", "password123")
    resp = client.put(
        f"/users/{admin.id}", json={"user_management": False}, headers=headers
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Brands / Products (FK relationship + permission split)
# ---------------------------------------------------------------------------
def test_product_requires_valid_brand(client, db_session):
    make_admin(db_session, email="catalog@example.com", password="password123")
    headers = auth_header(client, "catalog@example.com", "password123")

    resp = client.post("/products/", json={
        "product_name": "Widget",
        "price": "9.99",
        "brand_id": 9999,
    }, headers=headers)
    assert resp.status_code == 400


def test_product_rejects_invalid_category_id(client, db_session):
    make_admin(db_session, email="catalog3@example.com", password="password123")
    headers = auth_header(client, "catalog3@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "CatTestCo"}, headers=headers).json()["id"]

    resp = client.post("/products/", json={
        "product_name": "Widget",
        "price": "9.99",
        "brand_id": brand_id,
        "category_id": 9999,
    }, headers=headers)
    assert resp.status_code == 400


def test_full_catalog_crud_and_public_reads(client, db_session):
    make_admin(db_session, email="catalog2@example.com", password="password123")
    headers = auth_header(client, "catalog2@example.com", "password123")

    brand_resp = client.post("/brands/", data={"brand_name": "Acme"}, headers=headers)
    assert brand_resp.status_code == 201, brand_resp.text
    brand_id = brand_resp.json()["id"]

    category_resp = client.post("/categories/", data={"category_name": "Footwear"}, headers=headers)
    assert category_resp.status_code == 201, category_resp.text
    category_id = category_resp.json()["id"]

    product_resp = client.post(
        "/products/",
        json={
            "product_name": "Rocket Skates",
            "description": "Fast.",
            "price": "199.99",
            "discount": 20,
            "brand_id": brand_id,
            "category_id": category_id,
            "badge": "New",
        },
        headers=headers,
    )
    assert product_resp.status_code == 201, product_resp.text
    product = product_resp.json()
    assert product["brand"]["brand_name"] == "Acme"
    assert product["category"]["category_name"] == "Footwear"

    manual_resp = client.post(
        "/manuals/",
        data={"product_id": product["id"], "description": "How to not explode."},
        headers=headers,
    )
    assert manual_resp.status_code == 201, manual_resp.text

    # Public (unauthenticated) reads should work with no token at all
    public_client_resp = client.get("/products/")
    assert public_client_resp.status_code == 200
    assert len(public_client_resp.json()) == 1

    public_brand_resp = client.get(f"/brands/{brand_id}")
    assert public_brand_resp.status_code == 200

    public_category_resp = client.get(f"/categories/{category_id}")
    assert public_category_resp.status_code == 200

    # Deleting a brand that still has a product should be rejected (RESTRICT)
    del_resp = client.delete(f"/brands/{brand_id}", headers=headers)
    assert del_resp.status_code == 400

    # Same RESTRICT behavior for a category that still has a product
    del_cat_resp = client.delete(f"/categories/{category_id}", headers=headers)
    assert del_cat_resp.status_code == 400

    # Deleting the product cascades to its manual
    del_product_resp = client.delete(f"/products/{product['id']}", headers=headers)
    assert del_product_resp.status_code == 204
    assert client.get(f"/manuals/{manual_resp.json()['id']}").status_code == 404

    # Now the (now product-less) brand/category can be deleted
    assert client.delete(f"/brands/{brand_id}", headers=headers).status_code == 204
    assert client.delete(f"/categories/{category_id}", headers=headers).status_code == 204


def test_price_listing_permission_required_for_price_changes(client, db_session):
    from app.models import User
    from app.core.security import hash_password

    # A user with product_management but NOT price_listing
    limited = User(
        user_name="Catalog Editor",
        email="editor@example.com",
        hashed_password=hash_password("password123"),
        role_title="Catalog Editor",
        is_active=True,
        is_verified=True,
        product_management=True,
        price_listing=False,
    )
    db_session.add(limited)
    db_session.commit()

    admin = make_admin(db_session, email="priceadmin@example.com", password="password123")
    admin_headers = auth_header(client, "priceadmin@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "PriceCo"}, headers=admin_headers).json()["id"]
    product = client.post(
        "/products/",
        json={"product_name": "Gadget", "price": "10.00", "brand_id": brand_id},
        headers=admin_headers,
    ).json()

    editor_headers = auth_header(client, "editor@example.com", "password123")

    # Non-price field: allowed
    resp = client.put(
        f"/products/{product['id']}", json={"badge": "Sale"}, headers=editor_headers
    )
    assert resp.status_code == 200, resp.text

    # Price field via general update: forbidden without price_listing
    resp = client.put(
        f"/products/{product['id']}", json={"price": "5.00"}, headers=editor_headers
    )
    assert resp.status_code == 403

    # Dedicated price endpoint: also forbidden without price_listing
    resp = client.patch(
        f"/products/{product['id']}/price", json={"price": "5.00"}, headers=editor_headers
    )
    assert resp.status_code == 403

    # Admin (who has price_listing) can do it
    resp = client.patch(
        f"/products/{product['id']}/price", json={"price": "5.00"}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["price"] == "5.00"


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
def test_customer_crud(client, db_session):
    make_admin(db_session, email="custadmin@example.com", password="password123")
    headers = auth_header(client, "custadmin@example.com", "password123")

    resp = client.post(
        "/customers/",
        json={"customer_name": "Acme Corp", "email": "buyer@acmecorp.com", "access_permission": True},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    customer_id = resp.json()["id"]

    resp = client.get("/customers/", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = client.put(
        f"/customers/{customer_id}", json={"access_permission": False}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["access_permission"] is False

    # Customers endpoints are NOT public
    assert client.get("/customers/").status_code == 401


# ---------------------------------------------------------------------------
# Customer self-service auth
# ---------------------------------------------------------------------------
def test_customer_register_then_login_flow(client, db_session):
    resp = client.post(
        "/auth/customer/register",
        json={
            "customer_name": "Jane Shopper",
            "email": "jane.shopper@example.com",
            "password": "supersecret1",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_verified"] is False
    assert body["access_permission"] is False  # no price access by default

    # Cannot log in before verifying email
    resp = client.post(
        "/auth/customer/login", data={"username": "jane.shopper@example.com", "password": "supersecret1"}
    )
    assert resp.status_code == 403

    from app.models import Customer

    customer = db_session.query(Customer).filter(Customer.email == "jane.shopper@example.com").first()
    assert customer.verification_token is not None

    resp = client.get(f"/auth/customer/verify-email?token={customer.verification_token}")
    assert resp.status_code == 200

    resp = client.post(
        "/auth/customer/login", data={"username": "jane.shopper@example.com", "password": "supersecret1"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data
    assert data["customer"]["email"] == "jane.shopper@example.com"


def test_combined_login_accepts_customer_credentials(client, db_session):
    """POST /auth/login (originally staff-only) now also accepts customer
    credentials, falling back to the Customer table when no User matches."""
    make_customer(db_session, email="combined@example.com", password="customerpass1")

    resp = client.post(
        "/auth/login", data={"username": "combined@example.com", "password": "customerpass1"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["account_type"] == "customer"
    assert data["customer"]["email"] == "combined@example.com"
    assert data.get("user") is None


def test_combined_login_still_authenticates_staff(client, db_session):
    """The same endpoint still logs staff in as before, tagged account_type="user"."""
    make_admin(db_session, email="combinedstaff@example.com", password="adminpass123")

    resp = client.post(
        "/auth/login", data={"username": "combinedstaff@example.com", "password": "adminpass123"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["account_type"] == "user"
    assert data["user"]["email"] == "combinedstaff@example.com"
    assert data.get("customer") is None


def test_staff_created_customer_has_no_login(client, db_session):
    """A Customer created via POST /customers/ (staff-managed record, no
    password) must not be able to log in through /auth/customer/login."""
    make_admin(db_session, email="custadmin2@example.com", password="password123")
    headers = auth_header(client, "custadmin2@example.com", "password123")

    client.post(
        "/customers/",
        json={"customer_name": "Walk-in Customer", "email": "walkin@example.com"},
        headers=headers,
    )

    resp = client.post(
        "/auth/customer/login", data={"username": "walkin@example.com", "password": "anything"}
    )
    assert resp.status_code == 401


def test_customer_token_cannot_access_staff_endpoints_and_vice_versa(client, db_session):
    make_admin(db_session, email="staffonly@example.com", password="password123")
    staff_headers = auth_header(client, "staffonly@example.com", "password123")

    make_customer(db_session, email="custonly@example.com", password="password123")
    customer_headers = customer_auth_header(client, "custonly@example.com", "password123")

    # A customer token must not work as a staff token
    resp = client.get("/users/me", headers=customer_headers)
    assert resp.status_code == 401

    # A staff token must not work as a customer token
    resp = client.get("/customers/me", headers=staff_headers)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Self-service password/email changes
# ---------------------------------------------------------------------------
def test_user_can_change_own_password(client, db_session):
    make_admin(db_session, email="pwchange@example.com", password="oldpassword1")
    headers = auth_header(client, "pwchange@example.com", "oldpassword1")

    resp = client.post(
        "/users/me/change-password",
        json={"current_password": "wrongpassword", "new_password": "newpassword1"},
        headers=headers,
    )
    assert resp.status_code == 400

    resp = client.post(
        "/users/me/change-password",
        json={"current_password": "oldpassword1", "new_password": "newpassword1"},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = client.post("/auth/login", data={"username": "pwchange@example.com", "password": "newpassword1"})
    assert resp.status_code == 200


def test_user_changing_own_email_requires_reverification(client, db_session):
    make_admin(db_session, email="emailchange@example.com", password="password123")
    headers = auth_header(client, "emailchange@example.com", "password123")

    resp = client.put("/users/me", json={"email": "newaddress@example.com"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "newaddress@example.com"
    assert resp.json()["is_verified"] is False

    # Old token is still valid (JWT isn't revoked), but verified-gated
    # actions now fail until the new address is confirmed
    resp = client.put("/users/me", json={"user_name": "Still Me"}, headers=headers)
    assert resp.status_code == 403

    from app.models import User

    user = db_session.query(User).filter(User.email == "newaddress@example.com").first()
    assert user.verification_token is not None
    assert client.get(f"/auth/verify-email?token={user.verification_token}").status_code == 200

    resp = client.put("/users/me", json={"user_name": "Still Me"}, headers=headers)
    assert resp.status_code == 200


def test_customer_can_change_own_password(client, db_session):
    make_customer(db_session, email="custpw@example.com", password="oldpassword1")
    headers = customer_auth_header(client, "custpw@example.com", "oldpassword1")

    resp = client.post(
        "/customers/me/change-password",
        json={"current_password": "wrongpassword", "new_password": "newpassword1"},
        headers=headers,
    )
    assert resp.status_code == 400

    resp = client.post(
        "/customers/me/change-password",
        json={"current_password": "oldpassword1", "new_password": "newpassword1"},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = client.post(
        "/auth/customer/login", data={"username": "custpw@example.com", "password": "newpassword1"}
    )
    assert resp.status_code == 200


def test_customer_changing_own_email_requires_reverification(client, db_session):
    make_customer(db_session, email="custemailchange@example.com", password="password123")
    headers = customer_auth_header(client, "custemailchange@example.com", "password123")

    resp = client.put("/customers/me", json={"email": "newcust@example.com"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "newcust@example.com"
    assert resp.json()["is_verified"] is False

    from app.models import Customer

    customer = db_session.query(Customer).filter(Customer.email == "newcust@example.com").first()
    assert customer.verification_token is not None
    assert client.get(f"/auth/customer/verify-email?token={customer.verification_token}").status_code == 200


# ---------------------------------------------------------------------------
# Product price gating (access_permission)
# ---------------------------------------------------------------------------
def test_product_price_masked_until_access_permission_granted(client, db_session):
    admin = make_admin(db_session, email="priceview@example.com", password="password123")
    admin_headers = auth_header(client, "priceview@example.com", "password123")

    brand_id = client.post("/brands/", data={"brand_name": "PriceView Co"}, headers=admin_headers).json()["id"]
    product = client.post(
        "/products/",
        json={
            "product_name": "Locked Widget",
            "price": "42.00",
            "discount": 8,
            "brand_id": brand_id,
        },
        headers=admin_headers,
    ).json()
    product_id = product["id"]

    # Staff (mutation response) sees the real price
    assert product["price"] == "42.00"
    assert product["discount"] == "8.00"

    # Anonymous callers get a masked price and no discount at all
    anon_list = client.get("/products/").json()
    assert anon_list[0]["price"] == "XXXX"
    assert anon_list[0]["discount"] is None

    anon_detail = client.get(f"/products/{product_id}").json()
    assert anon_detail["price"] == "XXXX"
    assert anon_detail["discount"] is None

    # An authenticated staff user browsing the public catalog sees real prices
    staff_view = client.get("/products/", headers=admin_headers).json()
    assert staff_view[0]["price"] == "42.00"

    # A registered customer without access_permission still sees masked prices
    customer = make_customer(db_session, email="locked@example.com", password="password123", access_permission=False)
    customer_headers = customer_auth_header(client, "locked@example.com", "password123")
    resp = client.get("/products/", headers=customer_headers).json()
    assert resp[0]["price"] == "XXXX"
    assert resp[0]["discount"] is None

    # Staff grants access_permission
    grant_resp = client.put(
        f"/customers/{customer.id}", json={"access_permission": True}, headers=admin_headers
    )
    assert grant_resp.status_code == 200
    assert grant_resp.json()["access_permission"] is True

    # Same customer token (permission is checked live, not baked into the JWT)
    # now sees the real price
    resp = client.get("/products/", headers=customer_headers).json()
    assert resp[0]["price"] == "42.00"
    assert resp[0]["discount"] == "8.00"

    resp = client.get(f"/products/{product_id}", headers=customer_headers).json()
    assert resp["price"] == "42.00"


# ---------------------------------------------------------------------------
# Promotions
# ---------------------------------------------------------------------------
def test_promotion_date_validation_and_active_filter(client, db_session):
    make_admin(db_session, email="promoadmin@example.com", password="password123")
    headers = auth_header(client, "promoadmin@example.com", "password123")

    now = datetime.now(timezone.utc)

    # end_date before start_date must be rejected
    resp = client.post(
        "/promotions/",
        json={
            "promotion_name": "Bad Promo",
            "price": "10.00",
            "start_date": now.isoformat(),
            "end_date": (now - timedelta(days=1)).isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code == 422

    active_resp = client.post(
        "/promotions/",
        json={
            "promotion_name": "Summer Sale",
            "price": "10.00",
            "old_price": "20.00",
            "start_date": (now - timedelta(days=1)).isoformat(),
            "end_date": (now + timedelta(days=1)).isoformat(),
        },
        headers=headers,
    )
    assert active_resp.status_code == 201, active_resp.text

    future_resp = client.post(
        "/promotions/",
        json={
            "promotion_name": "Winter Sale",
            "price": "10.00",
            "start_date": (now + timedelta(days=10)).isoformat(),
            "end_date": (now + timedelta(days=20)).isoformat(),
        },
        headers=headers,
    )
    assert future_resp.status_code == 201

    # Public read, unauthenticated
    all_promos = client.get("/promotions/")
    assert all_promos.status_code == 200
    assert len(all_promos.json()) == 2

    active_only = client.get("/promotions/?active_only=true")
    assert active_only.status_code == 200
    assert len(active_only.json()) == 1
    assert active_only.json()[0]["promotion_name"] == "Summer Sale"


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------
def test_brand_image_upload(client, db_session):
    make_admin(db_session, email="uploader@example.com", password="password123")
    headers = auth_header(client, "uploader@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "UploadCo"}, headers=headers).json()["id"]

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    resp = client.post(
        f"/brands/{brand_id}/image",
        files={"file": ("logo.png", fake_png, "image/png")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["brand_image"].startswith("/static/uploads/brands/")

    resp = client.post(
        f"/brands/{brand_id}/image",
        files={"file": ("logo.txt", b"not an image", "text/plain")},
        headers=headers,
    )
    assert resp.status_code == 400


def test_create_brand_with_image_in_one_request(client, db_session):
    make_admin(db_session, email="uploader2@example.com", password="password123")
    headers = auth_header(client, "uploader2@example.com", "password123")

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    resp = client.post(
        "/brands/",
        data={"brand_name": "OneShotCo"},
        files={"file": ("logo.png", fake_png, "image/png")},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["brand_image"].startswith("/static/uploads/brands/")

    # Image is optional: creating without one still works
    resp = client.post("/brands/", data={"brand_name": "NoImageCo"}, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["brand_image"] is None


def test_category_image_upload(client, db_session):
    make_admin(db_session, email="catuploader@example.com", password="password123")
    headers = auth_header(client, "catuploader@example.com", "password123")
    category_id = client.post(
        "/categories/", data={"category_name": "UploadCat"}, headers=headers
    ).json()["id"]

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    resp = client.post(
        f"/categories/{category_id}/image",
        files={"file": ("logo.png", fake_png, "image/png")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["category_image"].startswith("/static/uploads/categories/")

    resp = client.post(
        f"/categories/{category_id}/image",
        files={"file": ("logo.txt", b"not an image", "text/plain")},
        headers=headers,
    )
    assert resp.status_code == 400


def test_create_category_with_image_in_one_request(client, db_session):
    make_admin(db_session, email="catuploader2@example.com", password="password123")
    headers = auth_header(client, "catuploader2@example.com", "password123")

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    resp = client.post(
        "/categories/",
        data={"category_name": "OneShotCat"},
        files={"file": ("logo.png", fake_png, "image/png")},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["category_image"].startswith("/static/uploads/categories/")

    # Image is optional: creating without one still works
    resp = client.post("/categories/", data={"category_name": "NoImageCat"}, headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["category_image"] is None


def test_create_manual_with_pdf_in_one_request(client, db_session):
    make_admin(db_session, email="uploader3@example.com", password="password123")
    headers = auth_header(client, "uploader3@example.com", "password123")

    brand_id = client.post("/brands/", data={"brand_name": "ManualCo"}, headers=headers).json()["id"]
    product_resp = client.post(
        "/products/",
        json={"product_name": "Widget", "price": "9.99", "brand_id": brand_id},
        headers=headers,
    )
    product_id = product_resp.json()["id"]

    fake_pdf = b"%PDF-1.4\n" + b"0" * 100
    resp = client.post(
        "/manuals/",
        data={"product_id": product_id, "description": "How to use the widget."},
        files={"file": ("manual.pdf", fake_pdf, "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["pdf"].startswith("/static/uploads/manuals/")
    assert body["description"] == "How to use the widget."

    # PDF is optional: creating without one still works
    resp = client.post(
        "/manuals/", data={"product_id": product_id}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["pdf"] is None

    # Wrong content-type is rejected
    resp = client.post(
        "/manuals/",
        data={"product_id": product_id},
        files={"file": ("manual.txt", b"not a pdf", "text/plain")},
        headers=headers,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Admin set-password
# ---------------------------------------------------------------------------
def test_admin_can_set_another_staff_members_password(client, db_session):
    make_admin(db_session, email="pwadmin@example.com", password="password123")
    admin_headers = auth_header(client, "pwadmin@example.com", "password123")

    resp = client.post(
        "/users/",
        json={
            "user_name": "Target Staff",
            "email": "target@example.com",
            "password": "originalpass1",
        },
        headers=admin_headers,
    )
    user_id = resp.json()["id"]

    resp = client.put(
        f"/users/{user_id}/password", json={"new_password": "brandnewpass1"}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text

    # Old password no longer works, new one does (once verified/activated - reuse the
    # admin-created-staff verify flow already exercised elsewhere in this file).
    from app.models import User

    target = db_session.query(User).filter(User.id == user_id).first()
    target.is_verified = True
    db_session.commit()

    resp = client.post("/auth/login", data={"username": "target@example.com", "password": "originalpass1"})
    assert resp.status_code == 401

    resp = client.post("/auth/login", data={"username": "target@example.com", "password": "brandnewpass1"})
    assert resp.status_code == 200


def test_admin_set_password_requires_user_management(client, db_session):
    from app.models import User
    from app.core.security import hash_password

    limited = User(
        user_name="No Perms",
        email="noperms@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_verified=True,
    )
    db_session.add(limited)
    db_session.commit()
    limited_headers = auth_header(client, "noperms@example.com", "password123")

    admin = make_admin(db_session, email="pwadmin2@example.com", password="password123")

    resp = client.put(
        f"/users/{admin.id}/password", json={"new_password": "whatever123"}, headers=limited_headers
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Orders (quotes)
# ---------------------------------------------------------------------------
def _make_order_product(client, headers, name="Quoted Widget", price="100.00"):
    brand_id = client.post("/brands/", data={"brand_name": f"OrderCo-{name}"}, headers=headers).json()["id"]
    return client.post(
        "/products/",
        json={"product_name": name, "price": price, "brand_id": brand_id},
        headers=headers,
    ).json()


def _make_set(client, headers, name="Order Set", price="50.00", old_price=None):
    payload = {"set_name": name, "price": price}
    if old_price is not None:
        payload["old_price"] = old_price
    resp = client.post("/sets/", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _order_payload(product_id, **overrides):
    payload = {
        "clinic_name": "Test Clinic",
        "phone": "012345678",
        "address": "123 Test St",
        "items": [{"product_id": product_id, "qty": 2}],
    }
    payload.update(overrides)
    return payload


def _make_promotion(client, headers, name="Order Promo", price="50.00", old_price=None, start_offset_days=-1, end_offset_days=1):
    now = datetime.now(timezone.utc)
    payload = {
        "promotion_name": name,
        "price": price,
        "start_date": (now + timedelta(days=start_offset_days)).isoformat(),
        "end_date": (now + timedelta(days=end_offset_days)).isoformat(),
    }
    if old_price is not None:
        payload["old_price"] = old_price
    resp = client.post("/promotions/", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_order_requires_clinic_phone_address(client, db_session):
    admin = make_admin(db_session, email="orderadmin1@example.com", password="password123")
    headers = auth_header(client, "orderadmin1@example.com", "password123")
    product = _make_order_product(client, headers)

    resp = client.post("/orders/", json={"items": [{"product_id": product["id"], "qty": 1}]}, headers=headers)
    assert resp.status_code == 422


def test_order_salesperson_and_user_are_server_derived(client, db_session):
    admin = make_admin(db_session, email="orderadmin2@example.com", password="password123")
    headers = auth_header(client, "orderadmin2@example.com", "password123")
    product = _make_order_product(client, headers, name="Widget2")

    resp = client.post(
        "/orders/",
        json=_order_payload(product["id"], salesperson="Ignored Name"),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["salesperson"] == "Admin User"
    assert body["quoted_by_name"] == "Admin User"
    assert body["quote_code"]
    assert body["order_number"]

    customer = make_customer(db_session, email="ordercust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "ordercust@example.com", "customerpass1")
    # payment_method is mandatory for customers (see the dedicated tests below).
    resp = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="cash"), headers=cust_headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["salesperson"] == "Website"
    assert body["quoted_by_name"] == "Test Customer"


def test_order_percent_discount_requires_product_management(client, db_session):
    from app.models import User
    from app.core.security import hash_password

    pricing_only = User(
        user_name="Pricing Only",
        email="pricingonly@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_verified=True,
        price_listing=True,
    )
    db_session.add(pricing_only)
    db_session.commit()

    pricing_headers = auth_header(client, "pricingonly@example.com", "password123")
    admin = make_admin(db_session, email="orderadmin3@example.com", password="password123")
    admin_headers = auth_header(client, "orderadmin3@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="Widget3", price="100.00")

    # price_listing alone is not enough to apply any discount - only product_management is.
    resp = client.post(
        "/orders/",
        json=_order_payload(product["id"], discount_type="percent", discount_value=10),
        headers=pricing_headers,
    )
    assert resp.status_code == 403

    resp = client.post(
        "/orders/",
        json=_order_payload(product["id"], discount_type="percent", discount_value=10),
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["subtotal"] == "200.00"
    assert body["discount_amount"] == "20.00"
    assert body["grand_total"] == "180.00"


def test_order_cash_discount_requires_product_management(client, db_session):
    from app.models import User
    from app.core.security import hash_password

    pricing_only = User(
        user_name="Pricing Only 2",
        email="pricingonly2@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_verified=True,
        price_listing=True,
    )
    db_session.add(pricing_only)
    db_session.commit()
    pricing_headers = auth_header(client, "pricingonly2@example.com", "password123")

    admin = make_admin(db_session, email="orderadmin4@example.com", password="password123")
    admin_headers = auth_header(client, "orderadmin4@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="Widget4", price="100.00")

    resp = client.post(
        "/orders/",
        json=_order_payload(product["id"], discount_type="cash", discount_value=15),
        headers=pricing_headers,
    )
    assert resp.status_code == 403

    resp = client.post(
        "/orders/",
        json=_order_payload(product["id"], discount_type="cash", discount_value=15),
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["discount_amount"] == "15.00"
    assert body["grand_total"] == "185.00"


def test_all_product_lines_are_discountable(client, db_session):
    admin = make_admin(db_session, email="orderadmin5@example.com", password="password123")
    headers = auth_header(client, "orderadmin5@example.com", "password123")

    first = _make_order_product(client, headers, name="Regular5", price="100.00")
    second = _make_order_product(client, headers, name="Regular5b", price="50.00")

    resp = client.post(
        "/orders/",
        json=_order_payload(
            None,
            discount_type="percent",
            discount_value=10,
            items=[
                {"product_id": first["id"], "qty": 1},
                {"product_id": second["id"], "qty": 1},
            ],
        ),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # subtotal = 100 + 50 = 150; both product lines are discountable -> 10% of the
    # full $150 subtotal (products no longer carry a discount-exempt type).
    assert body["subtotal"] == "150.00"
    assert body["discount_amount"] == "15.00"
    assert body["grand_total"] == "135.00"


def test_buy_active_promotion_creates_order_item(client, db_session):
    admin = make_admin(db_session, email="promobuy1@example.com", password="password123")
    headers = auth_header(client, "promobuy1@example.com", "password123")
    promo = _make_promotion(client, headers, name="Scaler Bundle", price="50.00", old_price="80.00")

    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"promotion_id": promo["id"], "qty": 2}]),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    item = body["items"][0]
    assert item["promotion_id"] == promo["id"]
    assert item["product_id"] is None
    assert item["product_name"] == "Scaler Bundle"
    assert item["unit_price"] == "50.00"
    # discount snapshot reconstructs old_price (50 + 30 = 80), same shape as a
    # product's own cash discount - see deriveOldUnitPrice()/derive_old_price().
    assert item["discount_type"] == "cash"
    assert item["discount"] == "30.00"
    assert item["line_amount"] == "100.00"
    assert body["subtotal"] == "100.00"


def test_promotion_excluded_from_order_discount_base(client, db_session):
    admin = make_admin(db_session, email="promobuy2@example.com", password="password123")
    headers = auth_header(client, "promobuy2@example.com", "password123")
    regular = _make_order_product(client, headers, name="Regular6", price="100.00")
    promo = _make_promotion(client, headers, name="Promo6", price="50.00")

    resp = client.post(
        "/orders/",
        json=_order_payload(
            None,
            discount_type="percent",
            discount_value=10,
            items=[
                {"product_id": regular["id"], "qty": 1},
                {"promotion_id": promo["id"], "qty": 1},
            ],
        ),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # subtotal = 100 + 50 = 150; only the $100 regular line is discountable - a
    # promotion is already a fixed deal price, so it's excluded from the base.
    assert body["subtotal"] == "150.00"
    assert body["discount_amount"] == "10.00"
    assert body["grand_total"] == "140.00"


def test_cannot_buy_expired_or_upcoming_promotion(client, db_session):
    admin = make_admin(db_session, email="promobuy3@example.com", password="password123")
    headers = auth_header(client, "promobuy3@example.com", "password123")

    expired = _make_promotion(client, headers, name="Expired Promo", start_offset_days=-10, end_offset_days=-5)
    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"promotion_id": expired["id"], "qty": 1}]),
        headers=headers,
    )
    assert resp.status_code == 400
    assert "not currently active" in resp.json()["detail"]

    upcoming = _make_promotion(client, headers, name="Upcoming Promo", start_offset_days=5, end_offset_days=10)
    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"promotion_id": upcoming["id"], "qty": 1}]),
        headers=headers,
    )
    assert resp.status_code == 400
    assert "not currently active" in resp.json()["detail"]


def test_order_item_requires_exactly_one_of_product_or_promotion(client, db_session):
    admin = make_admin(db_session, email="promobuy4@example.com", password="password123")
    headers = auth_header(client, "promobuy4@example.com", "password123")
    product = _make_order_product(client, headers, name="Widget7")
    promo = _make_promotion(client, headers, name="Promo7")

    # neither id set
    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"qty": 1}]),
        headers=headers,
    )
    assert resp.status_code == 422

    # both ids set
    resp = client.post(
        "/orders/",
        json=_order_payload(
            None, items=[{"product_id": product["id"], "promotion_id": promo["id"], "qty": 1}]
        ),
        headers=headers,
    )
    assert resp.status_code == 422


def test_buy_set_creates_order_item(client, db_session):
    admin = make_admin(db_session, email="setbuy1@example.com", password="password123")
    headers = auth_header(client, "setbuy1@example.com", "password123")
    set_ = _make_set(client, headers, name="Scaler Set", price="50.00", old_price="80.00")

    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"set_id": set_["id"], "qty": 2}]),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    item = body["items"][0]
    assert item["set_id"] == set_["id"]
    assert item["product_id"] is None
    assert item["promotion_id"] is None
    assert item["product_name"] == "Scaler Set"
    assert item["unit_price"] == "50.00"
    assert item["discount_type"] == "cash"
    assert item["discount"] == "30.00"
    assert item["line_amount"] == "100.00"
    assert body["subtotal"] == "100.00"


def test_set_excluded_from_order_discount_base(client, db_session):
    admin = make_admin(db_session, email="setbuy2@example.com", password="password123")
    headers = auth_header(client, "setbuy2@example.com", "password123")
    regular = _make_order_product(client, headers, name="Regular8", price="100.00")
    set_ = _make_set(client, headers, name="Set8", price="50.00")

    resp = client.post(
        "/orders/",
        json=_order_payload(
            None,
            discount_type="percent",
            discount_value=10,
            items=[
                {"product_id": regular["id"], "qty": 1},
                {"set_id": set_["id"], "qty": 1},
            ],
        ),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # subtotal = 100 + 50 = 150; only the $100 regular line is discountable - a set is
    # already a fixed deal price, so it's excluded from the base.
    assert body["subtotal"] == "150.00"
    assert body["discount_amount"] == "10.00"
    assert body["grand_total"] == "140.00"


def test_order_item_requires_exactly_one_of_product_promotion_or_set(client, db_session):
    admin = make_admin(db_session, email="setbuy3@example.com", password="password123")
    headers = auth_header(client, "setbuy3@example.com", "password123")
    product = _make_order_product(client, headers, name="Widget8")
    set_ = _make_set(client, headers, name="Set9")

    # both ids set
    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"product_id": product["id"], "set_id": set_["id"], "qty": 1}]),
        headers=headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Product discount type (percent vs cash)
# ---------------------------------------------------------------------------
def test_product_cash_discount_create_and_snapshot_onto_order_item(client, db_session):
    admin = make_admin(db_session, email="proddiscount1@example.com", password="password123")
    headers = auth_header(client, "proddiscount1@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "CashDiscountCo"}, headers=headers).json()["id"]

    resp = client.post(
        "/products/",
        json={
            "product_name": "Cash Discount Widget",
            "price": "100.00",
            "discount_type": "cash",
            "discount": "15.00",
            "brand_id": brand_id,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    product = resp.json()
    assert product["discount_type"] == "cash"
    assert product["discount"] == "15.00"

    resp = client.post(
        "/orders/",
        json={
            "clinic_name": "Clinic", "phone": "011", "address": "Addr",
            "items": [{"product_id": product["id"], "qty": 1}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    item = resp.json()["items"][0]
    assert item["discount_type"] == "cash"
    assert item["discount"] == "15.00"


def test_product_cash_discount_may_exceed_price(client, db_session):
    """price is the already-discounted amount the caller computed (e.g. Flask does
    original=100, cash discount=60 -> price=40 before calling this API), so a cash
    discount numerically larger than price is a normal, valid state, not an error."""
    admin = make_admin(db_session, email="proddiscount2@example.com", password="password123")
    headers = auth_header(client, "proddiscount2@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "CashDiscountCo2"}, headers=headers).json()["id"]

    resp = client.post(
        "/products/",
        json={
            "product_name": "Overdiscounted Widget",
            "price": "40.00",
            "discount_type": "cash",
            "discount": "60.00",
            "brand_id": brand_id,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    product = resp.json()
    assert product["price"] == "40.00"
    assert product["discount"] == "60.00"


def test_product_percent_discount_still_capped_at_100(client, db_session):
    admin = make_admin(db_session, email="proddiscount3@example.com", password="password123")
    headers = auth_header(client, "proddiscount3@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "PercentDiscountCo"}, headers=headers).json()["id"]

    resp = client.post(
        "/products/",
        json={
            "product_name": "Overdiscounted Percent Widget",
            "price": "50.00",
            "discount_type": "percent",
            "discount": "150",
            "brand_id": brand_id,
        },
        headers=headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Order type (quote vs. order) + KHQR payment
# ---------------------------------------------------------------------------
def _fast_alert_wait(monkeypatch):
    """deliver_order_alert waits _QUOTATION_PDF_WAIT_SECONDS for the browser's PDF
    upload before falling back - pointless (and slow) in tests, where no browser
    ever uploads one."""
    monkeypatch.setattr("app.services.telegram._QUOTATION_PDF_WAIT_SECONDS", 0.01)


def test_staff_order_is_stored_as_quote(client, db_session, monkeypatch):
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="quotestaff@example.com", password="password123")
    headers = auth_header(client, "quotestaff@example.com", "password123")
    product = _make_order_product(client, headers, name="QuoteWidget")

    # Even if a staff client sends a payment_method, the row is a quote with no
    # payment concept attached.
    resp = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="khqr"), headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["order_type"] == "quote"
    assert body["payment_method"] is None
    assert body["payment_status"] is None
    assert body["khqr_string"] is None


def test_customer_must_choose_payment_method(client, db_session):
    make_admin(db_session, email="pmadmin@example.com", password="password123")
    admin_headers = auth_header(client, "pmadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="PmWidget")

    make_customer(db_session, email="pmcust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "pmcust@example.com", "customerpass1")

    resp = client.post("/orders/", json=_order_payload(product["id"]), headers=cust_headers)
    assert resp.status_code == 400
    assert "payment method" in resp.json()["detail"].lower()


def test_customer_cash_order_is_quote(client, db_session, monkeypatch):
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="cashadmin@example.com", password="password123")
    admin_headers = auth_header(client, "cashadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="CashWidget")

    make_customer(db_session, email="cashcust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "cashcust@example.com", "customerpass1")

    resp = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="cash"), headers=cust_headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["order_type"] == "quote"
    assert body["payment_method"] == "cash"
    assert body["payment_status"] is None
    assert body["khqr_string"] is None


def _configure_bakong(monkeypatch, account_id="testmerchant@devb"):
    """Pins the Bakong-direct provider for a test, clearing anything the developer's
    real .env may have configured - without this, local PayWay credentials take
    priority and the test silently exercises the wrong provider (and calls out to
    ABA's sandbox for real)."""
    from app.config import settings

    monkeypatch.setattr(settings, "KHQR_PROVIDER", "auto")
    monkeypatch.setattr(settings, "PAYWAY_MERCHANT_ID", "")
    monkeypatch.setattr(settings, "PAYWAY_API_KEY", "")
    monkeypatch.setattr(settings, "KHQR_STATIC_TEMPLATE", "")
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_ID", account_id)


def test_khqr_unavailable_when_not_configured(client, db_session, monkeypatch):
    _configure_bakong(monkeypatch, account_id="")
    make_admin(db_session, email="noqradmin@example.com", password="password123")
    admin_headers = auth_header(client, "noqradmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="NoQrWidget")

    make_customer(db_session, email="noqrcust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "noqrcust@example.com", "customerpass1")

    resp = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="khqr"), headers=cust_headers
    )
    assert resp.status_code == 400
    assert "not available" in resp.json()["detail"]


def test_customer_khqr_order_generates_payload(client, db_session, monkeypatch):
    import hashlib

    from app.services.khqr import _crc16_ccitt

    _configure_bakong(monkeypatch)
    make_admin(db_session, email="qradmin@example.com", password="password123")
    admin_headers = auth_header(client, "qradmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="QrWidget", price="75.00")

    make_customer(db_session, email="qrcust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "qrcust@example.com", "customerpass1")

    resp = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="khqr"), headers=cust_headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["order_type"] == "order"
    assert body["payment_method"] == "khqr"
    assert body["payment_status"] == "unpaid"

    payload = body["khqr_string"]
    assert payload.startswith("000201")  # tag 00, len 02, version "01"
    assert "testmerchant@devb" in payload
    assert "5303840" in payload  # currency tag: USD
    assert "5406150.00" in payload  # amount tag: 2 x $75.00
    assert body["order_number"] in payload  # bill number carries the order number
    # CRC integrity: the last 4 chars must be the CRC of everything before them.
    assert payload[-8:-4] == "6304"
    assert payload[-4:] == _crc16_ccitt(payload[:-4])
    assert body["khqr_md5"] == hashlib.md5(payload.encode()).hexdigest()


def test_khqr_payment_status_poll_and_manual_mark_paid(client, db_session, monkeypatch):
    _fast_alert_wait(monkeypatch)
    _configure_bakong(monkeypatch)
    make_admin(db_session, email="payadmin@example.com", password="password123")
    admin_headers = auth_header(client, "payadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="PayWidget")

    make_customer(db_session, email="paycust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "paycust@example.com", "customerpass1")

    order = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="khqr"), headers=cust_headers
    ).json()

    # No Bakong token configured -> the poll can't auto-confirm; stays unpaid.
    resp = client.get(f"/orders/{order['id']}/payment-status", headers=cust_headers)
    assert resp.status_code == 200
    assert resp.json() == {"payment_status": "unpaid"}

    # Someone else's account can't poll it.
    make_customer(db_session, email="otherpay@example.com", password="customerpass1", access_permission=True)
    other_headers = customer_auth_header(client, "otherpay@example.com", "customerpass1")
    resp = client.get(f"/orders/{order['id']}/payment-status", headers=other_headers)
    assert resp.status_code == 403

    # Staff manually mark it paid (the no-Bakong-token fallback) - paid_at gets stamped.
    resp = client.put(
        f"/orders/{order['id']}", json={"payment_status": "paid"}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_status"] == "paid"
    assert body["paid_at"] is not None

    # The customer's poll now sees it.
    resp = client.get(f"/orders/{order['id']}/payment-status", headers=cust_headers)
    assert resp.json() == {"payment_status": "paid"}


def test_khqr_built_from_a_bank_static_qr_template(monkeypatch):
    """A bank's own personal/P2P QR isn't a plain name@bank alias - ABA's
    dual-currency one carries the institution id in tag 29 sub-00 and the real
    account number in sub-01, plus a proprietary tag 40. All of that payee routing
    data must survive into the generated dynamic QR, or payments have nowhere to
    land. Template values here mirror a real ABA card's structure."""
    from decimal import Decimal

    from app.config import settings
    from app.services.khqr import _parse_tlv, build_khqr

    template = (
        "00020101021129450016abaakhppxxx@abaa01090046136230208ABA Bank"
        "40600006abaP2P0112DA7A91479DE3020900461362303095000375630404Dual"
        "5204000053031165802KH5910BUNTHAY TE6010Phnom Penh6304C551"
    )
    monkeypatch.setattr(settings, "KHQR_STATIC_TEMPLATE", template)
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_ID", "")

    payload, md5 = build_khqr(Decimal("12.50"), bill_number="000123")
    fields = dict(_parse_tlv(payload))

    # Payee routing copied through byte-for-byte, including the proprietary tag.
    source = dict(_parse_tlv(template))
    assert fields["29"] == source["29"]
    assert fields["40"] == source["40"]
    assert "004613623" in fields["29"]  # the actual account, from sub-field 01

    # ...and the transaction-specific fields replaced.
    assert fields["01"] == "12"  # static -> dynamic
    assert fields["53"] == "840"  # USD, since every price here is USD
    assert fields["54"] == "12.50"
    assert fields["62"] == "0106000123"  # bill number = order_number
    # The name stays the bank's own (matches the account), not KHQR_MERCHANT_NAME.
    assert fields["59"] == "BUNTHAY TE"

    from app.services.khqr import _crc16_ccitt

    assert payload[-4:] == _crc16_ccitt(payload[:-4])
    import hashlib

    assert md5 == hashlib.md5(payload.encode()).hexdigest()


def test_khqr_template_rejects_a_non_khqr_string(monkeypatch):
    from decimal import Decimal

    from app.config import settings
    from app.services.khqr import build_khqr

    # Well-formed TLV, but no merchant-account tag (26-51) - nothing to pay into.
    monkeypatch.setattr(settings, "KHQR_STATIC_TEMPLATE", "00020101021158021KH")
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_ID", "")
    with pytest.raises(RuntimeError, match="merchant-account tag"):
        build_khqr(Decimal("1.00"), bill_number="000001")


def _settings(**overrides):
    """A Settings built in isolation from the developer's real .env - otherwise
    whatever credentials happen to be configured locally leak into these
    assertions (and, worse, into live network calls)."""
    from app.config import Settings

    return Settings(_env_file=None, **overrides)


def test_qr_provider_selection():
    """PayWay wins when both providers are configured; neither -> KHQR disabled."""
    both = _settings(PAYWAY_MERCHANT_ID="m", PAYWAY_API_KEY="k", BAKONG_ACCOUNT_ID="a@b")
    assert both.qr_provider == "payway" and both.khqr_configured
    bakong_only = _settings(BAKONG_ACCOUNT_ID="a@b")
    assert bakong_only.qr_provider == "bakong" and bakong_only.khqr_configured
    # A pasted static-QR template enables KHQR on its own, with no alias set.
    template_only = _settings(KHQR_STATIC_TEMPLATE="000201010211...")
    assert template_only.qr_provider == "bakong" and template_only.khqr_configured
    neither = _settings()
    assert neither.qr_provider == "" and not neither.khqr_configured


def test_khqr_provider_can_be_pinned_explicitly():
    """KHQR_PROVIDER pins the provider even with both configured - what lets a
    PayWay-credentialed setup test against a personal bank QR. A pin naming an
    unconfigured provider is ignored rather than disabling checkout."""
    both = dict(PAYWAY_MERCHANT_ID="m", PAYWAY_API_KEY="k", BAKONG_ACCOUNT_ID="a@b")
    assert _settings(**both, KHQR_PROVIDER="bakong").qr_provider == "bakong"
    assert _settings(**both, KHQR_PROVIDER="payway").qr_provider == "payway"
    assert _settings(**both, KHQR_PROVIDER="auto").qr_provider == "payway"
    # Pinned to a provider that isn't set up -> falls back to the one that is.
    assert _settings(BAKONG_ACCOUNT_ID="a@b", KHQR_PROVIDER="payway").qr_provider == "bakong"


def test_payway_reads_the_qr_string_under_either_key(monkeypatch):
    """PayWay's live API returns the QR under `qrString`, although its published
    docs call it `qr_string` (confirmed against the sandbox on 2026-07-29). Reading
    only the documented key meant a perfectly successful `code=00` purchase was
    treated as a failure, so both spellings are accepted - and an accepted purchase
    with no QR at all must still raise rather than return nothing."""
    from decimal import Decimal

    import httpx

    from app.config import settings
    from app.services import payway

    monkeypatch.setattr(settings, "PAYWAY_MERCHANT_ID", "m")
    monkeypatch.setattr(settings, "PAYWAY_API_KEY", "k")

    def fake_client_returning(body):
        class _Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return body

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def post(self, url, files=None):
                return _Response()

        return lambda **kwargs: _Client()

    monkeypatch.setattr(httpx, "Client", fake_client_returning(
        {"status": {"code": "00"}, "qrString": "QR-CAMEL"}))
    assert payway.create_payway_khqr(Decimal("1.00"), tran_id="T1") == "QR-CAMEL"

    monkeypatch.setattr(httpx, "Client", fake_client_returning(
        {"status": {"code": "00"}, "qr_string": "QR-SNAKE"}))
    assert payway.create_payway_khqr(Decimal("1.00"), tran_id="T2") == "QR-SNAKE"

    monkeypatch.setattr(httpx, "Client", fake_client_returning({"status": {"code": "00"}}))
    with pytest.raises(payway.PayWayError, match="no QR code"):
        payway.create_payway_khqr(Decimal("1.00"), tran_id="T3")

    monkeypatch.setattr(httpx, "Client", fake_client_returning(
        {"status": {"code": "5", "message": "Invalid hash"}}))
    with pytest.raises(payway.PayWayError, match="Invalid hash"):
        payway.create_payway_khqr(Decimal("1.00"), tran_id="T4")


def test_payway_base_url_tolerates_a_full_endpoint_url():
    """PayWay's docs list the base URL and endpoint path separately, so pasting the
    whole purchase URL into PAYWAY_API_BASE is an easy mistake - it used to produce
    a doubled path and a 404."""
    host = "https://checkout-sandbox.payway.com.kh"
    assert _settings(PAYWAY_API_BASE=host).payway_base_url == host
    assert _settings(PAYWAY_API_BASE=host + "/").payway_base_url == host
    full = host + "/api/payment-gateway/v1/payments/purchase"
    assert _settings(PAYWAY_API_BASE=full).payway_base_url == host


def _configure_payway(monkeypatch):
    from app.config import settings

    # KHQR_PROVIDER is pinned too: the real .env may pin "bakong", which would
    # otherwise route these PayWay tests down the Bakong path.
    monkeypatch.setattr(settings, "KHQR_PROVIDER", "auto")
    monkeypatch.setattr(settings, "PAYWAY_MERCHANT_ID", "testmerchant")
    monkeypatch.setattr(settings, "PAYWAY_API_KEY", "testkey123")
    # No Bakong config needed - PayWay alone enables KHQR checkout.
    monkeypatch.setattr(settings, "KHQR_STATIC_TEMPLATE", "")
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_ID", "")


def test_customer_khqr_order_via_payway(client, db_session, monkeypatch):
    _configure_payway(monkeypatch)
    # The real call would hit ABA's gateway - stub the service at its use site.
    monkeypatch.setattr(
        "app.routers.orders.create_payway_khqr",
        lambda amount, tran_id: f"FAKEQR|{amount:.2f}|{tran_id}",
    )
    make_admin(db_session, email="pwadmin1@example.com", password="password123")
    admin_headers = auth_header(client, "pwadmin1@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="PwWidget", price="60.00")

    make_customer(db_session, email="pwcust1@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "pwcust1@example.com", "customerpass1")

    resp = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="khqr"), headers=cust_headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["order_type"] == "order"
    assert body["payment_status"] == "unpaid"
    assert body["khqr_string"] == f"FAKEQR|120.00|{body['order_number']}"
    # PayWay orders keep khqr_md5 empty - that's how payment checks know to go by
    # tran_id instead of Bakong md5.
    assert body["khqr_md5"] is None


def test_payway_failure_degrades_to_choose_cash(client, db_session, monkeypatch):
    from app.services.payway import PayWayError

    _configure_payway(monkeypatch)

    def _boom(amount, tran_id):
        raise PayWayError("gateway down")

    monkeypatch.setattr("app.routers.orders.create_payway_khqr", _boom)
    make_admin(db_session, email="pwadmin2@example.com", password="password123")
    admin_headers = auth_header(client, "pwadmin2@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="PwWidget2")

    make_customer(db_session, email="pwcust2@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "pwcust2@example.com", "customerpass1")

    resp = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="khqr"), headers=cust_headers
    )
    assert resp.status_code == 400
    assert "choose Cash" in resp.json()["detail"]


def test_payway_payment_status_poll_flips_to_paid(client, db_session, monkeypatch):
    _fast_alert_wait(monkeypatch)
    _configure_payway(monkeypatch)
    monkeypatch.setattr(
        "app.routers.orders.create_payway_khqr", lambda amount, tran_id: "FAKEQR"
    )
    make_admin(db_session, email="pwadmin3@example.com", password="password123")
    admin_headers = auth_header(client, "pwadmin3@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="PwWidget3")

    make_customer(db_session, email="pwcust3@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "pwcust3@example.com", "customerpass1")

    order = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="khqr"), headers=cust_headers
    ).json()

    async def _not_paid(tran_id):
        return False

    async def _paid(tran_id):
        assert tran_id == order["order_number"]  # checked by tran_id, not md5
        return True

    monkeypatch.setattr("app.routers.orders.check_payway_payment", _not_paid)
    resp = client.get(f"/orders/{order['id']}/payment-status", headers=cust_headers)
    assert resp.json() == {"payment_status": "unpaid"}

    monkeypatch.setattr("app.routers.orders.check_payway_payment", _paid)
    resp = client.get(f"/orders/{order['id']}/payment-status", headers=cust_headers)
    assert resp.json() == {"payment_status": "paid"}

    # Paid state is persisted, not just reported.
    resp = client.get(f"/orders/{order['id']}", headers=admin_headers)
    assert resp.json()["payment_status"] == "paid"
    assert resp.json()["paid_at"] is not None


def test_payment_status_rejected_on_non_khqr_rows(client, db_session, monkeypatch):
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="npadmin@example.com", password="password123")
    admin_headers = auth_header(client, "npadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="NpWidget")

    quote = client.post("/orders/", json=_order_payload(product["id"]), headers=admin_headers).json()

    # A quote has no QR payment to poll...
    resp = client.get(f"/orders/{quote['id']}/payment-status", headers=admin_headers)
    assert resp.status_code == 400

    # ...and can't be marked paid either.
    resp = client.put(f"/orders/{quote['id']}", json={"payment_status": "paid"}, headers=admin_headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------
def test_unhandled_exception_returns_generic_500():
    """Uses raise_server_exceptions=False because that's what a real HTTP
    client sees: the app's global handler DOES catch this (verified
    separately) and returns a clean 500. The default TestClient
    re-raises internally-handled server exceptions purely for debugging
    visibility in tests, which would otherwise make this look like a
    failure even though the real HTTP response is correct."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    def broken_db():
        raise RuntimeError("simulated database outage")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken_db
    try:
        safe_client = TestClient(app, raise_server_exceptions=False)
        resp = safe_client.get("/brands/")
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Internal server error"}
        assert "RuntimeError" not in resp.text  # internals must not leak
    finally:
        app.dependency_overrides.pop(get_db, None)
