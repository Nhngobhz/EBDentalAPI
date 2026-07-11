from datetime import datetime, timedelta, timezone

from tests.conftest import auth_header, customer_auth_header, make_admin, make_customer


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


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


def test_product_type_defaults_to_single_and_filters(client, db_session):
    make_admin(db_session, email="ptype@example.com", password="password123")
    headers = auth_header(client, "ptype@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "ComboCo"}, headers=headers).json()["id"]

    single = client.post(
        "/products/",
        json={"product_name": "Single Item", "price": "10.00", "brand_id": brand_id},
        headers=headers,
    ).json()
    assert single["product_type"] == "single"

    combo = client.post(
        "/products/",
        json={
            "product_name": "Combo Set",
            "price": "50.00",
            "brand_id": brand_id,
            "product_type": "combo",
        },
        headers=headers,
    ).json()
    assert combo["product_type"] == "combo"

    resp = client.get("/products/?product_type=combo")
    assert resp.status_code == 200
    assert [p["product_name"] for p in resp.json()] == ["Combo Set"]

    # Not one of the allowed values (see schemas.ProductType) - rejected
    resp = client.post(
        "/products/",
        json={"product_name": "Bad Type", "price": "1.00", "brand_id": brand_id, "product_type": "bogus"},
        headers=headers,
    )
    assert resp.status_code == 422


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
            "old_price": "249.99",
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
            "old_price": "50.00",
            "brand_id": brand_id,
        },
        headers=admin_headers,
    ).json()
    product_id = product["id"]

    # Staff (mutation response) sees the real price
    assert product["price"] == "42.00"
    assert product["old_price"] == "50.00"

    # Anonymous callers get a masked price and no old_price at all
    anon_list = client.get("/products/").json()
    assert anon_list[0]["price"] == "XXXX"
    assert anon_list[0]["old_price"] is None

    anon_detail = client.get(f"/products/{product_id}").json()
    assert anon_detail["price"] == "XXXX"
    assert anon_detail["old_price"] is None

    # An authenticated staff user browsing the public catalog sees real prices
    staff_view = client.get("/products/", headers=admin_headers).json()
    assert staff_view[0]["price"] == "42.00"

    # A registered customer without access_permission still sees masked prices
    customer = make_customer(db_session, email="locked@example.com", password="password123", access_permission=False)
    customer_headers = customer_auth_header(client, "locked@example.com", "password123")
    resp = client.get("/products/", headers=customer_headers).json()
    assert resp[0]["price"] == "XXXX"
    assert resp[0]["old_price"] is None

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
    assert resp[0]["old_price"] == "50.00"

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
