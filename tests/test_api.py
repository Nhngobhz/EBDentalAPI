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


# ---------------------------------------------------------------------------
# Google sign-in (POST /auth/google)
#
# The real credential is a JWT signed by Google, so every test here stubs out
# verify_google_id_token - what's under test is the account matching/creation
# that happens AFTER a token verifies, not PyJWT's signature check.
# ---------------------------------------------------------------------------
def _stub_google(monkeypatch, email, name="Google Person", picture=None):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(
        "app.routers.auth.verify_google_id_token",
        lambda credential: {
            "iss": "https://accounts.google.com",
            "email": email,
            "email_verified": True,
            "name": name,
            "picture": picture,
        },
    )


def test_google_login_creates_verified_customer(client, db_session, monkeypatch):
    _stub_google(monkeypatch, "newgoogle@example.com", picture="https://lh3.example.com/a/pic")

    resp = client.post("/auth/google", json={"credential": "stub"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["account_type"] == "customer"
    assert "access_token" in body
    # Google confirming the address stands in for our own emailed link, but it
    # grants no price visibility - staff still have to turn that on.
    assert body["customer"]["is_verified"] is True
    assert body["customer"]["access_permission"] is False
    assert body["customer"]["customer_image"] == "https://lh3.example.com/a/pic"

    from app.models import Customer

    customer = db_session.query(Customer).filter(Customer.email == "newgoogle@example.com").first()
    assert customer.hashed_password is None  # no password: this account signs in with Google


def test_google_login_matches_existing_staff_by_email(client, db_session, monkeypatch):
    make_admin(db_session, email="GoogleStaff@example.com", password="password123")
    # Google always reports a lowercase address; the stored row keeps its casing.
    _stub_google(monkeypatch, "googlestaff@example.com")

    resp = client.post("/auth/google", json={"credential": "stub"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["account_type"] == "user"
    assert body["user"]["email"] == "GoogleStaff@example.com"

    from app.models import Customer

    # Matching a staff account must not also spawn a customer for that email.
    assert db_session.query(Customer).count() == 0


def test_google_login_verifies_and_reuses_existing_customer(client, db_session, monkeypatch):
    from app.models import Customer

    existing = Customer(
        customer_name="Already Here",
        email="existing@example.com",
        access_permission=True,
        is_active=True,
        is_verified=False,
    )
    db_session.add(existing)
    db_session.commit()

    _stub_google(monkeypatch, "existing@example.com", name="Different Google Name")
    resp = client.post("/auth/google", json={"credential": "stub"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["customer"]["id"] == existing.id
    assert body["customer"]["customer_name"] == "Already Here"  # never overwritten
    assert body["customer"]["is_verified"] is True  # Google proved the address
    assert body["customer"]["access_permission"] is True  # granted access survives
    assert db_session.query(Customer).count() == 1


def test_google_login_rejects_deactivated_customer(client, db_session, monkeypatch):
    customer = make_customer(db_session, email="gone@example.com", password="customerpass1")
    customer.is_active = False
    db_session.commit()

    _stub_google(monkeypatch, "gone@example.com")
    resp = client.post("/auth/google", json={"credential": "stub"})
    assert resp.status_code == 403
    assert "deactivated" in resp.json()["detail"].lower()


def test_google_login_disabled_when_client_id_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
    resp = client.post("/auth/google", json={"credential": "stub"})
    assert resp.status_code == 400


def test_google_login_rejects_unverified_google_token(client, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    resp = client.post("/auth/google", json={"credential": "not-a-real-token"})
    # Nothing stubbed here: the real verifier runs and refuses it.
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


def test_promotions_and_sets_are_authored_by_product_management(client, db_session):
    """Promotions and Sets used to be price_listing writes. They decide what the store
    advertises and which products are in the bundle, which is catalogue authoring - so
    they moved to product_management, and a sales/pricing staffer can now only read
    them."""
    from app.models import User
    from app.core.security import hash_password

    db_session.add(User(
        user_name="Pricing Only",
        email="promopricing@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_verified=True,
        price_listing=True,
    ))
    db_session.add(User(
        user_name="Catalog Only",
        email="promocatalog@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_verified=True,
        product_management=True,
    ))
    db_session.commit()

    pricing_headers = auth_header(client, "promopricing@example.com", "password123")
    catalog_headers = auth_header(client, "promocatalog@example.com", "password123")

    promo = {
        "promotion_name": "Perm Promo",
        "price": "50.00",
        "start_date": "2026-01-01T00:00:00",
        "end_date": "2026-12-31T23:59:59",
    }
    assert client.post("/promotions/", json=promo, headers=pricing_headers).status_code == 403
    assert client.post("/sets/", json={"set_name": "Perm Set", "price": "50.00"},
                       headers=pricing_headers).status_code == 403

    created_promo = client.post("/promotions/", json=promo, headers=catalog_headers)
    assert created_promo.status_code == 201, created_promo.text
    created_set = client.post("/sets/", json={"set_name": "Perm Set", "price": "50.00"},
                              headers=catalog_headers)
    assert created_set.status_code == 201, created_set.text

    # Editing and deleting follow the same rule.
    promo_id = created_promo.json()["id"]
    set_id = created_set.json()["id"]
    assert client.put(f"/promotions/{promo_id}", json={"price": "40.00"},
                      headers=pricing_headers).status_code == 403
    assert client.put(f"/sets/{set_id}", json={"price": "40.00"},
                      headers=pricing_headers).status_code == 403
    assert client.delete(f"/promotions/{promo_id}", headers=pricing_headers).status_code == 403
    assert client.delete(f"/sets/{set_id}", headers=pricing_headers).status_code == 403

    # But a pricing staffer still *reads* both - they sell them.
    assert client.get("/promotions/", headers=pricing_headers).status_code == 200
    assert client.get(f"/sets/{set_id}", headers=pricing_headers).status_code == 200


# ---------------------------------------------------------------------------
# list_price (the stored pre-discount price - see migration f2a9c4e18b73)
# ---------------------------------------------------------------------------
def _price_test_setup(client, db_session, email):
    make_admin(db_session, email=email, password="password123")
    headers = auth_header(client, email, "password123")
    brand_id = client.post("/brands/", data={"brand_name": f"LP {email}"}, headers=headers).json()["id"]
    return headers, brand_id


def test_list_price_is_derived_from_discount_when_not_sent(client, db_session):
    headers, brand_id = _price_test_setup(client, db_session, "listprice1@example.com")
    resp = client.post(
        "/products/",
        json={"product_name": "Derived", "price": "90.00", "discount": "10",
              "discount_type": "percent", "brand_id": brand_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    # 90 charged after a 10% discount implies a 100.00 list price.
    assert resp.json()["list_price"] == "100.00"


def test_repricing_does_not_move_an_existing_list_price(client, db_session):
    """The regression this column exists for.

    The pre-discount price used to be recomputed as price/(1 - discount/100) on
    every read, so dropping the charged price silently dragged the "was" figure
    down with it. It must now stay put, with the discount re-derived instead."""
    headers, brand_id = _price_test_setup(client, db_session, "listprice2@example.com")
    product = client.post(
        "/products/",
        json={"product_name": "Repriced", "price": "90.00", "discount": "10",
              "discount_type": "percent", "brand_id": brand_id},
        headers=headers,
    ).json()
    assert product["list_price"] == "100.00"

    resp = client.patch(
        f"/products/{product['id']}/price", json={"price": "80.00"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["price"] == "80.00"
    # The old behaviour would have reported 88.89 here.
    assert body["list_price"] == "100.00"
    # ...and the discount now describes the real gap between the two prices.
    assert body["discount"] == "20.00"


def test_explicit_list_price_is_stored_verbatim_and_must_not_be_below_price(client, db_session):
    headers, brand_id = _price_test_setup(client, db_session, "listprice3@example.com")
    resp = client.post(
        "/products/",
        json={"product_name": "Explicit", "price": "80.00", "list_price": "129.99",
              "discount": "10", "discount_type": "percent", "brand_id": brand_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    # Sent explicitly, so it is NOT the 88.89 the discount would have implied.
    assert resp.json()["list_price"] == "129.99"

    resp = client.post(
        "/products/",
        json={"product_name": "Backwards", "price": "80.00", "list_price": "50.00",
              "brand_id": brand_id},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "list_price" in resp.json()["detail"]


def test_list_price_is_masked_like_price(client, db_session):
    headers, brand_id = _price_test_setup(client, db_session, "listprice4@example.com")
    product = client.post(
        "/products/",
        json={"product_name": "Masked", "price": "90.00", "discount": "10",
              "discount_type": "percent", "brand_id": brand_id},
        headers=headers,
    ).json()

    anon = client.get(f"/products/{product['id']}").json()
    assert anon["price"] == "XXXX"
    # Returning the list price to an unentitled viewer would give away both the
    # pre-discount figure and, with `discount`, the charged one.
    assert anon["list_price"] is None
    assert anon["discount"] is None


def test_order_item_snapshots_list_price(client, db_session):
    headers, brand_id = _price_test_setup(client, db_session, "listprice5@example.com")
    product = client.post(
        "/products/",
        json={"product_name": "Ordered", "price": "90.00", "discount": "10",
              "discount_type": "percent", "brand_id": brand_id},
        headers=headers,
    ).json()

    order = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"product_id": product["id"], "qty": 2}]),
        headers=headers,
    )
    assert order.status_code == 201, order.text
    item = order.json()["items"][0]
    assert item["unit_price"] == "90.00"
    # Snapshotted, so the printed quote's "UP before Discount" reads it directly
    # instead of dividing the discount back out.
    assert item["list_price"] == "100.00"

    # ...and it stays put on the placed order even after the product is repriced.
    client.patch(f"/products/{product['id']}/price", json={"price": "10.00"}, headers=headers)
    reread = client.get(f"/orders/{order.json()['id']}", headers=headers).json()
    assert reread["items"][0]["list_price"] == "100.00"


def test_updated_by_records_the_staff_member_who_last_wrote(client, db_session):
    headers, brand_id = _price_test_setup(client, db_session, "updatedby1@example.com")
    product = client.post(
        "/products/",
        json={"product_name": "Audited", "price": "10.00", "brand_id": brand_id},
        headers=headers,
    ).json()
    assert product["updated_by"]["user_name"] == "Admin User"
    assert product["updated_at"] is not None

    # A second admin edits it - updated_by follows the most recent writer.
    make_admin(db_session, email="updatedby2@example.com", password="password123")
    other = auth_header(client, "updatedby2@example.com", "password123")
    resp = client.put(
        f"/products/{product['id']}", json={"product_name": "Audited v2"}, headers=other
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated_by"]["user_name"] == "Admin User"
    assert resp.json()["updated_by"]["id"] != product["updated_by"]["id"]


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


def test_staff_date_of_birth_and_gender(client, db_session):
    """`users` carries the same optional pair as `customers` - settable by a
    user_management admin on create/update, and by the staff member themselves
    via PUT /users/me."""
    make_admin(db_session, email="staffdob@example.com", password="password123")
    headers = auth_header(client, "staffdob@example.com", "password123")

    resp = client.post(
        "/users/",
        json={
            "user_name": "Dated Staff",
            "email": "datedstaff@example.com",
            "password": "supersecret1",
            "date_of_birth": "1979-02-20",
            "gender": "other",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]
    assert resp.json()["date_of_birth"] == "1979-02-20"
    assert resp.json()["gender"] == "other"

    resp = client.put(
        f"/users/{user_id}",
        json={"date_of_birth": "1980-06-06", "gender": "male"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["date_of_birth"] == "1980-06-06"
    assert resp.json()["gender"] == "male"

    # Explicit nulls clear them again (how the admin modal blanks a value out).
    resp = client.put(
        f"/users/{user_id}", json={"date_of_birth": None, "gender": None}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["date_of_birth"] is None
    assert resp.json()["gender"] is None

    # Same validation as customers.
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    assert client.put(f"/users/{user_id}", json={"date_of_birth": tomorrow}, headers=headers).status_code == 422
    assert client.put(f"/users/{user_id}", json={"gender": "banana"}, headers=headers).status_code == 422

    # And the staff member can edit their own via /users/me.
    resp = client.put(
        "/users/me", json={"date_of_birth": "1991-09-09", "gender": "female"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["date_of_birth"] == "1991-09-09"
    assert resp.json()["gender"] == "female"

    resp = client.get("/users/me", headers=headers)
    assert resp.json()["date_of_birth"] == "1991-09-09"
    assert resp.json()["gender"] == "female"

    # A self-update that doesn't mention them leaves them alone (exclude_unset).
    resp = client.put("/users/me", json={"phone_num": "012345678"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["date_of_birth"] == "1991-09-09"
    assert resp.json()["gender"] == "female"


def test_staff_can_set_customer_date_of_birth_and_gender(client, db_session):
    make_admin(db_session, email="dobadmin@example.com", password="password123")
    headers = auth_header(client, "dobadmin@example.com", "password123")

    # Both fields are optional - a customer created without them comes back null.
    resp = client.post(
        "/customers/",
        json={"customer_name": "No Demographics", "email": "nodemo@example.com"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["date_of_birth"] is None
    assert resp.json()["gender"] is None

    resp = client.post(
        "/customers/",
        json={
            "customer_name": "Dated Customer",
            "email": "dated@example.com",
            "date_of_birth": "1990-05-14",
            "gender": "female",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    customer_id = resp.json()["id"]
    assert resp.json()["date_of_birth"] == "1990-05-14"
    assert resp.json()["gender"] == "female"

    # Explicit nulls clear them again (this is how both the profile page and the
    # admin customer modal blank a wrong value out).
    resp = client.put(
        f"/customers/{customer_id}",
        json={"date_of_birth": None, "gender": None},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["date_of_birth"] is None
    assert resp.json()["gender"] is None


def test_customer_date_of_birth_and_gender_are_validated(client, db_session):
    make_admin(db_session, email="dobvalidator@example.com", password="password123")
    headers = auth_header(client, "dobvalidator@example.com", "password123")

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    resp = client.post(
        "/customers/",
        json={"customer_name": "Time Traveller", "email": "future@example.com", "date_of_birth": tomorrow},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    resp = client.post(
        "/customers/",
        json={"customer_name": "Typo Year", "email": "typo@example.com", "date_of_birth": "0199-05-14"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    resp = client.post(
        "/customers/",
        json={"customer_name": "Bad Gender", "email": "badgender@example.com", "gender": "banana"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


def test_customer_can_edit_own_date_of_birth_and_gender(client, db_session):
    make_customer(db_session, email="selfdemo@example.com", password="password123")
    headers = customer_auth_header(client, "selfdemo@example.com", "password123")

    resp = client.put(
        "/customers/me",
        json={"date_of_birth": "1985-11-02", "gender": "male"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["date_of_birth"] == "1985-11-02"
    assert resp.json()["gender"] == "male"

    resp = client.get("/customers/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["date_of_birth"] == "1985-11-02"
    assert resp.json()["gender"] == "male"

    # A self-update that doesn't mention them leaves them alone (exclude_unset),
    # so editing just the phone number on the profile page can't wipe a birthday.
    resp = client.put("/customers/me", json={"phone_num": "012345678"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["date_of_birth"] == "1985-11-02"
    assert resp.json()["gender"] == "male"

    resp = client.put("/customers/me", json={"gender": "unicorn"}, headers=headers)
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Delivery location (Customer.latitude/longitude/map_link, and its snapshot on
# Order). See the column comments in models.py for why these are three
# independent fields rather than one derived from another.
# ---------------------------------------------------------------------------
def test_staff_can_set_and_clear_a_customer_location(client, db_session):
    make_admin(db_session, email="locadmin@example.com", password="password123")
    headers = auth_header(client, "locadmin@example.com", "password123")

    # Optional, like the demographics above - a customer created without one
    # comes back with all three null rather than a default pin somewhere.
    resp = client.post(
        "/customers/",
        json={"customer_name": "No Pin", "email": "nopin@example.com"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["latitude"] is None
    assert resp.json()["longitude"] is None
    assert resp.json()["map_link"] is None

    resp = client.post(
        "/customers/",
        json={
            "customer_name": "Pinned Clinic",
            "email": "pinned@example.com",
            "latitude": "11.556374",
            "longitude": "104.928207",
            "map_link": "https://maps.app.goo.gl/abc123",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    customer_id = resp.json()["id"]
    # Serialized as strings, like every other Decimal in this API.
    assert resp.json()["latitude"] == "11.556374"
    assert resp.json()["longitude"] == "104.928207"
    assert resp.json()["map_link"] == "https://maps.app.goo.gl/abc123"

    # Explicit nulls clear it. This one matters more than most: a WRONG pin
    # sends a driver to the wrong building, so "Clear" on the admin picker has
    # to really erase rather than leave the old coordinates in place.
    resp = client.put(
        f"/customers/{customer_id}",
        json={"latitude": None, "longitude": None, "map_link": None},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["latitude"] is None
    assert resp.json()["longitude"] is None
    assert resp.json()["map_link"] is None


def test_customer_location_halves_are_independent(client, db_session):
    """A link with no readable coordinates, and coordinates with no link, are
    both valid states - neither is derived from the other."""
    make_customer(db_session, email="halfpin@example.com", password="password123")
    headers = customer_auth_header(client, "halfpin@example.com", "password123")

    # A Google Maps short link carries no coordinates until its redirect is
    # followed, and that may fail. The link is still worth keeping.
    resp = client.put(
        "/customers/me",
        json={"map_link": "https://maps.app.goo.gl/onlyalink"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["map_link"] == "https://maps.app.goo.gl/onlyalink"
    assert resp.json()["latitude"] is None

    # A pin dropped on the map is the mirror image: coordinates, no link.
    resp = client.put(
        "/customers/me",
        json={"latitude": "11.6", "longitude": "104.9", "map_link": None},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["map_link"] is None
    assert resp.json()["latitude"] == "11.600000"

    # An update that does not mention the location leaves it alone, so editing
    # a phone number on the profile page cannot wipe a pin.
    resp = client.put("/customers/me", json={"phone_num": "012345678"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["latitude"] == "11.600000"


def test_customer_location_is_validated(client, db_session):
    make_admin(db_session, email="locvalidator@example.com", password="password123")
    headers = auth_header(client, "locvalidator@example.com", "password123")

    def create(email, **fields):
        return client.post(
            "/customers/",
            json={"customer_name": "Bad Location", "email": email, **fields},
            headers=headers,
        )

    # Off the planet - a transposed pair or a mis-parsed link, not a place.
    assert create("badlat@example.com", latitude="95.0").status_code == 422
    assert create("badlng@example.com", longitude="181.0").status_code == 422

    # A scheme that executes is stored XSS the moment a page renders the href.
    assert create("badscheme@example.com", map_link="javascript:alert(1)").status_code == 422

    # ...and so is any other host, for a subtler reason: this field is written
    # by CUSTOMERS and rendered to STAFF as an "Open in Google Maps" link, so
    # "is a valid URL" is not a high enough bar. See _MAP_LINK_HOST_RE.
    assert create("badhost@example.com", map_link="https://evil.example/phish").status_code == 422
    assert create("nearmiss@example.com", map_link="https://google.com.evil.example/x").status_code == 422

    # The shapes people actually paste all pass.
    links = [
        "https://maps.app.goo.gl/abc123",
        "https://www.google.com/maps/place/X/@11.55,104.92,17z",
        "https://google.com.kh/maps?q=11.55,104.92",
        "https://www.openstreetmap.org/#map=17/11.55/104.92",
    ]
    for i, link in enumerate(links):
        resp = create(f"goodlink{i}@example.com", map_link=link)
        assert resp.status_code == 201, resp.text
        assert resp.json()["map_link"] == link


def test_order_snapshots_the_delivery_location(client, db_session):
    headers, brand_id = _price_test_setup(client, db_session, "orderloc@example.com")
    product = client.post(
        "/products/",
        json={"product_name": "Delivered Thing", "price": "50.00", "brand_id": brand_id},
        headers=headers,
    ).json()

    resp = client.post(
        "/orders/",
        json=_order_payload(
            product["id"],
            latitude="11.556374",
            longitude="104.928207",
            map_link="https://maps.app.goo.gl/abc123",
        ),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    order_id = resp.json()["id"]
    assert resp.json()["latitude"] == "11.556374"
    assert resp.json()["map_link"] == "https://maps.app.goo.gl/abc123"

    # An edit that does not mention the pin leaves it alone - correcting a
    # phone number must not silently drop where the order is going.
    resp = client.put(f"/orders/{order_id}", json={"phone": "099999999"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["latitude"] == "11.556374"

    # ...but staff can clear a wrong one outright, unlike address/clinic/phone,
    # which are NOT NULL and reject an explicit null.
    resp = client.put(
        f"/orders/{order_id}",
        json={"latitude": None, "longitude": None, "map_link": None},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["latitude"] is None
    assert resp.json()["map_link"] is None

    resp = client.put(f"/orders/{order_id}", json={"address": None}, headers=headers)
    assert resp.status_code == 400, resp.text


def test_order_without_a_location_is_still_valid(client, db_session):
    """A pin is a convenience, never a condition of buying - and every staff
    quote for a walk-in has none."""
    headers, brand_id = _price_test_setup(client, db_session, "orderloc2@example.com")
    product = client.post(
        "/products/",
        json={"product_name": "Unpinned Thing", "price": "50.00", "brand_id": brand_id},
        headers=headers,
    ).json()

    resp = client.post("/orders/", json=_order_payload(product["id"]), headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["latitude"] is None
    assert resp.json()["longitude"] is None
    assert resp.json()["map_link"] is None


def test_self_registration_keeps_a_supplied_location(client, db_session):
    """POST /auth/customer/register builds its Customer from an EXPLICIT field
    list, so a column added only to CustomerBase would be validated here and
    then silently dropped. This is the test that catches that."""
    resp = client.post(
        "/auth/customer/register",
        json={
            "customer_name": "Pinned Signup",
            "email": "pinnedsignup@example.com",
            "password": "password123",
            "latitude": "11.556374",
            "longitude": "104.928207",
            "map_link": "https://maps.app.goo.gl/signup",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["latitude"] == "11.556374"
    assert resp.json()["map_link"] == "https://maps.app.goo.gl/signup"


def test_products_are_listed_alphabetically(client, db_session):
    """The catalog is browsed, not scrolled in insertion order - and the sort
    has to happen in the DATABASE, because `limit` slices the result before any
    client could reorder it."""
    headers, brand_id = _price_test_setup(client, db_session, "sortbyname@example.com")
    # Created deliberately out of order, with mixed case, since the sort is
    # case-insensitive (lower(product_name)).
    for name in ["zeta drill", "Alpha Chair", "middle Light", "Beta Scaler"]:
        resp = client.post(
            "/products/",
            json={"product_name": name, "price": "10.00", "brand_id": brand_id},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    names = [p["product_name"] for p in client.get("/products/", params={"limit": 500}).json()]
    assert names == sorted(names, key=str.lower)
    assert names.index("Alpha Chair") < names.index("Beta Scaler")
    assert names.index("Beta Scaler") < names.index("middle Light")
    assert names.index("middle Light") < names.index("zeta drill")


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
# Sets - brand assignment and the storefront's brand filter
# ---------------------------------------------------------------------------
def test_set_brand_is_optional_and_filterable(client, db_session):
    make_admin(db_session, email="setbrand@example.com", password="password123")
    headers = auth_header(client, "setbrand@example.com", "password123")

    brand_id = client.post(
        "/brands/", data={"brand_name": "SetBrandCo"}, headers=headers
    ).json()["id"]

    branded = client.post(
        "/sets/",
        json={"set_name": "Branded Set", "price": "50.00", "brand_id": brand_id},
        headers=headers,
    )
    assert branded.status_code == 201, branded.text
    assert branded.json()["brand"]["brand_name"] == "SetBrandCo"

    # A set without a brand is still valid - it just sits under "All".
    plain = client.post("/sets/", json={"set_name": "Plain Set", "price": "20.00"}, headers=headers)
    assert plain.status_code == 201, plain.text
    assert plain.json()["brand"] is None

    # Public listing, unauthenticated: the filter is what the Promotions page's
    # brand strip uses.
    all_sets = client.get("/sets/")
    assert all_sets.status_code == 200
    assert len(all_sets.json()) == 2

    filtered = client.get(f"/sets/?brand_id={brand_id}")
    assert filtered.status_code == 200
    assert [s["set_name"] for s in filtered.json()] == ["Branded Set"]

    # Empty string (a browser-built `?brand_id=`) means "no filter", not a 422.
    assert len(client.get("/sets/?brand_id=").json()) == 2


def test_set_brand_can_be_changed_and_cleared(client, db_session):
    make_admin(db_session, email="setbrand2@example.com", password="password123")
    headers = auth_header(client, "setbrand2@example.com", "password123")

    brand_id = client.post(
        "/brands/", data={"brand_name": "SwitchBrandCo"}, headers=headers
    ).json()["id"]
    set_id = client.post(
        "/sets/", json={"set_name": "Switchable", "price": "30.00"}, headers=headers
    ).json()["id"]

    assigned = client.put(f"/sets/{set_id}", json={"brand_id": brand_id}, headers=headers)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["brand"]["id"] == brand_id

    # Explicit null clears it; omitting the field would have left it alone.
    cleared = client.put(f"/sets/{set_id}", json={"brand_id": None}, headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["brand"] is None


def test_set_rejects_unknown_brand(client, db_session):
    make_admin(db_session, email="setbrand3@example.com", password="password123")
    headers = auth_header(client, "setbrand3@example.com", "password123")

    resp = client.post(
        "/sets/", json={"set_name": "Ghost Brand Set", "price": "10.00", "brand_id": 999999}, headers=headers
    )
    assert resp.status_code == 400
    assert "brand_id" in resp.json()["detail"]


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


def test_product_gallery_upload_append_and_delete(client, db_session):
    """Extra product photos: appended (never replaced), ordered, individually
    removable, and separate from the primary product_image.

    Deliberately asserts nothing about the returned URL's shape - these tests run
    against whatever storage the environment has configured (R2 when credentials
    are present, local disk otherwise), and either is a valid answer here."""
    make_admin(db_session, email="gallery@example.com", password="password123")
    headers = auth_header(client, "gallery@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "GalleryCo"}, headers=headers).json()["id"]
    product_id = client.post(
        "/products/",
        json={"product_name": "Photogenic Widget", "price": "10.00", "brand_id": brand_id},
        headers=headers,
    ).json()["id"]

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    resp = client.post(
        f"/products/{product_id}/gallery",
        files=[
            ("files", ("one.png", fake_png, "image/png")),
            ("files", ("two.png", fake_png, "image/png")),
        ],
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    images = resp.json()["images"]
    assert len(images) == 2
    assert [i["sort_order"] for i in images] == [0, 1]
    assert all(i["image"] for i in images)
    # The gallery never touches the primary picture.
    assert resp.json()["product_image"] is None

    # A second upload appends rather than replacing.
    resp = client.post(
        f"/products/{product_id}/gallery",
        files=[("files", ("three.png", fake_png, "image/png"))],
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert [i["sort_order"] for i in resp.json()["images"]] == [0, 1, 2]

    # Non-images are refused, same as every other upload endpoint.
    resp = client.post(
        f"/products/{product_id}/gallery",
        files=[("files", ("notes.txt", b"not an image", "text/plain"))],
        headers=headers,
    )
    assert resp.status_code == 400

    # Public reads carry the gallery.
    listed = client.get(f"/products/{product_id}").json()["images"]
    assert len(listed) == 3

    # Deleting one leaves the others (and their sort_order) alone.
    resp = client.delete(f"/products/{product_id}/gallery/{listed[1]['id']}", headers=headers)
    assert resp.status_code == 204
    remaining = client.get(f"/products/{product_id}").json()["images"]
    assert [i["id"] for i in remaining] == [listed[0]["id"], listed[2]["id"]]
    assert [i["sort_order"] for i in remaining] == [0, 2]


def test_product_gallery_delete_is_scoped_to_its_product(client, db_session):
    """An image id belonging to another product can't be deleted through this
    product's path."""
    make_admin(db_session, email="gallery2@example.com", password="password123")
    headers = auth_header(client, "gallery2@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "GalleryCo2"}, headers=headers).json()["id"]

    def make_product(name):
        return client.post(
            "/products/",
            json={"product_name": name, "price": "10.00", "brand_id": brand_id},
            headers=headers,
        ).json()["id"]

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    owner_id = make_product("Owner")
    other_id = make_product("Other")
    image_id = client.post(
        f"/products/{owner_id}/gallery",
        files=[("files", ("one.png", fake_png, "image/png"))],
        headers=headers,
    ).json()["images"][0]["id"]

    resp = client.delete(f"/products/{other_id}/gallery/{image_id}", headers=headers)
    assert resp.status_code == 404
    assert len(client.get(f"/products/{owner_id}").json()["images"]) == 1

    # Deleting the product takes its gallery with it (ON DELETE CASCADE).
    assert client.delete(f"/products/{owner_id}", headers=headers).status_code == 204
    from app.models import ProductImage

    assert db_session.query(ProductImage).filter(ProductImage.id == image_id).first() is None


def test_product_gallery_upload_requires_product_management(client, db_session):
    make_admin(db_session, email="galleryowner@example.com", password="password123")
    owner_headers = auth_header(client, "galleryowner@example.com", "password123")
    brand_id = client.post(
        "/brands/", data={"brand_name": "GalleryCo3"}, headers=owner_headers
    ).json()["id"]
    product_id = client.post(
        "/products/",
        json={"product_name": "Guarded Widget", "price": "10.00", "brand_id": brand_id},
        headers=owner_headers,
    ).json()["id"]

    from app.core.security import hash_password
    from app.models import User

    db_session.add(
        User(
            user_name="Pricing Only",
            email="galleryPricer@example.com",
            hashed_password=hash_password("password123"),
            is_active=True,
            is_verified=True,
            price_listing=True,
        )
    )
    db_session.commit()
    headers = auth_header(client, "galleryPricer@example.com", "password123")

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    resp = client.post(
        f"/products/{product_id}/gallery",
        files=[("files", ("one.png", fake_png, "image/png"))],
        headers=headers,
    )
    assert resp.status_code == 403
    assert "product_management" in resp.json()["detail"]


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


def test_a_product_can_carry_several_titled_manuals(client, db_session):
    """A user guide, a quick-start sheet and a service manual on one product,
    each named. `title` is what makes several documents distinguishable - without
    it they were three identical-looking rows."""
    make_admin(db_session, email="manytitles@example.com", password="password123")
    headers = auth_header(client, "manytitles@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "DocsCo"}, headers=headers).json()["id"]
    product_id = client.post(
        "/products/",
        json={"product_name": "Documented Widget", "price": "50.00", "brand_id": brand_id},
        headers=headers,
    ).json()["id"]

    fake_pdf = b"%PDF-1.4\n" + b"0" * 50
    for title in ("User Manual", "Quick Start Guide", "Service Manual"):
        resp = client.post(
            "/manuals/",
            data={"product_id": product_id, "title": title, "description": f"{title} text"},
            files={"file": (f"{title}.pdf", fake_pdf, "application/pdf")},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        # POST /manuals/ builds its Manual from an explicit argument list, so a
        # field present only on the schema would be silently dropped here.
        assert resp.json()["title"] == title

    listed = client.get("/manuals/", params={"product_id": product_id}).json()
    assert [m["title"] for m in listed] == [
        "User Manual", "Quick Start Guide", "Service Manual",
    ]


def test_manual_title_is_optional_and_can_be_cleared(client, db_session):
    make_admin(db_session, email="notitle@example.com", password="password123")
    headers = auth_header(client, "notitle@example.com", "password123")
    brand_id = client.post("/brands/", data={"brand_name": "UntitledCo"}, headers=headers).json()["id"]
    product_id = client.post(
        "/products/",
        json={"product_name": "Untitled Widget", "price": "20.00", "brand_id": brand_id},
        headers=headers,
    ).json()["id"]

    # Omitted entirely - every manual predating the column looks like this, and
    # the storefront captions it "Product Manual".
    created = client.post(
        "/manuals/", data={"product_id": product_id}, headers=headers
    ).json()
    assert created["title"] is None

    named = client.put(
        f"/manuals/{created['id']}", json={"title": "Install Guide"}, headers=headers
    ).json()
    assert named["title"] == "Install Guide"

    # An explicit null clears it again - the admin form sends that when the box
    # is emptied, so a mistyped title can actually be removed.
    cleared = client.put(
        f"/manuals/{created['id']}", json={"title": None}, headers=headers
    ).json()
    assert cleared["title"] is None


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
def _make_order_product(
    client, headers, name="Quoted Widget", price="100.00", free_items=None,
    is_purchasable=None, section=None,
):
    brand_id = client.post("/brands/", data={"brand_name": f"OrderCo-{name}"}, headers=headers).json()["id"]
    payload = {"product_name": name, "price": price, "brand_id": brand_id}
    if free_items is not None:
        payload["free_items"] = free_items
    if is_purchasable is not None:
        payload["is_purchasable"] = is_purchasable
    if section is not None:
        payload["section"] = section
    resp = client.post("/products/", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_set(
    client, headers, name="Order Set", price="50.00", old_price=None, items=None,
    option_groups=None,
):
    payload = {"set_name": name, "price": price}
    if old_price is not None:
        payload["old_price"] = old_price
    if items is not None:
        payload["items"] = items
    if option_groups is not None:
        payload["option_groups"] = option_groups
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


def _make_promotion(client, headers, name="Order Promo", price="50.00", old_price=None, start_offset_days=-1, end_offset_days=1, items=None):
    now = datetime.now(timezone.utc)
    payload = {
        "promotion_name": name,
        "price": price,
        "start_date": (now + timedelta(days=start_offset_days)).isoformat(),
        "end_date": (now + timedelta(days=end_offset_days)).isoformat(),
    }
    if old_price is not None:
        payload["old_price"] = old_price
    if items is not None:
        payload["items"] = items
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
# Bundle contents (Promotion/Set members) and product free items
# ---------------------------------------------------------------------------
def _components_of(body, parent_index=0):
    parent = body["items"][parent_index]
    return parent, [i for i in body["items"] if i["parent_item_id"] == parent["id"]]


def test_set_contents_expand_into_zero_priced_component_lines(client, db_session):
    make_admin(db_session, email="bundle1@example.com", password="password123")
    headers = auth_header(client, "bundle1@example.com", "password123")
    glove = _make_order_product(client, headers, name="Glove", price="10.00")
    mirror = _make_order_product(client, headers, name="Mirror", price="7.00")
    set_ = _make_set(
        client, headers, name="Starter Set", price="50.00",
        items=[{"product_id": glove["id"], "qty": 3}, {"product_id": mirror["id"], "qty": 1}],
    )
    assert [i["product_name"] for i in set_["items"]] == ["Glove", "Mirror"]

    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"set_id": set_["id"], "qty": 2}]),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    parent, components = _components_of(body)
    assert parent["product_name"] == "Starter Set"
    assert parent["parent_item_id"] is None
    assert len(body["items"]) == 3  # the set + its two members

    # Member quantities multiply by how many of the set were bought, and every
    # member is $0 - the set's own price already covers them.
    assert [(c["product_name"], c["qty"], c["unit_price"], c["line_amount"]) for c in components] == [
        ("Glove", 6, "0.00", "0.00"),
        ("Mirror", 2, "0.00", "0.00"),
    ]
    assert all(c["product_id"] is not None and c["discount"] == "0.00" for c in components)
    # Totals see only the set's own price.
    assert body["subtotal"] == "100.00"
    assert body["grand_total"] == "100.00"


def test_order_items_come_back_with_components_under_their_own_line(client, db_session):
    """The printed quote, the admin order view and the fallback PDF all just walk
    order["items"] in order, so a component has to arrive directly under the line
    it belongs to - NOT after every paid line, which is the order the rows are
    actually INSERTed in (see Order.items' order_by)."""
    make_admin(db_session, email="bundleorder@example.com", password="password123")
    headers = auth_header(client, "bundleorder@example.com", "password123")
    gift = _make_order_product(client, headers, name="Ride-Along", price="5.00")
    paid = _make_order_product(
        client, headers, name="Paid Thing", price="50.00",
        free_items=[{"product_id": gift["id"], "qty": 1}],
    )
    member = _make_order_product(client, headers, name="Set Member", price="9.00")
    set_ = _make_set(
        client, headers, name="Ordered Set", price="20.00",
        items=[{"product_id": member["id"], "qty": 1}],
    )

    resp = client.post(
        "/orders/",
        json=_order_payload(
            None,
            items=[{"product_id": paid["id"], "qty": 1}, {"set_id": set_["id"], "qty": 1}],
        ),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    names = [i["product_name"] for i in resp.json()["items"]]
    assert names == ["Paid Thing", "Ride-Along", "Ordered Set", "Set Member"]

    # Re-reading the order (a different query path) must group them the same way.
    order_id = resp.json()["id"]
    fetched = client.get(f"/orders/{order_id}", headers=headers).json()
    assert [i["product_name"] for i in fetched["items"]] == names


def test_promotion_contents_expand_into_component_lines(client, db_session):
    make_admin(db_session, email="bundle2@example.com", password="password123")
    headers = auth_header(client, "bundle2@example.com", "password123")
    tip = _make_order_product(client, headers, name="Scaler Tip", price="20.00")
    promo = _make_promotion(
        client, headers, name="Scaler Deal", price="80.00",
        items=[{"product_id": tip["id"], "qty": 2}],
    )

    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"promotion_id": promo["id"], "qty": 1}]),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    parent, components = _components_of(body)
    assert parent["promotion_id"] == promo["id"]
    assert [(c["product_name"], c["qty"], c["line_amount"]) for c in components] == [("Scaler Tip", 2, "0.00")]
    assert body["subtotal"] == "80.00"


def test_product_free_items_ride_along_at_zero(client, db_session):
    make_admin(db_session, email="bundle3@example.com", password="password123")
    headers = auth_header(client, "bundle3@example.com", "password123")
    gift = _make_order_product(client, headers, name="Free Bur", price="5.00")
    main = _make_order_product(
        client, headers, name="Handpiece", price="100.00",
        free_items=[{"product_id": gift["id"], "qty": 2}],
    )
    assert main["free_items"][0]["product_name"] == "Free Bur"

    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"product_id": main["id"], "qty": 3}]),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    parent, components = _components_of(body)
    assert parent["line_amount"] == "300.00"
    assert [(c["product_name"], c["qty"], c["line_amount"]) for c in components] == [("Free Bur", 6, "0.00")]
    # The freebie is free: it never reaches the subtotal, so it can't be
    # discounted or paid for either.
    assert body["subtotal"] == "300.00"


def test_products_are_purchasable_by_default(client, db_session):
    make_admin(db_session, email="giftdefault@example.com", password="password123")
    headers = auth_header(client, "giftdefault@example.com", "password123")
    product = _make_order_product(client, headers, name="Ordinary Item", price="10.00")
    assert product["is_purchasable"] is True


def test_gift_only_product_cannot_be_ordered_on_its_own(client, db_session):
    make_admin(db_session, email="gift1@example.com", password="password123")
    headers = auth_header(client, "gift1@example.com", "password123")
    gift = _make_order_product(
        client, headers, name="Gift Stand", price="1.00", is_purchasable=False,
    )
    assert gift["is_purchasable"] is False

    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"product_id": gift["id"], "qty": 1}]),
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "cannot be ordered on its own" in resp.json()["detail"]


def test_gift_only_product_still_rides_along_free(client, db_session):
    """The whole point of the flag: refused as a paid line, still expanded as a
    $0 component under the product it comes with."""
    make_admin(db_session, email="gift2@example.com", password="password123")
    headers = auth_header(client, "gift2@example.com", "password123")
    gift = _make_order_product(
        client, headers, name="Gift Teeth", price="1.00", is_purchasable=False,
    )
    scanner = _make_order_product(
        client, headers, name="Gift Scanner", price="500.00",
        free_items=[{"product_id": gift["id"], "qty": 5}],
    )

    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"product_id": scanner["id"], "qty": 1}]),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    parent, components = _components_of(body)
    assert parent["line_amount"] == "500.00"
    assert [(c["product_name"], c["qty"], c["line_amount"]) for c in components] == [
        ("Gift Teeth", 5, "0.00")
    ]
    # The freebie rode along without adding anything to the bill.
    assert body["subtotal"] == "500.00"


def test_gift_only_product_hidden_from_catalog_listing(client, db_session):
    make_admin(db_session, email="gift3@example.com", password="password123")
    headers = auth_header(client, "gift3@example.com", "password123")
    gift = _make_order_product(
        client, headers, name="Gift Hidden", price="1.00", is_purchasable=False,
    )
    sellable = _make_order_product(client, headers, name="Gift Visible", price="9.00")

    listed = [p["id"] for p in client.get("/products/", params={"limit": 200}).json()]
    assert sellable["id"] in listed
    assert gift["id"] not in listed

    # The admin screens opt back in - they have to edit and bundle these.
    everything = [
        p["id"]
        for p in client.get(
            "/products/", params={"limit": 200, "include_unpurchasable": True}
        ).json()
    ]
    assert gift["id"] in everything

    # Fetched directly it is still served, so the admin can open it.
    assert client.get(f"/products/{gift['id']}").status_code == 200


def test_gift_only_flag_can_be_turned_back_on(client, db_session):
    """Clearing the flag has to work, not just setting it - a mis-marked product
    would otherwise be stuck off the storefront."""
    make_admin(db_session, email="gift4@example.com", password="password123")
    headers = auth_header(client, "gift4@example.com", "password123")
    gift = _make_order_product(
        client, headers, name="Gift Reversible", price="12.00", is_purchasable=False,
    )

    resp = client.put(
        f"/products/{gift['id']}", json={"is_purchasable": True}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_purchasable"] is True

    listed = [p["id"] for p in client.get("/products/", params={"limit": 200}).json()]
    assert gift["id"] in listed

    order = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"product_id": gift["id"], "qty": 1}]),
        headers=headers,
    )
    assert order.status_code == 201, order.text


def test_products_default_to_machinery(client, db_session):
    """Every product that existed before the machinery/materials split is machinery,
    and so is anything created without an opinion. Materials only ever arrive by
    saying so explicitly - in practice, from the SAP item sync."""
    make_admin(db_session, email="section1@example.com", password="password123")
    headers = auth_header(client, "section1@example.com", "password123")
    product = _make_order_product(client, headers, name="Unstated Item", price="10.00")
    assert product["section"] == "machinery"


def test_materials_are_hidden_from_the_machinery_catalog(client, db_session):
    """The property the section column exists for.

    GET /products/ defaults to machinery rather than to everything, so a materials
    product is invisible to every caller that has not deliberately asked for it -
    the storefront catalog, the sitewide product global, search. Getting this wrong
    is how a consumable ends up on the machinery shop front.
    """
    make_admin(db_session, email="section2@example.com", password="password123")
    headers = auth_header(client, "section2@example.com", "password123")
    machine = _make_order_product(client, headers, name="Dental Chair", price="900.00")
    material = _make_order_product(
        client, headers, name="Composite Syringe", price="9.00", section="materials",
    )

    default_listing = [p["id"] for p in client.get("/products/", params={"limit": 200}).json()]
    assert machine["id"] in default_listing
    assert material["id"] not in default_listing

    materials_only = [
        p["id"]
        for p in client.get("/products/", params={"limit": 200, "section": "materials"}).json()
    ]
    assert material["id"] in materials_only
    assert machine["id"] not in materials_only

    # The admin table spans both halves, the same way it opts into gift-only rows.
    everything = [
        p["id"]
        for p in client.get("/products/", params={"limit": 200, "section": "all"}).json()
    ]
    assert {machine["id"], material["id"]} <= set(everything)

    # Fetched directly it is still served, so admin screens can open it.
    assert client.get(f"/products/{material['id']}").status_code == 200


def test_section_can_be_changed_after_creation(client, db_session):
    """Setting it is not enough: a product filed into the wrong half would
    otherwise be stranded off both storefronts with no way back."""
    make_admin(db_session, email="section3@example.com", password="password123")
    headers = auth_header(client, "section3@example.com", "password123")
    product = _make_order_product(
        client, headers, name="Misfiled Item", price="20.00", section="materials",
    )

    resp = client.put(
        f"/products/{product['id']}", json={"section": "machinery"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["section"] == "machinery"

    listed = [p["id"] for p in client.get("/products/", params={"limit": 200}).json()]
    assert product["id"] in listed


def test_section_rejects_an_unknown_value(client, db_session):
    make_admin(db_session, email="section4@example.com", password="password123")
    headers = auth_header(client, "section4@example.com", "password123")
    brand_id = client.post(
        "/brands/", data={"brand_name": "SectionCo"}, headers=headers
    ).json()["id"]
    resp = client.post(
        "/products/",
        json={"product_name": "Bad Section", "price": "5.00", "brand_id": brand_id,
              "section": "implants"},
        headers=headers,
    )
    assert resp.status_code == 422


def _configurable_set(client, headers, tag, set_price="2000.00"):
    """A set with a fixed item plus two swappable slots: one whose upgrade is
    auto-priced from the product gap, one with an explicit override."""
    fixed = _make_order_product(client, headers, name=f"Camera {tag}", price="900.00")
    base_xray = _make_order_product(client, headers, name=f"Base Xray {tag}", price="700.00")
    up_xray = _make_order_product(client, headers, name=f"Up Xray {tag}", price="1000.00")
    base_light = _make_order_product(client, headers, name=f"Base Light {tag}", price="100.00")
    up_light = _make_order_product(client, headers, name=f"Up Light {tag}", price="500.00")

    set_ = _make_set(
        client, headers, name=f"Configurable {tag}", price=set_price,
        items=[{"product_id": fixed["id"], "qty": 1}],
        option_groups=[
            {"name": "Xray", "choices": [
                {"product_id": base_xray["id"], "is_default": True},
                # No price_delta -> derived: 1000 - 700 = 300
                {"product_id": up_xray["id"]},
            ]},
            {"name": "Light", "choices": [
                {"product_id": base_light["id"], "is_default": True},
                # Explicit override: a 400 gap deliberately sold as +25
                {"product_id": up_light["id"], "price_delta": "25.00"},
            ]},
        ],
    )
    groups = {g["name"]: g for g in set_["option_groups"]}
    return set_, groups


def _choice(group, product_name):
    return next(c for c in group["choices"] if c["product_name"] == product_name)


def test_set_option_deltas_derive_from_products_unless_overridden(client, db_session):
    make_admin(db_session, email="opt1@example.com", password="password123")
    headers = auth_header(client, "opt1@example.com", "password123")
    set_, groups = _configurable_set(client, headers, "D1")

    xray, light = groups["Xray"], groups["Light"]
    # The default is the baseline, so it can never be an upcharge on itself.
    assert _choice(xray, "Base Xray D1")["effective_delta"] == "0"
    # Derived from the live product prices...
    assert _choice(xray, "Up Xray D1")["effective_delta"] == "300.00"
    # ...unless a figure was stored, which wins over the 400.00 raw gap.
    assert _choice(light, "Up Light D1")["effective_delta"] == "25.00"
    assert _choice(light, "Up Light D1")["price_delta"] == "25.00"

    # "was" for the standard configuration counts the default choices as
    # contents: 900 fixed + 700 base xray + 100 base light.
    assert set_["old_price"] == "1700.00"


def test_unconfigured_set_line_falls_back_to_the_defaults(client, db_session):
    make_admin(db_session, email="opt2@example.com", password="password123")
    headers = auth_header(client, "opt2@example.com", "password123")
    set_, _ = _configurable_set(client, headers, "D2")

    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"set_id": set_["id"], "qty": 1}]),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    parent, components = _components_of(resp.json())
    # No upgrades chosen, so the set books at exactly its own price.
    assert parent["unit_price"] == "2000.00"
    assert sorted(c["product_name"] for c in components) == [
        "Base Light D2", "Base Xray D2", "Camera D2",
    ]


def test_configured_set_folds_upgrades_into_its_price_and_swaps_components(client, db_session):
    make_admin(db_session, email="opt3@example.com", password="password123")
    headers = auth_header(client, "opt3@example.com", "password123")
    set_, groups = _configurable_set(client, headers, "D3")

    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{
            "set_id": set_["id"], "qty": 1,
            "options": [
                {"group_id": groups["Xray"]["id"],
                 "choice_id": _choice(groups["Xray"], "Up Xray D3")["id"]},
                {"group_id": groups["Light"]["id"],
                 "choice_id": _choice(groups["Light"], "Up Light D3")["id"]},
            ],
        }]),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    parent, components = _components_of(body)

    # 2000 + 300 derived + 25 overridden, on ONE line rather than three.
    assert parent["unit_price"] == "2325.00"
    assert body["grand_total"] == "2325.00"
    # The chosen products replaced the defaults in the component list.
    assert sorted(c["product_name"] for c in components) == [
        "Camera D3", "Up Light D3", "Up Xray D3",
    ]
    # Components stay free - upgrades are paid for through the parent's price.
    assert all(c["line_amount"] == "0.00" for c in components)
    # "was" tracks the upgraded contents: 900 + 1000 + 500.
    assert parent["list_price"] == "2400.00"


def test_set_option_selection_survives_an_order_edit(client, db_session):
    """The reason the selection is persisted at all: update_order re-prices every
    line from scratch, so without it an upgrade would silently revert."""
    make_admin(db_session, email="opt4@example.com", password="password123")
    headers = auth_header(client, "opt4@example.com", "password123")
    set_, groups = _configurable_set(client, headers, "D4")
    picked = _choice(groups["Xray"], "Up Xray D4")["id"]

    created = client.post(
        "/orders/",
        json=_order_payload(None, items=[{
            "set_id": set_["id"], "qty": 1,
            "options": [{"group_id": groups["Xray"]["id"], "choice_id": picked}],
        }]),
        headers=headers,
    ).json()
    parent, _ = _components_of(created)
    assert parent["unit_price"] == "2300.00"
    assert parent["set_options"] == [{"group_id": groups["Xray"]["id"], "choice_id": picked}]

    # Re-send the stored selection the way the admin screen does.
    resp = client.put(
        f"/orders/{created['id']}",
        json={"items": [{
            "set_id": set_["id"], "qty": 3, "options": parent["set_options"],
        }]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    edited, components = _components_of(resp.json())
    assert edited["unit_price"] == "2300.00"
    assert edited["qty"] == 3
    assert "Up Xray D4" in [c["product_name"] for c in components]


def test_an_option_slot_upgrades_its_included_product_rather_than_adding_one(client, db_session):
    """A slot whose standard choice is also an Included Product describes ONE
    machine with an upgrade path, not two. Regression: the standard build used to
    list the x-ray twice, and the upgraded build listed both the standard machine
    and the Pro that replaced it."""
    make_admin(db_session, email="optdup@example.com", password="password123")
    headers = auth_header(client, "optdup@example.com", "password123")
    sensor = _make_order_product(client, headers, name="Dup Sensor", price="800.00")
    base_xray = _make_order_product(client, headers, name="Dup Xray", price="700.00")
    up_xray = _make_order_product(client, headers, name="Dup Xray Pro", price="1000.00")

    set_ = _make_set(
        client, headers, name="Dup Set", price="1500.00",
        # The x-ray is listed as included AND is the slot's standard choice -
        # exactly how the admin screen now pre-fills a new slot.
        items=[{"product_id": sensor["id"], "qty": 1}, {"product_id": base_xray["id"], "qty": 1}],
        option_groups=[{"name": "Xray", "choices": [
            {"product_id": base_xray["id"], "is_default": True},
            {"product_id": up_xray["id"]},
        ]}],
    )
    groups = {g["name"]: g for g in set_["option_groups"]}

    standard = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"set_id": set_["id"], "qty": 1}]),
        headers=headers,
    ).json()
    _, components = _components_of(standard)
    names = sorted(c["product_name"] for c in components)
    assert names == ["Dup Sensor", "Dup Xray"], names

    upgraded = client.post(
        "/orders/",
        json=_order_payload(None, items=[{
            "set_id": set_["id"], "qty": 1,
            "options": [{"group_id": groups["Xray"]["id"],
                         "choice_id": _choice(groups["Xray"], "Dup Xray Pro")["id"]}],
        }]),
        headers=headers,
    ).json()
    _, components = _components_of(upgraded)
    names = sorted(c["product_name"] for c in components)
    # The Pro replaced the standard x-ray - it is NOT listed alongside it.
    assert names == ["Dup Sensor", "Dup Xray Pro"], names

    # And the "was" price counts the swapped contents once: 800 + 1000.
    parent, _ = _components_of(upgraded)
    assert parent["list_price"] == "1800.00"


def test_an_option_slot_whose_default_is_not_included_still_adds_its_choice(client, db_session):
    """The other legitimate way to build a set: the slot's products aren't listed
    as contents at all, so it claims nothing and simply contributes its choice."""
    make_admin(db_session, email="optadd@example.com", password="password123")
    headers = auth_header(client, "optadd@example.com", "password123")
    fixed = _make_order_product(client, headers, name="Add Camera", price="900.00")
    a = _make_order_product(client, headers, name="Add Light A", price="100.00")
    b = _make_order_product(client, headers, name="Add Light B", price="300.00")

    set_ = _make_set(
        client, headers, name="Add Set", price="1000.00",
        items=[{"product_id": fixed["id"], "qty": 1}],
        option_groups=[{"name": "Light", "choices": [
            {"product_id": a["id"], "is_default": True},
            {"product_id": b["id"]},
        ]}],
    )
    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{"set_id": set_["id"], "qty": 1}]),
        headers=headers,
    )
    _, components = _components_of(resp.json())
    assert sorted(c["product_name"] for c in components) == ["Add Camera", "Add Light A"]


def test_stale_option_choice_is_rejected_not_silently_defaulted(client, db_session):
    make_admin(db_session, email="opt5@example.com", password="password123")
    headers = auth_header(client, "opt5@example.com", "password123")
    set_, groups = _configurable_set(client, headers, "D5")

    resp = client.post(
        "/orders/",
        json=_order_payload(None, items=[{
            "set_id": set_["id"], "qty": 1,
            "options": [{"group_id": groups["Xray"]["id"], "choice_id": 99999}],
        }]),
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "not an option" in resp.json()["detail"]


def test_option_group_must_offer_at_least_one_choice(client, db_session):
    make_admin(db_session, email="opt6@example.com", password="password123")
    headers = auth_header(client, "opt6@example.com", "password123")
    resp = client.post(
        "/sets/",
        json={"set_name": "Empty Group Set", "price": "100.00",
              "option_groups": [{"name": "Nothing", "choices": []}]},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "at least one choice" in resp.json()["detail"]


def test_option_group_always_ends_up_with_exactly_one_default(client, db_session):
    """A group with no default gets one; a second default is demoted rather than
    tripping the partial unique index."""
    make_admin(db_session, email="opt7@example.com", password="password123")
    headers = auth_header(client, "opt7@example.com", "password123")
    a = _make_order_product(client, headers, name="Opt A", price="10.00")
    b = _make_order_product(client, headers, name="Opt B", price="20.00")

    none_flagged = _make_set(
        client, headers, name="No Default Set", price="100.00",
        option_groups=[{"name": "Pick", "choices": [
            {"product_id": a["id"]}, {"product_id": b["id"]},
        ]}],
    )
    flags = [c["is_default"] for c in none_flagged["option_groups"][0]["choices"]]
    assert flags == [True, False]

    both_flagged = _make_set(
        client, headers, name="Two Default Set", price="100.00",
        option_groups=[{"name": "Pick", "choices": [
            {"product_id": a["id"], "is_default": True},
            {"product_id": b["id"], "is_default": True},
        ]}],
    )
    flags = [c["is_default"] for c in both_flagged["option_groups"][0]["choices"]]
    assert flags == [True, False]


def test_set_option_groups_are_replaced_wholesale_on_update(client, db_session):
    make_admin(db_session, email="opt8@example.com", password="password123")
    headers = auth_header(client, "opt8@example.com", "password123")
    set_, groups = _configurable_set(client, headers, "D8")
    keep = _choice(groups["Xray"], "Base Xray D8")["product_id"]

    # Re-submitting a group that keeps an existing product is the ordinary edit -
    # it must not collide with uq_set_option_choice (see replace_option_groups).
    resp = client.put(
        f"/sets/{set_['id']}",
        json={"option_groups": [{"name": "Xray only", "choices": [
            {"product_id": keep, "is_default": True},
        ]}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [g["name"] for g in body["option_groups"]] == ["Xray only"]

    # Omitting the field entirely leaves them alone.
    unchanged = client.put(
        f"/sets/{set_['id']}", json={"description": "touched"}, headers=headers
    ).json()
    assert [g["name"] for g in unchanged["option_groups"]] == ["Xray only"]


def test_set_option_upcharges_are_hidden_from_price_masked_viewers(client, db_session):
    make_admin(db_session, email="opt9@example.com", password="password123")
    headers = auth_header(client, "opt9@example.com", "password123")
    set_, _ = _configurable_set(client, headers, "D9")

    # Anonymous - no price access at all.
    public = client.get(f"/sets/{set_['id']}").json()
    assert public["price"] == "XXXX"
    for group in public["option_groups"]:
        for choice in group["choices"]:
            assert choice["effective_delta"] is None
            assert choice["price_delta"] is None


def test_bundle_components_never_enter_the_discount_base(client, db_session):
    make_admin(db_session, email="bundle4@example.com", password="password123")
    headers = auth_header(client, "bundle4@example.com", "password123")
    member = _make_order_product(client, headers, name="Member Item", price="40.00")
    regular = _make_order_product(client, headers, name="Regular Item", price="100.00")
    set_ = _make_set(
        client, headers, name="Discount Set", price="60.00",
        items=[{"product_id": member["id"], "qty": 1}],
    )

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
    # Only the $100 regular line is discountable: the set is a fixed deal price
    # and its $0 member line adds nothing to either total.
    assert body["subtotal"] == "160.00"
    assert body["discount_amount"] == "10.00"
    assert body["grand_total"] == "150.00"


def test_bundle_old_price_is_the_combined_price_of_its_contents(client, db_session):
    """A bundle's "was" price is what its members cost bought separately - not
    the stored old_price column, which only survives as the fallback for a
    bundle that lists no contents (see bundle_old_price)."""
    make_admin(db_session, email="bundleprice@example.com", password="password123")
    headers = auth_header(client, "bundleprice@example.com", "password123")
    big = _make_order_product(client, headers, name="Big Part", price="70.00")
    small = _make_order_product(client, headers, name="Small Part", price="15.00")

    # Stored old_price is deliberately wrong here - the contents must win.
    set_ = _make_set(
        client, headers, name="Combined Set", price="80.00", old_price="90.00",
        items=[{"product_id": big["id"], "qty": 1}, {"product_id": small["id"], "qty": 2}],
    )
    assert set_["old_price"] == "100.00"  # 70 + 15x2
    assert client.get(f"/sets/{set_['id']}", headers=headers).json()["old_price"] == "100.00"

    # It tracks the members' current prices - repricing one reprices the bundle's
    # "was" figure, with no edit to the set itself.
    client.patch(f"/products/{small['id']}/price", json={"price": "25.00"}, headers=headers)
    assert client.get(f"/sets/{set_['id']}", headers=headers).json()["old_price"] == "120.00"

    # And it's what the order line snapshots as its discount, so the printed
    # quote shows the real saving.
    order = client.post(
        "/orders/", json=_order_payload(None, items=[{"set_id": set_["id"], "qty": 2}]), headers=headers
    ).json()
    parent = order["items"][0]
    assert parent["discount_type"] == "cash"
    assert parent["discount"] == "40.00"  # 120 combined - 80 charged
    assert order["subtotal"] == "160.00"  # still only the bundle price x2

    # No contents -> the manually entered old_price still stands.
    plain = _make_set(client, headers, name="Plain Set", price="30.00", old_price="45.00")
    assert plain["old_price"] == "45.00"


def test_bundle_old_price_still_reports_contents_that_cost_less_than_the_bundle(client, db_session):
    """Contents win even when they add up to less than the bundle's own price -
    the figure stays truthful about what's inside. It must not leak out as a
    negative discount: the order line just books at the bundle price."""
    make_admin(db_session, email="bundleprice2@example.com", password="password123")
    headers = auth_header(client, "bundleprice2@example.com", "password123")
    cheap = _make_order_product(client, headers, name="Cheap Part", price="5.00")

    set_ = _make_set(
        client, headers, name="Overpriced Set", price="50.00", old_price="60.00",
        items=[{"product_id": cheap["id"], "qty": 1}],
    )
    assert set_["old_price"] == "5.00"  # contents, not the stored 60.00

    order = client.post(
        "/orders/", json=_order_payload(None, items=[{"set_id": set_["id"], "qty": 1}]), headers=headers
    ).json()
    parent = order["items"][0]
    assert parent["discount"] == "0.00"
    assert parent["line_amount"] == "50.00"
    assert order["grand_total"] == "50.00"


def test_bundle_contents_reject_unknown_and_duplicate_products(client, db_session):
    make_admin(db_session, email="bundle5@example.com", password="password123")
    headers = auth_header(client, "bundle5@example.com", "password123")
    product = _make_order_product(client, headers, name="Real Item", price="10.00")

    unknown = client.post(
        "/sets/", json={"set_name": "Bad Set", "price": "10.00", "items": [{"product_id": 999999, "qty": 1}]},
        headers=headers,
    )
    assert unknown.status_code == 400
    assert "does not exist" in unknown.json()["detail"]

    duplicate = client.post(
        "/sets/",
        json={
            "set_name": "Dup Set", "price": "10.00",
            "items": [{"product_id": product["id"], "qty": 1}, {"product_id": product["id"], "qty": 2}],
        },
        headers=headers,
    )
    assert duplicate.status_code == 400
    assert "more than once" in duplicate.json()["detail"]


def test_product_cannot_come_free_with_itself(client, db_session):
    make_admin(db_session, email="bundle6@example.com", password="password123")
    headers = auth_header(client, "bundle6@example.com", "password123")
    product = _make_order_product(client, headers, name="Self Gift", price="10.00")

    resp = client.put(
        f"/products/{product['id']}",
        json={"free_items": [{"product_id": product["id"], "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "itself" in resp.json()["detail"]


def test_updating_contents_replaces_them_but_omitting_leaves_them_alone(client, db_session):
    make_admin(db_session, email="bundle7@example.com", password="password123")
    headers = auth_header(client, "bundle7@example.com", "password123")
    first = _make_order_product(client, headers, name="First Item", price="10.00")
    second = _make_order_product(client, headers, name="Second Item", price="20.00")
    set_ = _make_set(
        client, headers, name="Editable Set", price="25.00",
        items=[{"product_id": first["id"], "qty": 1}],
    )

    # Sent -> replaced wholesale.
    replaced = client.put(
        f"/sets/{set_['id']}", json={"items": [{"product_id": second["id"], "qty": 4}]}, headers=headers
    )
    assert replaced.status_code == 200, replaced.text
    assert [(i["product_name"], i["qty"]) for i in replaced.json()["items"]] == [("Second Item", 4)]

    # Omitted -> left alone.
    renamed = client.put(f"/sets/{set_['id']}", json={"set_name": "Renamed Set"}, headers=headers)
    assert renamed.status_code == 200, renamed.text
    assert [i["product_name"] for i in renamed.json()["items"]] == ["Second Item"]

    # Emptied explicitly.
    emptied = client.put(f"/sets/{set_['id']}", json={"items": []}, headers=headers)
    assert emptied.status_code == 200, emptied.text
    assert emptied.json()["items"] == []


def test_editing_contents_can_keep_a_product_that_is_already_in_the_bundle(client, db_session):
    """The most ordinary edit there is - "add one more product to this set" -
    resubmits the members that are already saved. Replacing the collection by
    plain assignment made SQLAlchemy INSERT those before DELETEing the old rows,
    which tripped the (owner, product_id) unique constraint as a 500; see
    replace_bundle_rows."""
    make_admin(db_session, email="bundlekeep@example.com", password="password123")
    headers = auth_header(client, "bundlekeep@example.com", "password123")
    kept = _make_order_product(client, headers, name="Kept Item", price="10.00")
    added = _make_order_product(client, headers, name="Added Item", price="20.00")
    dropped = _make_order_product(client, headers, name="Dropped Item", price="30.00")

    set_ = _make_set(
        client, headers, name="Growing Set", price="25.00",
        items=[{"product_id": kept["id"], "qty": 1}, {"product_id": dropped["id"], "qty": 1}],
    )

    resp = client.put(
        f"/sets/{set_['id']}",
        json={"items": [
            {"product_id": kept["id"], "qty": 3},      # kept, quantity changed
            {"product_id": added["id"], "qty": 1},     # new
        ]},                                            # `dropped` falls out
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert [(i["product_name"], i["qty"]) for i in resp.json()["items"]] == [
        ("Kept Item", 3), ("Added Item", 1)
    ]

    # Same edit shape on a product's free items, which has its own unique constraint.
    paid = _make_order_product(
        client, headers, name="Gift Giver", price="99.00",
        free_items=[{"product_id": kept["id"], "qty": 1}],
    )
    resp = client.put(
        f"/products/{paid['id']}",
        json={"free_items": [
            {"product_id": kept["id"], "qty": 2},
            {"product_id": added["id"], "qty": 1},
        ]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert [(i["product_name"], i["qty"]) for i in resp.json()["free_items"]] == [
        ("Kept Item", 2), ("Added Item", 1)
    ]


def test_deleting_a_product_drops_it_from_bundles_but_not_from_history(client, db_session):
    make_admin(db_session, email="bundle8@example.com", password="password123")
    headers = auth_header(client, "bundle8@example.com", "password123")
    member = _make_order_product(client, headers, name="Doomed Item", price="15.00")
    set_ = _make_set(
        client, headers, name="Surviving Set", price="30.00",
        items=[{"product_id": member["id"], "qty": 1}],
    )
    order = client.post(
        "/orders/", json=_order_payload(None, items=[{"set_id": set_["id"], "qty": 1}]), headers=headers
    ).json()

    assert client.delete(f"/products/{member['id']}", headers=headers).status_code == 204

    # The set simply loses that member (ON DELETE CASCADE on the join row)...
    assert client.get(f"/sets/{set_['id']}", headers=headers).json()["items"] == []
    # ...while the already-placed order keeps its snapshot of what was included.
    placed = client.get(f"/orders/{order['id']}", headers=headers).json()
    assert [i["product_name"] for i in placed["items"]] == ["Surviving Set", "Doomed Item"]


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


def _async_return(value):
    """Stub for an awaited collaborator (the Bakong check, a Telegram send). Written as a
    real coroutine function rather than a lambda so it can stand in for one."""

    async def _stub(*args, **kwargs):
        return value

    return _stub


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
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_INFORMATION", "")
    monkeypatch.setattr(settings, "BAKONG_ACQUIRING_BANK", "")


def test_khqr_unavailable_when_not_configured(client, db_session, monkeypatch):
    _configure_bakong(monkeypatch, account_id="")
    make_admin(db_session, email="noqradmin@example.com", password="password123")
    admin_headers = auth_header(client, "noqradmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="NoQrWidget")

    make_customer(db_session, email="noqrcust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "noqrcust@example.com", "customerpass1")

    resp = client.post(
        "/orders/checkout", json=_order_payload(product["id"], payment_method="khqr"),
        headers=cust_headers,
    )
    assert resp.status_code == 400
    assert "not available" in resp.json()["detail"]


def test_orders_endpoint_refuses_khqr_and_creates_nothing(client, db_session, monkeypatch):
    """POST /orders/ makes documents, not purchases. Routing a pay-by-QR order through it
    would write exactly the unpaid order this design exists to prevent."""
    _configure_bakong(monkeypatch)
    make_admin(db_session, email="nokhqrpost@example.com", password="password123")
    admin_headers = auth_header(client, "nokhqrpost@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="NoPostWidget")

    make_customer(db_session, email="nokhqrcust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "nokhqrcust@example.com", "customerpass1")

    before = len(client.get("/orders/", headers=admin_headers).json())
    resp = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="khqr"), headers=cust_headers
    )
    assert resp.status_code == 400
    assert "checkout" in resp.json()["detail"]
    assert len(client.get("/orders/", headers=admin_headers).json()) == before


def test_customer_khqr_checkout_creates_no_order(client, db_session, monkeypatch):
    """The core rule: pressing Confirm Purchase with KHQR issues a QR and NOTHING else.
    No order, no order items, nothing in the customer's own order list."""
    from decimal import Decimal

    from app.services.khqr import _crc16_ccitt

    _configure_bakong(monkeypatch)
    make_admin(db_session, email="qradmin@example.com", password="password123")
    admin_headers = auth_header(client, "qradmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="QrWidget", price="75.00")

    make_customer(db_session, email="qrcust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "qrcust@example.com", "customerpass1")

    orders_before = len(client.get("/orders/", headers=admin_headers).json())
    resp = client.post(
        "/orders/checkout", json=_order_payload(product["id"], payment_method="khqr"),
        headers=cust_headers,
    )
    assert resp.status_code == 201, resp.text
    checkout = resp.json()

    # A checkout is NOT an order - it has no order number, no items, no payment status.
    assert set(checkout) == {"id", "reference", "grand_total", "khqr_string", "expires_at"}
    assert Decimal(checkout["grand_total"]) == Decimal("150.00")

    payload = checkout["khqr_string"]
    assert payload.startswith("000201")  # tag 00, len 02, version "01"
    assert "testmerchant@devb" in payload
    assert "5303840" in payload  # currency tag: USD
    assert "5406150.00" in payload  # amount tag: 2 x $75.00
    assert checkout["reference"] in payload  # bill number carries the checkout reference
    assert payload[-8:-4] == "6304"
    assert payload[-4:] == _crc16_ccitt(payload[:-4])

    # Nothing was written: not for staff, and not in the customer's own history.
    assert len(client.get("/orders/", headers=admin_headers).json()) == orders_before
    assert client.get("/orders/mine", headers=cust_headers).json() == []


def test_checkout_becomes_an_order_only_once_paid(client, db_session, monkeypatch):
    """The order is created by the payment, not by the checkout - and exactly once, no
    matter how many times the browser polls."""
    from decimal import Decimal

    _fast_alert_wait(monkeypatch)
    _configure_bakong(monkeypatch)
    make_admin(db_session, email="payadmin@example.com", password="password123")
    admin_headers = auth_header(client, "payadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="PayWidget")

    make_customer(db_session, email="paycust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "paycust@example.com", "customerpass1")

    checkout = client.post(
        "/orders/checkout", json=_order_payload(product["id"], payment_method="khqr"),
        headers=cust_headers,
    ).json()

    # Unpaid: the poll reports it and still writes nothing.
    resp = client.get(f"/orders/checkout/{checkout['id']}/payment-status", headers=cust_headers)
    assert resp.status_code == 200
    assert resp.json() == {"payment_status": "unpaid", "order": None}
    assert client.get("/orders/mine", headers=cust_headers).json() == []

    # Someone else's account can't poll it, and gets a 404 rather than a 403 so it can't
    # be used to discover which checkouts exist.
    make_customer(db_session, email="otherpay@example.com", password="customerpass1", access_permission=True)
    other_headers = customer_auth_header(client, "otherpay@example.com", "customerpass1")
    resp = client.get(f"/orders/checkout/{checkout['id']}/payment-status", headers=other_headers)
    assert resp.status_code == 404

    # The payment lands.
    monkeypatch.setattr("app.routers.orders.check_bakong_payment", _async_return(True))
    resp = client.get(f"/orders/checkout/{checkout['id']}/payment-status", headers=cust_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_status"] == "paid"

    order = body["order"]
    assert order["order_type"] == "order"
    assert order["payment_method"] == "khqr"
    assert order["payment_status"] == "paid"
    assert order["paid_at"] is not None
    # The order carries the reference the bank knows the payment by - order_number only
    # came into being just now, so it is not what the payment was made against.
    assert order["payment_reference"] == checkout["reference"]
    assert order["khqr_string"] == checkout["khqr_string"]
    assert [i["product_name"] for i in order["items"]] == ["PayWidget"]
    assert Decimal(order["grand_total"]) == Decimal(checkout["grand_total"])

    # It exists exactly once, and polling again returns the same order rather than
    # making a second one for the same payment.
    assert [o["id"] for o in client.get("/orders/mine", headers=cust_headers).json()] == [order["id"]]
    again = client.get(f"/orders/checkout/{checkout['id']}/payment-status", headers=cust_headers).json()
    assert again["payment_status"] == "paid"
    assert again["order"]["id"] == order["id"]
    assert len(client.get("/orders/mine", headers=cust_headers).json()) == 1


def test_staff_see_outstanding_checkouts_and_can_confirm_one(client, db_session, monkeypatch):
    """The back-office safety net: when automatic confirmation can't see a payment that
    plainly landed, staff must be able to find the attempt and turn it into an order.
    Without this a failed auto-confirm means money received against nothing visible."""
    from decimal import Decimal

    _fast_alert_wait(monkeypatch)
    _configure_bakong(monkeypatch)
    make_admin(db_session, email="recadmin@example.com", password="password123")
    admin_headers = auth_header(client, "recadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="RecWidget", price="40.00")

    make_customer(db_session, email="reccust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "reccust@example.com", "customerpass1")

    checkout = client.post(
        "/orders/checkout", json=_order_payload(product["id"], payment_method="khqr"),
        headers=cust_headers,
    ).json()

    # Staff can see it, with enough detail to match against a bank statement.
    listing = client.get("/orders/checkouts", headers=admin_headers).json()
    assert [c["id"] for c in listing] == [checkout["id"]]
    row = listing[0]
    assert row["reference"] == checkout["reference"]
    assert Decimal(row["grand_total"]) == Decimal("80.00")
    assert row["is_expired"] is False
    assert [(i["product_name"], i["qty"]) for i in row["items"]] == [("RecWidget", 2)]

    # A customer must not be able to read the back-office list. 401 rather than 403:
    # a customer token isn't a staff user at all, so it fails authentication before
    # price_listing is ever considered.
    assert client.get("/orders/checkouts", headers=cust_headers).status_code == 401

    # Staff confirm the payment by hand - the order is written, exactly as the automatic
    # path would have written it, and recorded against the staff member who vouched.
    resp = client.post(f"/orders/checkout/{checkout['id']}/confirm", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    order = resp.json()
    assert order["payment_status"] == "paid"
    assert order["payment_reference"] == checkout["reference"]
    # UserMini deliberately carries no email - the name is what an admin screen needs.
    assert order["updated_by"]["user_name"] == "Admin User"
    assert [i["product_name"] for i in order["items"]] == ["RecWidget"]

    # It leaves the outstanding list, and the customer now has exactly one order.
    assert client.get("/orders/checkouts", headers=admin_headers).json() == []
    assert len(client.get("/orders/mine", headers=cust_headers).json()) == 1

    # Confirming again is idempotent - no second order for one payment.
    again = client.post(f"/orders/checkout/{checkout['id']}/confirm", headers=admin_headers)
    assert again.status_code == 200
    assert again.json()["id"] == order["id"]
    assert len(client.get("/orders/mine", headers=cust_headers).json()) == 1


def test_expired_checkout_reports_expired(client, db_session, monkeypatch):
    """Once the QR's own expiry passes unpaid the code is dead, and the browser is told
    so instead of polling forever."""
    from app.models import PendingCheckout

    _configure_bakong(monkeypatch)
    make_admin(db_session, email="expadmin@example.com", password="password123")
    admin_headers = auth_header(client, "expadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="ExpWidget")

    make_customer(db_session, email="expcust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "expcust@example.com", "customerpass1")

    checkout = client.post(
        "/orders/checkout", json=_order_payload(product["id"], payment_method="khqr"),
        headers=cust_headers,
    ).json()

    row = db_session.query(PendingCheckout).filter_by(id=checkout["id"]).first()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    resp = client.get(f"/orders/checkout/{checkout['id']}/payment-status", headers=cust_headers)
    assert resp.json() == {"payment_status": "expired", "order": None}
    assert client.get("/orders/mine", headers=cust_headers).json() == []


def test_sweep_creates_the_order_when_the_browser_never_saw_the_payment(
    client, db_session, monkeypatch
):
    """The reason the sweep exists: a customer who pays and closes the tab must still end
    up with an order, or the money is received against nothing at all."""
    import asyncio

    from app.models import PendingCheckout
    from app.services import checkout_sweep

    _fast_alert_wait(monkeypatch)
    _configure_bakong(monkeypatch)
    make_admin(db_session, email="sweepadmin@example.com", password="password123")
    admin_headers = auth_header(client, "sweepadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="SweepWidget")

    make_customer(db_session, email="sweepcust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "sweepcust@example.com", "customerpass1")

    checkout = client.post(
        "/orders/checkout", json=_order_payload(product["id"], payment_method="khqr"),
        headers=cust_headers,
    ).json()
    assert client.get("/orders/mine", headers=cust_headers).json() == []

    # The customer pays; nobody is polling.
    monkeypatch.setattr("app.routers.orders.check_bakong_payment", _async_return(True))
    # _reconcile_once imports this at call time (import cycle), so the module attribute
    # is what has to be replaced, not a name bound inside checkout_sweep.
    monkeypatch.setattr("app.services.telegram.deliver_order_alert", _async_return(None))
    asyncio.run(checkout_sweep._reconcile_once())

    mine = client.get("/orders/mine", headers=cust_headers).json()
    assert len(mine) == 1
    assert mine[0]["payment_status"] == "paid"
    assert mine[0]["payment_reference"] == checkout["reference"]

    # And the pending row is linked, so a later poll or sweep can't duplicate it.
    row = db_session.query(PendingCheckout).filter_by(id=checkout["id"]).first()
    db_session.refresh(row)
    assert row.order_id == mine[0]["id"]


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


def test_khqr_individual_carries_all_three_payee_sub_fields(monkeypatch):
    """A bank account (as opposed to a Bakong wallet alias) is only identified by
    tag 29 as a whole: sub-00 names the bank, sub-01 the account number. Dropping
    sub-01 would produce a QR that routes to ABA and nobody in particular."""
    from decimal import Decimal

    from app.config import settings
    from app.services.khqr import _parse_tlv, build_khqr
    from app.services.khqr import expiry_minutes as khqr_expiry_minutes

    monkeypatch.setattr(settings, "KHQR_STATIC_TEMPLATE", "irrelevant-when-account-id-set")
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_ID", "abaakhppxxx@abaa")
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_INFORMATION", "004613623")
    monkeypatch.setattr(settings, "BAKONG_ACQUIRING_BANK", "ABA Bank")

    payload, _ = build_khqr(Decimal("12.50"), bill_number="000123")
    fields = dict(_parse_tlv(payload))

    # BAKONG_ACCOUNT_ID wins over a template that's also set.
    assert dict(_parse_tlv(fields["29"])) == {
        "00": "abaakhppxxx@abaa",
        "01": "004613623",
        "02": "ABA Bank",
    }
    # Nothing outside the spec's own tags - notably no proprietary tag 40, which is
    # what the static-template path would have copied in from ABA's QR.
    assert set(fields) == {"00", "01", "29", "52", "53", "54", "58", "59", "60", "62", "99", "63"}
    assert fields["01"] == "12"  # dynamic
    assert fields["54"] == "12.50"
    assert fields["59"] == "EB DENTAL"  # our own name, not the bank's copy

    # Tag 99 carries creation + expiry in ms; the spec makes expiry mandatory here.
    stamps = dict(_parse_tlv(fields["99"]))
    assert len(stamps["00"]) == 13 and len(stamps["01"]) == 13
    gap_minutes = (int(stamps["01"]) - int(stamps["00"])) / 60000
    # The admin-editable setting, whose default is settings.KHQR_EXPIRY_MINUTES.
    assert gap_minutes == khqr_expiry_minutes()


def test_khqr_expired_reads_the_payloads_own_expiry(monkeypatch):
    """POST /orders/{id}/khqr reuses a stored QR rather than minting one, which would
    strand an order on a code every wallet refuses once its expiry passed. Anything
    with no readable expiry (PayWay QRs, pre-expiry rows) must read as still-valid."""
    from decimal import Decimal

    from app.config import settings
    from app.services import khqr as khqr_module
    from app.services.khqr import build_khqr, khqr_expired

    monkeypatch.setattr(settings, "KHQR_STATIC_TEMPLATE", "")
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_ID", "john_smith@devb")
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_INFORMATION", "")
    monkeypatch.setattr(settings, "BAKONG_ACQUIRING_BANK", "")

    fresh, _ = build_khqr(Decimal("1.00"), bill_number="000001")
    assert not khqr_expired(fresh)

    # Forcing an already-past expiry: patch the accessor rather than the setting it
    # reads, because the spec puts a minimum of 1 on khqr_expiry_minutes - a negative
    # value can't be saved through the API, only manufactured here.
    monkeypatch.setattr(khqr_module, "expiry_minutes", lambda: -1)
    stale, _ = build_khqr(Decimal("1.00"), bill_number="000002")
    assert khqr_expired(stale)

    # No tag 99 at all, and outright junk - both "not expired", never a false replace.
    assert not khqr_expired("00020101021158021KH")
    assert not khqr_expired("FAKEQR|1.00|000003")


def test_khqr_omits_payee_sub_fields_that_are_not_configured(monkeypatch):
    """A plain `name@bank` wallet alias needs no account number or bank name, and
    empty TLVs would be malformed rather than merely redundant."""
    from decimal import Decimal

    from app.config import settings
    from app.services.khqr import _parse_tlv, build_khqr

    monkeypatch.setattr(settings, "KHQR_STATIC_TEMPLATE", "")
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_ID", "john_smith@devb")
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_INFORMATION", "")
    monkeypatch.setattr(settings, "BAKONG_ACQUIRING_BANK", "")

    payload, _ = build_khqr(Decimal("1.00"), bill_number="000001")
    fields = dict(_parse_tlv(payload))
    assert dict(_parse_tlv(fields["29"])) == {"00": "john_smith@devb"}


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


def test_customer_khqr_checkout_via_payway(client, db_session, monkeypatch):
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
        "/orders/checkout", json=_order_payload(product["id"], payment_method="khqr"),
        headers=cust_headers,
    )
    assert resp.status_code == 201, resp.text
    checkout = resp.json()
    # PayWay is issued against the checkout reference - there is no order number yet.
    assert checkout["khqr_string"] == f"FAKEQR|120.00|{checkout['reference']}"
    assert client.get("/orders/mine", headers=cust_headers).json() == []


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
        "/orders/checkout", json=_order_payload(product["id"], payment_method="khqr"),
        headers=cust_headers,
    )
    assert resp.status_code == 400
    assert "choose Cash" in resp.json()["detail"]


def test_payway_checkout_poll_creates_the_order(client, db_session, monkeypatch):
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

    checkout = client.post(
        "/orders/checkout", json=_order_payload(product["id"], payment_method="khqr"),
        headers=cust_headers,
    ).json()

    async def _not_paid(tran_id):
        return False

    async def _paid(tran_id):
        # A PayWay checkout stores no md5, which is what routes it here - and it is
        # checked by the checkout's reference, since no order number exists yet.
        assert tran_id == checkout["reference"]
        return True

    monkeypatch.setattr("app.routers.orders.check_payway_payment", _not_paid)
    resp = client.get(f"/orders/checkout/{checkout['id']}/payment-status", headers=cust_headers)
    assert resp.json() == {"payment_status": "unpaid", "order": None}

    monkeypatch.setattr("app.routers.orders.check_payway_payment", _paid)
    resp = client.get(f"/orders/checkout/{checkout['id']}/payment-status", headers=cust_headers)
    body = resp.json()
    assert body["payment_status"] == "paid"

    # The order exists now, and is persisted paid rather than merely reported so.
    resp = client.get(f"/orders/{body['order']['id']}", headers=admin_headers)
    assert resp.json()["payment_status"] == "paid"
    assert resp.json()["paid_at"] is not None
    assert resp.json()["khqr_md5"] is None


def test_payment_status_poll_rejected_on_non_khqr_rows(client, db_session, monkeypatch):
    """There is nothing for the QR poll to ask about on a row with no QR - that's
    still a 400. Marking it paid is a different matter and is allowed now (staff take
    cash against a quote); see test_any_order_can_be_marked_paid."""
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="npadmin@example.com", password="password123")
    admin_headers = auth_header(client, "npadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="NpWidget")

    quote = client.post("/orders/", json=_order_payload(product["id"]), headers=admin_headers).json()

    resp = client.get(f"/orders/{quote['id']}/payment-status", headers=admin_headers)
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


def test_orders_mine_is_scoped_to_the_caller(client, db_session):
    """GET /orders/mine is what the storefront's account drawer lists. A customer
    has no price_listing permission, so it must work off the token's own principal
    rather than the staff-only GET /orders/, and must never leak another account's
    orders."""
    make_admin(db_session, email="mineadmin@example.com", password="password123")
    admin_headers = auth_header(client, "mineadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="Mine Widget")

    make_customer(db_session, email="minecust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "minecust@example.com", "customerpass1")
    make_customer(db_session, email="othercust@example.com", password="customerpass1", access_permission=True)
    other_headers = customer_auth_header(client, "othercust@example.com", "customerpass1")

    staff_order = client.post("/orders/", json=_order_payload(product["id"]), headers=admin_headers)
    assert staff_order.status_code == 201, staff_order.text
    mine = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="cash"), headers=cust_headers
    )
    assert mine.status_code == 201, mine.text
    theirs = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="cash"), headers=other_headers
    )
    assert theirs.status_code == 201, theirs.text

    cust_ids = [o["id"] for o in client.get("/orders/mine", headers=cust_headers).json()]
    assert cust_ids == [mine.json()["id"]]

    # Staff see the quotes they recorded themselves, not every order in the system.
    staff_ids = [o["id"] for o in client.get("/orders/mine", headers=admin_headers).json()]
    assert staff_ids == [staff_order.json()["id"]]


def test_orders_mine_requires_a_token(client):
    assert client.get("/orders/mine").status_code == 401


def test_orders_mine_detail_is_scoped_to_the_caller(client, db_session):
    """GET /orders/mine/{id} backs the account drawer's order detail view and the PDF it
    re-prints from, so it must return the full line items - and 404 (not 403) on an
    order the caller doesn't own, so it can't be used to probe which ids exist."""
    make_admin(db_session, email="minedetailadmin@example.com", password="password123")
    admin_headers = auth_header(client, "minedetailadmin@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="Mine Detail Widget")

    make_customer(db_session, email="minedetail@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "minedetail@example.com", "customerpass1")

    staff_order = client.post("/orders/", json=_order_payload(product["id"]), headers=admin_headers)
    assert staff_order.status_code == 201, staff_order.text
    mine = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="cash"), headers=cust_headers
    )
    assert mine.status_code == 201, mine.text

    resp = client.get(f"/orders/mine/{mine.json()['id']}", headers=cust_headers)
    assert resp.status_code == 200, resp.text
    assert [i["product_id"] for i in resp.json()["items"]] == [product["id"]]

    assert client.get(f"/orders/mine/{staff_order.json()['id']}", headers=cust_headers).status_code == 404
    assert client.get(f"/orders/mine/{mine.json()['id']}", headers=admin_headers).status_code == 404


# ---------------------------------------------------------------------------
# Editing an order, and the freeze that lands with payment
# ---------------------------------------------------------------------------
def test_staff_can_edit_an_unpaid_order(client, db_session, monkeypatch):
    """The admin Orders page's edit modal: clinic details, terms and the item list
    itself. Items are REPLACED wholesale and re-priced from the current catalogue -
    the request only ever carries ids and quantities."""
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="editadmin@example.com", password="password123")
    headers = auth_header(client, "editadmin@example.com", "password123")
    widget = _make_order_product(client, headers, name="EditWidget", price="100.00")
    gadget = _make_order_product(client, headers, name="EditGadget", price="25.00")

    order = client.post("/orders/", json=_order_payload(widget["id"]), headers=headers).json()
    assert order["grand_total"] == "200.00"  # 2 x $100

    resp = client.put(
        f"/orders/{order['id']}",
        json={
            "clinic_name": "Renamed Clinic",
            "contact_person": "Dr Edit",
            "address": "9 New Street",
            "payment_term": "Net 30",
            "items": [
                {"product_id": widget["id"], "qty": 3},
                {"product_id": gadget["id"], "qty": 2},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["clinic_name"] == "Renamed Clinic"
    assert body["contact_person"] == "Dr Edit"
    assert body["address"] == "9 New Street"
    assert body["payment_term"] == "Net 30"
    # Re-priced server-side: 3 x $100 + 2 x $25.
    assert body["subtotal"] == "350.00"
    assert body["grand_total"] == "350.00"
    assert sorted((i["product_id"], i["qty"]) for i in body["items"]) == sorted(
        [(gadget["id"], 2), (widget["id"], 3)]
    )
    # The replaced rows are really gone, not just detached.
    assert len(client.get(f"/orders/{order['id']}", headers=headers).json()["items"]) == 2


def test_editing_an_order_re_expands_bundle_contents(client, db_session, monkeypatch):
    """A product's freebies are regenerated from the parent line on every edit, so an
    edited order carries the same $0 component rows a freshly placed one does."""
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="editbundle@example.com", password="password123")
    headers = auth_header(client, "editbundle@example.com", "password123")
    freebie = _make_order_product(client, headers, name="EditFreebie", price="10.00")
    main = _make_order_product(
        client, headers, name="EditMain", price="100.00",
        free_items=[{"product_id": freebie["id"], "qty": 1}],
    )
    plain = _make_order_product(client, headers, name="EditPlain", price="40.00")

    order = client.post(
        "/orders/",
        json=_order_payload(plain["id"], items=[{"product_id": plain["id"], "qty": 1}]),
        headers=headers,
    ).json()
    assert len(order["items"]) == 1

    body = client.put(
        f"/orders/{order['id']}",
        json={"items": [{"product_id": main["id"], "qty": 2}]},
        headers=headers,
    ).json()
    paid_lines = [i for i in body["items"] if i["parent_item_id"] is None]
    components = [i for i in body["items"] if i["parent_item_id"] is not None]
    assert len(paid_lines) == 1 and paid_lines[0]["qty"] == 2
    # 2 of a product that comes with 1 freebie = 2 freebies, at $0.
    assert len(components) == 1
    assert components[0]["qty"] == 2
    assert components[0]["line_amount"] == "0.00"
    assert components[0]["parent_item_id"] == paid_lines[0]["id"]
    assert body["grand_total"] == "200.00"  # the freebie moves nothing


def test_editing_the_discount_alone_recomputes_the_total(client, db_session, monkeypatch):
    """Changing only one half of the discount keeps the other half, and the totals are
    recomputed off the order's existing lines without resending them."""
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="editdisc@example.com", password="password123")
    headers = auth_header(client, "editdisc@example.com", "password123")
    product = _make_order_product(client, headers, name="DiscWidget", price="100.00")

    order = client.post(
        "/orders/",
        json=_order_payload(product["id"], discount_type="percent", discount_value=10),
        headers=headers,
    ).json()
    assert order["grand_total"] == "180.00"

    body = client.put(
        f"/orders/{order['id']}", json={"discount_value": 25}, headers=headers
    ).json()
    assert body["discount_type"] == "percent"  # not reset by sending only the value
    assert body["discount_amount"] == "50.00"
    assert body["grand_total"] == "150.00"

    # A percent value that only becomes invalid once combined with the stored type.
    resp = client.put(f"/orders/{order['id']}", json={"discount_value": 150}, headers=headers)
    assert resp.status_code == 400


def test_editing_an_order_requires_product_management_for_a_discount(client, db_session, monkeypatch):
    """Same gate as placing one: price_listing can edit an order, but not hand out money."""
    from app.core.security import hash_password
    from app.models import User

    _fast_alert_wait(monkeypatch)
    db_session.add(User(
        user_name="Pricing Only Editor",
        email="editpricing@example.com",
        hashed_password=hash_password("password123"),
        is_active=True,
        is_verified=True,
        price_listing=True,
    ))
    db_session.commit()
    pricing_headers = auth_header(client, "editpricing@example.com", "password123")
    make_admin(db_session, email="editadmin2@example.com", password="password123")
    admin_headers = auth_header(client, "editadmin2@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="EditPermWidget", price="100.00")

    order = client.post("/orders/", json=_order_payload(product["id"]), headers=admin_headers).json()

    assert client.put(
        f"/orders/{order['id']}",
        json={"discount_type": "cash", "discount_value": 10},
        headers=pricing_headers,
    ).status_code == 403
    # Everything else about the order is still theirs to fix.
    assert client.put(
        f"/orders/{order['id']}", json={"clinic_name": "Fixed Typo Clinic"}, headers=pricing_headers,
    ).status_code == 200


def test_any_order_can_be_marked_paid(client, db_session, monkeypatch):
    """Payment is no longer KHQR-only: staff take cash at the counter against a quote,
    and marking it paid is what turns the printed document into a receipt. order_type
    stays "quote" - how the row came to exist doesn't change when it's settled."""
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="cashpaid@example.com", password="password123")
    headers = auth_header(client, "cashpaid@example.com", "password123")
    product = _make_order_product(client, headers, name="CashWidget", price="100.00")

    order = client.post("/orders/", json=_order_payload(product["id"]), headers=headers).json()
    assert order["order_type"] == "quote"
    assert order["payment_method"] is None

    resp = client.put(f"/orders/{order['id']}", json={"payment_status": "paid"}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_status"] == "paid"
    assert body["paid_at"] is not None
    assert body["order_type"] == "quote"


def test_a_paid_order_stays_editable(client, db_session, monkeypatch):
    """Paid orders are editable and deletable (changed 2026-08-11 - _reject_if_paid is
    now a no-op). This used to 409 on the reasoning that a receipt had been issued
    against those exact figures; staff needing to correct a real order after taking
    payment won out. The customer's already-printed receipt will not match afterwards -
    updated_by is the only record that an amendment happened.

    `status` is the one exception - see the test below."""
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="frozen@example.com", password="password123")
    headers = auth_header(client, "frozen@example.com", "password123")
    product = _make_order_product(client, headers, name="FrozenWidget", price="100.00")

    order = client.post("/orders/", json=_order_payload(product["id"]), headers=headers).json()
    client.put(f"/orders/{order['id']}", json={"payment_status": "paid"}, headers=headers)

    for payload in (
        {"clinic_name": "Now It Applies"},
        {"discount_type": "cash", "discount_value": 5},
        {"items": [{"product_id": product["id"], "qty": 9}]},
    ):
        resp = client.put(f"/orders/{order['id']}", json=payload, headers=headers)
        assert resp.status_code == 200, f"{payload} -> {resp.status_code} {resp.text}"

    body = client.get(f"/orders/{order['id']}", headers=headers).json()
    assert body["clinic_name"] == "Now It Applies"
    assert body["grand_total"] == "895.00"  # 9 x 100 re-priced, less the $5 cash discount
    # The edit is attributed, which is the whole audit trail a paid-order amendment has.
    assert body["updated_by"]["user_name"] == "Admin User"

    # Payment can also be reversed now, and the row deleted outright - though deleting a
    # row that is STILL paid is admin-only, see the test below.
    assert client.put(
        f"/orders/{order['id']}", json={"payment_status": "unpaid"}, headers=headers
    ).status_code == 200
    assert client.delete(f"/orders/{order['id']}", headers=headers).status_code == 204
    assert client.get(f"/orders/{order['id']}", headers=headers).status_code == 404


def test_only_an_admin_can_delete_a_paid_order(client, db_session, monkeypatch):
    """The one piece of the old paid-order freeze that came back (2026-08-20).

    Editing a completed sale leaves a trail in updated_by/updated_at; deleting it leaves
    nothing at all, line items included. So it stays possible - the owner asked for it -
    but only for `admin`, not for every price_listing salesperson who can raise a quote.
    An UNPAID order is still anybody's to delete."""
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="paiddeleteadmin@example.com", password="password123")
    admin_headers = auth_header(client, "paiddeleteadmin@example.com", "password123")
    _staff_without_admin(db_session, "paiddeletestaff@example.com")
    staff_headers = auth_header(client, "paiddeletestaff@example.com", "password123")

    product = _make_order_product(client, admin_headers, name="PaidDeleteWidget")

    # Unpaid: the salesperson deletes their own mistake, as before.
    unpaid = client.post(
        "/orders/", json=_order_payload(product["id"]), headers=admin_headers
    ).json()
    assert client.delete(f"/orders/{unpaid['id']}", headers=staff_headers).status_code == 204

    paid = client.post(
        "/orders/", json=_order_payload(product["id"]), headers=admin_headers
    ).json()
    assert client.put(
        f"/orders/{paid['id']}", json={"payment_status": "paid"}, headers=admin_headers
    ).status_code == 200

    refused = client.delete(f"/orders/{paid['id']}", headers=staff_headers)
    assert refused.status_code == 403, refused.text
    assert "admin" in refused.json()["detail"]
    # Refused, not half-done.
    assert client.get(f"/orders/{paid['id']}", headers=admin_headers).status_code == 200

    assert client.delete(f"/orders/{paid['id']}", headers=admin_headers).status_code == 204
    assert client.get(f"/orders/{paid['id']}", headers=admin_headers).status_code == 404


def test_a_paid_orders_status_is_final(client, db_session, monkeypatch):
    """The one thing a completed sale won't accept. Everything else about a paid order
    can still be corrected (see the test above), but its workflow status describes how
    the sale ended, and moving a settled order back to "pending" or on to "cancelled"
    contradicts the receipt the customer is holding."""
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="finalstatus@example.com", password="password123")
    headers = auth_header(client, "finalstatus@example.com", "password123")
    product = _make_order_product(client, headers, name="FinalWidget", price="100.00")

    order = client.post("/orders/", json=_order_payload(product["id"]), headers=headers).json()
    # Free to move while there is no payment on record.
    assert client.put(
        f"/orders/{order['id']}", json={"status": "delivered"}, headers=headers
    ).status_code == 200

    client.put(f"/orders/{order['id']}", json={"payment_status": "paid"}, headers=headers)

    for new_status in ("pending", "cancelled", "confirmed"):
        resp = client.put(f"/orders/{order['id']}", json={"status": new_status}, headers=headers)
        assert resp.status_code == 409, f"{new_status} -> {resp.status_code} {resp.text}"

    # Re-sending the status it already has is not a change, so a full-object edit that
    # carries the current status through still saves.
    resp = client.put(
        f"/orders/{order['id']}",
        json={"status": "delivered", "clinic_name": "Still Editable"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = client.get(f"/orders/{order['id']}", headers=headers).json()
    assert body["status"] == "delivered"
    assert body["clinic_name"] == "Still Editable"

    # Reversing the payment unlocks it again - the sale is no longer complete.
    client.put(f"/orders/{order['id']}", json={"payment_status": "unpaid"}, headers=headers)
    assert client.put(
        f"/orders/{order['id']}", json={"status": "cancelled"}, headers=headers
    ).status_code == 200


def test_staff_can_issue_a_khqr_for_an_existing_order(client, db_session, monkeypatch):
    """The counter/phone-order case: a quote that already exists gets a QR the customer
    can scan. Idempotent, and invalidated by an edit that moves the total."""
    _fast_alert_wait(monkeypatch)
    _configure_bakong(monkeypatch)
    make_admin(db_session, email="qradmin@example.com", password="password123")
    headers = auth_header(client, "qradmin@example.com", "password123")
    product = _make_order_product(client, headers, name="QRWidget", price="75.00")

    order = client.post("/orders/", json=_order_payload(product["id"]), headers=headers).json()
    assert order["khqr_string"] is None

    resp = client.post(f"/orders/{order['id']}/khqr", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_method"] == "khqr"
    assert body["payment_status"] == "unpaid"
    # A quote that gets a payment QR is still the quote it started as.
    assert body["order_type"] == "quote"
    assert "5406150.00" in body["khqr_string"]  # amount tag: 2 x $75.00
    assert body["order_number"] in body["khqr_string"]  # bill number
    assert body["khqr_md5"] is not None

    # Re-opening the dialog hands back the same payload, not a fresh one.
    again = client.post(f"/orders/{order['id']}/khqr", headers=headers).json()
    assert again["khqr_string"] == body["khqr_string"]

    # An edit that moves the total drops it, so the next request builds a new one.
    moved = client.put(
        f"/orders/{order['id']}",
        json={"items": [{"product_id": product["id"], "qty": 4}]},
        headers=headers,
    ).json()
    assert moved["khqr_string"] is None
    rebuilt = client.post(f"/orders/{order['id']}/khqr", headers=headers).json()
    assert "5406300.00" in rebuilt["khqr_string"]  # 4 x $75.00

    # And a paid order has nothing left to collect.
    client.put(f"/orders/{order['id']}", json={"payment_status": "paid"}, headers=headers)
    assert client.post(f"/orders/{order['id']}/khqr", headers=headers).status_code == 400


def test_staff_can_poll_any_orders_payment_status(client, db_session, monkeypatch):
    """Staff issue the QR from the admin page against an order that already exists, so
    they have to be able to watch that same order settle - even one a customer placed.

    This is now the ONLY way an order sits unpaid with a QR on it: a customer's own
    pay-by-QR purchase never creates an order until the money has arrived."""
    _fast_alert_wait(monkeypatch)
    _configure_bakong(monkeypatch)
    make_admin(db_session, email="pollstaff@example.com", password="password123")
    admin_headers = auth_header(client, "pollstaff@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="PollWidget")

    make_customer(db_session, email="pollcust@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "pollcust@example.com", "customerpass1")
    # A cash checkout IS an order (a quote) - payment happens offline, so there's a
    # document from the start. Staff then put a QR on it to take the money now.
    order = client.post(
        "/orders/", json=_order_payload(product["id"], payment_method="cash"), headers=cust_headers
    ).json()
    order = client.post(f"/orders/{order['id']}/khqr", headers=admin_headers).json()
    assert order["payment_status"] == "unpaid"

    # Staff didn't place it, but price_listing already reads the whole order.
    resp = client.get(f"/orders/{order['id']}/payment-status", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == {"payment_status": "unpaid"}


def test_staff_can_poll_a_customers_checkout(client, db_session, monkeypatch):
    """The counter case for the new flow: a customer is paying by QR in front of staff,
    and staff need to see it settle. price_listing already reads any order, so this
    exposes nothing new - but it must not 404 the way another customer does."""
    _configure_bakong(monkeypatch)
    make_admin(db_session, email="pollstaff2@example.com", password="password123")
    admin_headers = auth_header(client, "pollstaff2@example.com", "password123")
    product = _make_order_product(client, admin_headers, name="PollWidget2")

    make_customer(db_session, email="pollcust2@example.com", password="customerpass1", access_permission=True)
    cust_headers = customer_auth_header(client, "pollcust2@example.com", "customerpass1")
    checkout = client.post(
        "/orders/checkout", json=_order_payload(product["id"], payment_method="khqr"),
        headers=cust_headers,
    ).json()

    resp = client.get(f"/orders/checkout/{checkout['id']}/payment-status", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == {"payment_status": "unpaid", "order": None}


def test_editing_an_order_rejects_a_blank_required_field(client, db_session, monkeypatch):
    """Explicit nulls clear the optional fields and are refused on the ones an order
    can't exist without."""
    _fast_alert_wait(monkeypatch)
    make_admin(db_session, email="blankedit@example.com", password="password123")
    headers = auth_header(client, "blankedit@example.com", "password123")
    product = _make_order_product(client, headers, name="BlankWidget")

    order = client.post(
        "/orders/", json=_order_payload(product["id"], contact_person="Dr Gone"), headers=headers
    ).json()

    assert client.put(
        f"/orders/{order['id']}", json={"clinic_name": None}, headers=headers
    ).status_code == 400
    assert client.put(
        f"/orders/{order['id']}", json={"status": None}, headers=headers
    ).status_code == 400
    # contact_person is genuinely optional - null means "clear it".
    body = client.put(
        f"/orders/{order['id']}", json={"contact_person": None}, headers=headers
    ).json()
    assert body["contact_person"] is None


def _press_order_button(client, monkeypatch, order_id, action="delivered"):
    """Drive one Delivered/Cancelled button press through the real webhook, returning
    the caption it tried to write back. Both outbound Telegram calls are stubbed - the
    point is what we would have sent, not sending it."""
    from app.routers import telegram_webhook as hook

    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    sent = {}

    async def fake_clear(chat_id, message_id, new_caption):
        sent["caption"] = new_caption

    async def fake_answer(callback_query_id, text):
        sent["answer"] = text

    monkeypatch.setattr(hook, "clear_order_alert_buttons", fake_clear)
    monkeypatch.setattr(hook, "answer_callback_query", fake_answer)

    resp = client.post(
        "/telegram/webhook/s3cret",
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        json={
            "callback_query": {
                "id": "cbq-1",
                "data": f"order:{order_id}:{action}",
                # Telegram hands the caption back as PLAIN text - every entity it
                # parsed on the way in is gone. Reusing this is what used to strip
                # the formatting off the alert.
                "message": {"message_id": 77, "chat": {"id": -100123}, "caption": "NEW QUOTE"},
            }
        },
    )
    assert resp.status_code == 200, resp.text
    return sent


def test_pressing_delivered_keeps_the_alert_formatted(client, db_session, monkeypatch):
    """The button press used to rebuild the caption from Telegram's plain-text copy,
    so every bold header and the Maps link vanished the moment anyone tapped it. The
    caption is re-rendered from the order instead."""
    make_admin(db_session, email="tgbutton@example.com", password="password123")
    headers = auth_header(client, "tgbutton@example.com", "password123")
    product = _make_order_product(client, headers)
    order = client.post(
        "/orders/",
        json=_order_payload(
            product["id"],
            clinic_name="Sunrise Dental",
            latitude="11.5564",
            longitude="104.9282",
        ),
        headers=headers,
    ).json()

    sent = _press_order_button(client, monkeypatch, order["id"])

    caption = sent["caption"]
    assert "<b>Status: Delivered ✅</b>" in caption
    assert "<b>Sunrise Dental</b>" in caption  # markup survived
    assert "NEW QUOTE" in caption
    assert 'href="https://www.google.com/maps?q=11.556400,104.928200"' in caption
    assert "Quoted Widget" in caption  # and so did the itemisation
    assert client.get(f"/orders/{order['id']}", headers=headers).json()["status"] == "delivered"


def test_pressing_cancelled_records_the_status(client, db_session, monkeypatch):
    make_admin(db_session, email="tgbutton2@example.com", password="password123")
    headers = auth_header(client, "tgbutton2@example.com", "password123")
    product = _make_order_product(client, headers)
    order = client.post("/orders/", json=_order_payload(product["id"]), headers=headers).json()

    sent = _press_order_button(client, monkeypatch, order["id"], action="cancelled")

    assert "<b>Status: Cancelled ❌</b>" in sent["caption"]
    assert client.get(f"/orders/{order['id']}", headers=headers).json()["status"] == "cancelled"


def test_paid_quote_alert_is_an_invoice_not_a_new_quote():
    """A quote whose payment staff recorded is a completed sale - the Telegram alert
    must not still say "no payment has been made" just because order_type is "quote",
    and the attached document is named for what it now is (an Invoice since
    2026-08-17, "Receipt" before that)."""
    from app.schemas import OrderOut
    from app.services.telegram import _document_word
    from app.services.telegram_format import render_order_alert

    base = {
        "id": 1, "order_number": "000001", "quote_code": "260808010101",
        "clinic_name": "Cash Clinic", "phone": "012", "address": "1 St",
        "discount_type": "cash", "discount_value": "0", "discount_amount": "0",
        "subtotal": "100.00", "grand_total": "100.00", "status": "pending",
        "order_type": "quote", "created_at": "2026-08-08T00:00:00Z",
        "updated_at": "2026-08-08T00:00:00Z",
    }
    unpaid = OrderOut(**base)
    assert _document_word(unpaid) == "Quotation"
    assert "no payment received yet" in render_order_alert(unpaid).full

    paid = OrderOut(**{**base, "payment_status": "paid"})
    paid_caption = render_order_alert(paid).full
    assert _document_word(paid) == "Invoice"
    assert "PAID" in paid_caption
    assert "no payment received yet" not in paid_caption
    assert "via KHQR" not in paid_caption  # no method recorded - don't invent one

    khqr_caption = render_order_alert(
        OrderOut(**{**base, "order_type": "order", "payment_method": "khqr", "payment_status": "paid"})
    ).full
    assert "PAID via KHQR" in khqr_caption


# ---------------------------------------------------------------------------
# Site-wide settings + the `admin` permission
# ---------------------------------------------------------------------------
def _staff_without_admin(db_session, email):
    """A staff member holding every OTHER permission. The point of these tests is that
    `admin` is not implied by the rest - so the negative case has to be someone who
    would otherwise be able to do anything."""
    from app.core.security import hash_password
    from app.models import User

    user = User(
        user_name="Busy Staffer",
        email=email,
        hashed_password=hash_password("password123"),
        role_title="Manager",
        is_active=True,
        is_verified=True,
        user_management=True,
        price_listing=True,
        product_management=True,
        customer_management=True,
        admin=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_public_settings_need_no_auth(client):
    resp = client.get("/settings/public")
    assert resp.status_code == 200
    values = resp.json()
    # The storefront footer renders from these before anyone has signed in.
    assert values["store_name"]
    assert "quote_validity_days" in values


def test_public_settings_expose_only_public_keys(client):
    from app.core.settings_spec import PUBLIC_KEYS, SETTINGS

    resp = client.get("/settings/public")
    assert set(resp.json()) == set(PUBLIC_KEYS)
    # The split has to actually withhold something, or this test passes vacuously and
    # would keep passing if `public=False` stopped being honoured.
    private = set(SETTINGS) - set(PUBLIC_KEYS)
    assert private
    assert not (private & set(resp.json()))


def test_khqr_presentation_settings_are_not_public(client, db_session):
    """Merchant name/city/expiry are admin-editable but server-side only: no browser
    needs them, and the payer sees the name through the QR payload itself."""
    from app.core.settings_spec import SETTINGS

    for key in ("khqr_merchant_name", "khqr_merchant_city", "khqr_expiry_minutes"):
        assert SETTINGS[key].public is False
    assert "khqr_merchant_name" not in client.get("/settings/public").json()

    # ...but an admin reading the full settings does get them.
    make_admin(db_session, email="khqradmin@example.com", password="password123")
    headers = auth_header(client, "khqradmin@example.com", "password123")
    assert "khqr_merchant_name" in client.get("/settings/", headers=headers).json()["values"]


def test_khqr_settings_default_to_the_environment_and_reach_the_payload(client, db_session):
    """The spec default IS the env value, so a deployment that configures
    KHQR_MERCHANT_NAME in .env and never opens Settings behaves exactly as before."""
    from decimal import Decimal

    from app.config import settings as env
    from app.core.settings_spec import DEFAULTS
    from app.services import khqr

    assert DEFAULTS["khqr_merchant_name"] == env.KHQR_MERCHANT_NAME
    assert DEFAULTS["khqr_expiry_minutes"] == env.KHQR_EXPIRY_MINUTES
    assert khqr.merchant_name() == env.KHQR_MERCHANT_NAME

    make_admin(db_session, email="khqrbuild@example.com", password="password123")
    headers = auth_header(client, "khqrbuild@example.com", "password123")
    client.put("/settings/", json={"values": {
        "khqr_merchant_name": "NEW MERCHANT",
        "khqr_merchant_city": "Siem Reap",
        "khqr_expiry_minutes": 15,
    }}, headers=headers)

    assert khqr.merchant_name() == "NEW MERCHANT"
    assert khqr.expiry_minutes() == 15

    # The name and city are written into tags 59/60, so they must show up in a QR built
    # after the change - this is the whole point of making them editable.
    payload = khqr._build_from_account_id(Decimal("12.34"), "EB-TEST-1")
    assert "NEW MERCHANT" in payload
    assert "Siem Reap" in payload


def test_reading_settings_requires_the_admin_permission(client, db_session):
    _staff_without_admin(db_session, "notadmin@example.com")
    headers = auth_header(client, "notadmin@example.com", "password123")

    resp = client.get("/settings/", headers=headers)
    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"]

    # ...and so does writing, not just reading.
    resp = client.put("/settings/", json={"values": {"store_name": "Nope"}}, headers=headers)
    assert resp.status_code == 403


def test_admin_can_read_and_save_settings(client, db_session):
    make_admin(db_session, email="settingsadmin@example.com", password="password123")
    headers = auth_header(client, "settingsadmin@example.com", "password123")

    resp = client.get("/settings/", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert {"values", "defaults", "groups", "status"} <= set(body)
    # The admin form is rendered from `groups`, so it has to describe every setting.
    described = {s["key"] for g in body["groups"] for s in g["settings"]}
    assert described == set(body["defaults"])

    resp = client.put(
        "/settings/",
        json={"values": {"contact_phone": "010 111 222", "quote_validity_days": "45"}},
        headers=headers,
    )
    assert resp.status_code == 200
    values = resp.json()["values"]
    assert values["contact_phone"] == "010 111 222"
    # Coerced to its declared type, not left as the string the form submitted.
    assert values["quote_validity_days"] == 45

    # Visible to an anonymous visitor immediately - no restart, no cache wait.
    assert client.get("/settings/public").json()["contact_phone"] == "010 111 222"


def test_settings_reject_out_of_range_and_unknown_keys(client, db_session):
    make_admin(db_session, email="rangeadmin@example.com", password="password123")
    headers = auth_header(client, "rangeadmin@example.com", "password123")

    resp = client.put("/settings/", json={"values": {"quote_validity_days": 0}}, headers=headers)
    assert resp.status_code == 400
    # The message names the field the way the admin sees it, not by key.
    assert "Quote validity (days)" in resp.json()["detail"]

    resp = client.put("/settings/", json={"values": {"not_a_setting": "x"}}, headers=headers)
    assert resp.status_code == 400

    # A url-typed setting lands in an href on a public page, so anything that isn't
    # http(s)/tel/mailto is refused rather than rendered.
    resp = client.put(
        "/settings/", json={"values": {"call_now_url": "javascript:alert(1)"}}, headers=headers
    )
    assert resp.status_code == 400


def test_a_rejected_value_saves_nothing_at_all(client, db_session):
    """One bad field must not let the good ones through - a half-saved form is the
    worst outcome, because the admin can't tell which half took."""
    make_admin(db_session, email="atomicadmin@example.com", password="password123")
    headers = auth_header(client, "atomicadmin@example.com", "password123")

    resp = client.put(
        "/settings/",
        json={"values": {"contact_phone": "011 999 888", "quote_validity_days": 9999}},
        headers=headers,
    )
    assert resp.status_code == 400
    assert client.get("/settings/public").json()["contact_phone"] != "011 999 888"


def test_saving_the_default_value_stores_no_override(client, db_session):
    from app.core.settings_spec import DEFAULTS
    from app.models import AppSetting

    make_admin(db_session, email="defaultadmin@example.com", password="password123")
    headers = auth_header(client, "defaultadmin@example.com", "password123")

    client.put("/settings/", json={"values": {"store_name": "Temporary Name"}}, headers=headers)
    assert db_session.query(AppSetting).filter_by(key="store_name").count() == 1

    # Typing the default back in is the same thing as resetting - so it leaves no row,
    # and the setting keeps tracking the default if that ever changes.
    client.put(
        "/settings/", json={"values": {"store_name": DEFAULTS["store_name"]}}, headers=headers
    )
    assert db_session.query(AppSetting).filter_by(key="store_name").count() == 0


def test_settings_reset_restores_a_whole_group(client, db_session):
    from app.core.settings_spec import DEFAULTS

    make_admin(db_session, email="resetadmin@example.com", password="password123")
    headers = auth_header(client, "resetadmin@example.com", "password123")

    client.put(
        "/settings/",
        json={"values": {"contact_phone": "012 000 000", "store_name": "Changed"}},
        headers=headers,
    )
    resp = client.post("/settings/reset", json={"group": "store"}, headers=headers)
    assert resp.status_code == 200
    values = resp.json()["values"]
    assert values["contact_phone"] == DEFAULTS["contact_phone"]
    assert values["store_name"] == DEFAULTS["store_name"]

    assert client.post("/settings/reset", json={"group": "nope"}, headers=headers).status_code == 400
    assert client.post("/settings/reset", json={}, headers=headers).status_code == 400


def test_settings_record_who_changed_them(client, db_session):
    from app.models import AppSetting

    admin = make_admin(db_session, email="auditadmin@example.com", password="password123")
    headers = auth_header(client, "auditadmin@example.com", "password123")

    client.put("/settings/", json={"values": {"business_hours": "Daily"}}, headers=headers)
    row = db_session.query(AppSetting).filter_by(key="business_hours").one()
    assert row.updated_by_user_id == admin.id


def test_admin_permission_round_trips_through_the_users_api(client, db_session):
    """The POST /users/ trap: `admin` has to be in the explicit field list create_user
    builds the User from, not just in the schema, or it validates and is then dropped."""
    make_admin(db_session, email="grantadmin@example.com", password="password123")
    headers = auth_header(client, "grantadmin@example.com", "password123")

    created = client.post(
        "/users/",
        json={
            "user_name": "New Settings Person",
            "email": "newsettings@example.com",
            "password": "password123",
            "admin": True,
        },
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["admin"] is True

    user_id = created.json()["id"]
    updated = client.put(f"/users/{user_id}", json={"admin": False}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["admin"] is False


def test_the_last_admin_cannot_revoke_their_own_admin_permission(client, db_session):
    """Settings is the only place `admin` can be granted from, so the last holder
    dropping it would make the permission ungrantable without a manual DB write."""
    only_admin = make_admin(db_session, email="lonelyadmin@example.com", password="password123")
    headers = auth_header(client, "lonelyadmin@example.com", "password123")

    resp = client.put(f"/users/{only_admin.id}", json={"admin": False}, headers=headers)
    assert resp.status_code == 400
    assert "only account" in resp.json()["detail"]

    # With a second admin in place it's allowed - the guard is about the last one.
    make_admin(db_session, email="secondadmin@example.com", password="password123")
    resp = client.put(f"/users/{only_admin.id}", json={"admin": False}, headers=headers)
    assert resp.status_code == 200


def test_default_quote_terms_come_from_settings(client, db_session):
    """The payment/installation terms were the same two literals in two repos - constants
    in the Flask app and `or ...` fallbacks in the PDF builder. One setting now, so the
    cart, the recorded order and the printed document can't disagree."""
    from app.core.settings_spec import DEFAULTS
    from app.services import app_settings

    assert DEFAULTS["default_payment_term"] == "COD"
    assert DEFAULTS["default_install_term"] == "Free within Phnom Penh"

    make_admin(db_session, email="termsadmin@example.com", password="password123")
    headers = auth_header(client, "termsadmin@example.com", "password123")
    client.put("/settings/", json={"values": {
        "default_payment_term": "50% deposit",
        "default_install_term": "Nationwide, 7 days",
    }}, headers=headers)

    # Public, because the customer's cart drawer renders them before checkout.
    public = client.get("/settings/public").json()
    assert public["default_payment_term"] == "50% deposit"
    assert public["default_install_term"] == "Nationwide, 7 days"
    assert app_settings.get_all()["default_install_term"] == "Nationwide, 7 days"


def test_invoice_pdf_uses_the_configured_letterhead(client, db_session):
    """The printed PDF reads its letterhead from settings. Mirrored by
    buildPrintTemplate() in the website's main.js - see the eb-quote-parity skill."""
    from app.schemas import OrderOut
    from app.services import app_settings
    from app.services.invoice_pdf import build_invoice_pdf

    make_admin(db_session, email="pdfadmin@example.com", password="password123")
    headers = auth_header(client, "pdfadmin@example.com", "password123")
    client.put(
        "/settings/",
        json={"values": {"document_brand_name": "TOTALLY OTHER CLINIC", "quote_validity_days": 7}},
        headers=headers,
    )

    order = OrderOut(**{
        "id": 1, "order_number": "EB-1", "quote_code": "260817120000",
        "items": [], "clinic_name": "C", "phone": "012", "address": "1 St",
        "discount_type": "cash", "discount_value": "0", "discount_amount": "0",
        "subtotal": "0.00", "grand_total": "0.00", "status": "pending",
        "order_type": "quote", "created_at": "2026-08-17T00:00:00Z",
        "updated_at": "2026-08-17T00:00:00Z",
    })
    pdf_bytes = build_invoice_pdf(order)
    assert pdf_bytes[:4] == b"%PDF"
    # Belt and braces: the values really did reach the builder, not just the API.
    values = app_settings.get_all()
    assert values["document_brand_name"] == "TOTALLY OTHER CLINIC"
    assert values["quote_validity_days"] == 7


def test_a_payment_qr_is_uploaded_rather_than_typed(client, db_session):
    """`image`-typed settings hold a stored picture URL that only the upload endpoint
    writes - there is no text box for one on the Settings screen."""
    make_admin(db_session, email="qrsetting@example.com", password="password123")
    headers = auth_header(client, "qrsetting@example.com", "password123")

    resp = client.post(
        "/settings/image/quote_payment_qr",
        files={"file": ("aba.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    assert resp.status_code == 200
    stored = resp.json()["value"]
    assert stored.startswith("/static/uploads/settings/")

    # Public: the printed quote is built in the customer's own browser.
    assert client.get("/settings/public").json()["quote_payment_qr"] == stored


def test_only_picture_settings_accept_an_upload(client, db_session):
    make_admin(db_session, email="qrsetting2@example.com", password="password123")
    headers = auth_header(client, "qrsetting2@example.com", "password123")

    files = {"file": ("aba.png", _png_bytes(), "image/png")}
    assert client.post(
        "/settings/image/document_brand_name", files=files, headers=headers
    ).status_code == 400
    assert client.post(
        "/settings/image/not_a_setting", files=files, headers=headers
    ).status_code == 404

    # And a typed value still has to look like a stored picture, not a javascript: URL
    # - it lands in a src= on the printed quote.
    resp = client.put(
        "/settings/",
        json={"values": {"quote_payment_qr": "javascript:alert(1)"}},
        headers=headers,
    )
    assert resp.status_code == 400


def test_the_payment_qr_prints_on_a_quotation_but_not_once_it_is_paid(client, db_session):
    """The terms box carries the bank QR only while there is still something to pay -
    a paid invoice and a cancelled order print their own one-line note instead. The
    same rule lives in buildPrintTemplate() in the website's main.js; see the
    eb-quote-parity skill."""
    from app.schemas import OrderOut
    from app.services import invoice_pdf

    make_admin(db_session, email="qrprint@example.com", password="password123")
    headers = auth_header(client, "qrprint@example.com", "password123")
    client.post(
        "/settings/image/quote_payment_qr",
        files={"file": ("aba.png", _png_bytes(), "image/png")},
        headers=headers,
    )

    asked_for = []
    original = invoice_pdf._payment_qr_image
    invoice_pdf._payment_qr_image = lambda url: asked_for.append(url) or original(url)
    try:
        def build(**overrides):
            payload = {
                "id": 1, "order_number": "EB-9", "quote_code": "260822120000",
                "items": [], "clinic_name": "C", "phone": "012", "address": "1 St",
                "discount_type": "cash", "discount_value": "0", "discount_amount": "0",
                "subtotal": "0.00", "grand_total": "0.00", "status": "pending",
                "order_type": "quote", "created_at": "2026-08-22T00:00:00Z",
                "updated_at": "2026-08-22T00:00:00Z",
            }
            payload.update(overrides)
            return build_pdf(OrderOut(**payload))

        build_pdf = invoice_pdf.build_invoice_pdf
        assert build()[:4] == b"%PDF"
        assert len(asked_for) == 1  # a quotation asks for the picture

        assert build(payment_status="paid")[:4] == b"%PDF"
        assert build(status="cancelled")[:4] == b"%PDF"
        assert len(asked_for) == 1  # ... and neither of those did
    finally:
        invoice_pdf._payment_qr_image = original


# ---------------------------------------------------------------------------
# Department QR codes (contact page)
# ---------------------------------------------------------------------------
def _png_bytes():
    """Smallest thing PIL and the content-type check both accept as an image."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "black").save(buffer, "PNG")
    return buffer.getvalue()


def test_qr_codes_are_public_to_read_and_sorted(client, db_session):
    make_admin(db_session, email="qradmin1@example.com", password="password123")
    headers = auth_header(client, "qradmin1@example.com", "password123")

    # Created out of order on purpose - the list has to come back by sort_order.
    client.post("/qr-codes/", data={"title": "Second", "sort_order": 2}, headers=headers)
    client.post("/qr-codes/", data={"title": "First", "sort_order": 1}, headers=headers)

    # No Authorization header at all: the contact page is served to strangers.
    resp = client.get("/qr-codes/")
    assert resp.status_code == 200
    assert [c["title"] for c in resp.json()] == ["First", "Second"]


def test_creating_a_qr_code_stores_its_picture_and_badge(client, db_session):
    make_admin(db_session, email="qradmin2@example.com", password="password123")
    headers = auth_header(client, "qradmin2@example.com", "password123")

    resp = client.post(
        "/qr-codes/",
        data={
            "title": "Technician Support",
            "subtitle": "Technical Support Team",
            "badge_label": "Support",
            "badge_variant": "machinery",
            "badge_icon": "fa-wrench",
            "sort_order": 3,
        },
        files={"file": ("qr.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    card = resp.json()
    assert card["subtitle"] == "Technical Support Team"
    assert card["badge_variant"] == "machinery"
    assert card["sort_order"] == 3
    # Stored as uploaded - a .png, not re-encoded to JPEG, since JPEG artifacts
    # around a QR's hard edges are what makes a printed code fail to scan.
    assert card["qr_image"].endswith(".png")


def test_qr_code_optional_fields_can_be_cleared(client, db_session):
    """The blank-means-two-things trap: a form posts an untouched field as "", and an
    admin erasing a subtitle has to actually erase it."""
    make_admin(db_session, email="qradmin3@example.com", password="password123")
    headers = auth_header(client, "qradmin3@example.com", "password123")

    created = client.post(
        "/qr-codes/",
        data={"title": "Machine Sale", "subtitle": "Sales - Machinery", "badge_label": "Machinery"},
        headers=headers,
    ).json()

    # Empty strings on create are stored as NULL, not as "".
    blank = client.post(
        "/qr-codes/", data={"title": "Blank", "subtitle": "", "badge_label": ""}, headers=headers
    ).json()
    assert blank["subtitle"] is None and blank["badge_label"] is None

    resp = client.put(
        f"/qr-codes/{created['id']}", json={"subtitle": None, "badge_label": None}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["subtitle"] is None
    assert resp.json()["badge_label"] is None
    # An omitted key still means "leave it alone".
    assert resp.json()["title"] == "Machine Sale"


def test_editing_a_qr_codes_caption_keeps_its_picture(client, db_session):
    """The PUT carries no file, so it must not blank the image an admin uploaded -
    that's what the separate /image endpoint is for."""
    make_admin(db_session, email="qradmin4@example.com", password="password123")
    headers = auth_header(client, "qradmin4@example.com", "password123")

    card = client.post(
        "/qr-codes/",
        data={"title": "Material Sale"},
        files={"file": ("qr.png", _png_bytes(), "image/png")},
        headers=headers,
    ).json()
    original_image = card["qr_image"]
    assert original_image

    renamed = client.put(
        f"/qr-codes/{card['id']}", json={"title": "Materials Team"}, headers=headers
    ).json()
    assert renamed["title"] == "Materials Team"
    assert renamed["qr_image"] == original_image

    # Replacing the picture is its own request, and does change it.
    replaced = client.post(
        f"/qr-codes/{card['id']}/image",
        files={"file": ("new.png", _png_bytes(), "image/png")},
        headers=headers,
    ).json()
    assert replaced["qr_image"] != original_image


def test_qr_codes_reject_an_unknown_badge_variant(client, db_session):
    """The storefront only has CSS for four colours - anything else would render as an
    unstyled pill, so it's refused at the edge rather than stored."""
    make_admin(db_session, email="qradmin5@example.com", password="password123")
    headers = auth_header(client, "qradmin5@example.com", "password123")

    resp = client.post(
        "/qr-codes/", data={"title": "Nope", "badge_variant": "chartreuse"}, headers=headers
    )
    assert resp.status_code == 422


def test_writing_qr_codes_requires_the_admin_permission(client, db_session):
    """Same gate the Settings screen uses: these captions used to BE settings, so who
    may rewrite the contact page shouldn't change just because the storage did."""
    _staff_without_admin(db_session, "qrnotadmin@example.com")
    headers = auth_header(client, "qrnotadmin@example.com", "password123")

    resp = client.post("/qr-codes/", data={"title": "Sneaky"}, headers=headers)
    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"]

    # Reading stays open to everyone, including this account.
    assert client.get("/qr-codes/", headers=headers).status_code == 200


def test_deleting_a_qr_code_removes_it_from_the_public_list(client, db_session):
    make_admin(db_session, email="qradmin6@example.com", password="password123")
    headers = auth_header(client, "qradmin6@example.com", "password123")

    card = client.post("/qr-codes/", data={"title": "Temporary"}, headers=headers).json()
    assert client.delete(f"/qr-codes/{card['id']}", headers=headers).status_code == 204
    assert client.get("/qr-codes/").json() == []
    assert client.get(f"/qr-codes/{card['id']}").status_code == 404


def test_the_removed_qr_settings_group_is_gone(client, db_session):
    """The captions moved to /qr-codes; leaving the old keys behind would give an admin
    two places to edit the same card, one of which no longer does anything."""
    make_admin(db_session, email="qradmin7@example.com", password="password123")
    headers = auth_header(client, "qradmin7@example.com", "password123")

    body = client.get("/settings/", headers=headers).json()
    assert "qr" not in {g["id"] for g in body["groups"]}
    assert not [key for key in body["defaults"] if key.startswith("qr_")]

    resp = client.put("/settings/", json={"values": {"qr_machine_title": "x"}}, headers=headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Orders: who may work them, and paying a cancelled one
# ---------------------------------------------------------------------------
def _admin_only_staff(db_session, email, password="password123"):
    """Holds `admin` and nothing else - the owner who runs the store but was never
    given a sales flag. The orders area has to let this person in."""
    from app.core.security import hash_password
    from app.models import User

    user = User(
        user_name="Owner",
        email=email,
        hashed_password=hash_password(password),
        role_title="Owner",
        is_active=True,
        is_verified=True,
        user_management=False,
        price_listing=False,
        product_management=False,
        customer_management=False,
        admin=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_admin_permission_alone_can_work_orders(client, db_session):
    """`admin` is "runs this store", not a job title, so it isn't implied by
    price_listing - and an owner holding only it used to be locked out of the whole
    orders area, including recording a payment."""
    make_admin(db_session, email="ordersetup1@example.com", password="password123")
    setup_headers = auth_header(client, "ordersetup1@example.com", "password123")
    product = _make_order_product(client, setup_headers, name="Owner Widget")
    order = client.post(
        "/orders/", json=_order_payload(product["id"]), headers=setup_headers
    ).json()

    _admin_only_staff(db_session, "owneronly@example.com")
    owner_headers = auth_header(client, "owneronly@example.com", "password123")

    assert client.get("/orders/", headers=owner_headers).status_code == 200
    assert client.get(f"/orders/{order['id']}", headers=owner_headers).status_code == 200

    resp = client.put(
        f"/orders/{order['id']}", json={"payment_status": "paid"}, headers=owner_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["payment_status"] == "paid"
    assert resp.json()["paid_at"] is not None


def test_working_orders_needs_price_listing_or_admin(client, db_session):
    """Neither flag is still a 403, and the message names both doors."""
    from app.core.security import hash_password
    from app.models import User

    user = User(
        user_name="Stock Clerk", email="neitherflag@example.com",
        hashed_password=hash_password("password123"), role_title="Clerk",
        is_active=True, is_verified=True, user_management=False, price_listing=False,
        product_management=True, customer_management=False, admin=False,
    )
    db_session.add(user)
    db_session.commit()
    headers = auth_header(client, "neitherflag@example.com", "password123")

    resp = client.get("/orders/", headers=headers)
    assert resp.status_code == 403
    assert "price_listing" in resp.json()["detail"] and "admin" in resp.json()["detail"]


def test_a_cancelled_order_cannot_be_marked_paid(client, db_session):
    """Recording money against a cancelled sale produced a row the totals strip
    excluded as cancelled while the customer's own order list showed it as paid."""
    make_admin(db_session, email="cancelpaid@example.com", password="password123")
    headers = auth_header(client, "cancelpaid@example.com", "password123")
    product = _make_order_product(client, headers, name="Cancel Widget")
    order = client.post("/orders/", json=_order_payload(product["id"]), headers=headers).json()

    client.put(f"/orders/{order['id']}", json={"status": "cancelled"}, headers=headers)

    resp = client.put(f"/orders/{order['id']}", json={"payment_status": "paid"}, headers=headers)
    assert resp.status_code == 409
    assert "cancelled" in resp.json()["detail"]
    assert client.get(f"/orders/{order['id']}", headers=headers).json()["payment_status"] != "paid"

    # Reopening and paying in one request is the way through - the effective status is
    # what's checked, not the stored one.
    resp = client.put(
        f"/orders/{order['id']}",
        json={"status": "confirmed", "payment_status": "paid"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["payment_status"] == "paid"


def test_a_paid_order_still_cannot_be_cancelled(client, db_session):
    """The mirror rule, which already existed - together they keep paid and cancelled
    from ever being set from the admin screen in either order."""
    make_admin(db_session, email="paidcancel@example.com", password="password123")
    headers = auth_header(client, "paidcancel@example.com", "password123")
    product = _make_order_product(client, headers, name="Paid Widget")
    order = client.post("/orders/", json=_order_payload(product["id"]), headers=headers).json()

    client.put(f"/orders/{order['id']}", json={"payment_status": "paid"}, headers=headers)
    resp = client.put(f"/orders/{order['id']}", json={"status": "cancelled"}, headers=headers)
    assert resp.status_code == 409


def test_a_paid_order_prints_as_an_invoice(client, db_session):
    """Quotation until paid, Invoice after - the one rule both print engines follow
    (this one, and `docTitle` in buildPrintTemplate() in the website's main.js). The
    Telegram attachment is named from the same helper, so the filename staff see always
    matches the title inside the file."""
    from app.schemas import OrderOut
    from app.services.invoice_pdf import build_invoice_pdf, document_title
    from app.services.telegram import _document_word

    base = {
        "id": 1, "order_number": "000001", "quote_code": "260817120000",
        "items": [], "clinic_name": "C", "phone": "012", "address": "1 St",
        "discount_type": "cash", "discount_value": "0", "discount_amount": "0",
        "subtotal": "0.00", "grand_total": "0.00", "status": "pending",
        "order_type": "quote", "created_at": "2026-08-17T00:00:00Z",
        "updated_at": "2026-08-17T00:00:00Z",
    }
    unpaid = OrderOut(**base)
    paid = OrderOut(**{**base, "payment_status": "paid"})

    assert document_title(unpaid) == "Quotation"
    assert document_title(paid) == "Invoice"
    # A paid *order* (customer KHQR purchase), not just a paid quote, prints the same.
    assert document_title(OrderOut(**{**base, "order_type": "order", "payment_status": "paid"})) == "Invoice"

    assert _document_word(paid) == document_title(paid)
    # Both still build - the title change didn't break the layout either way.
    assert build_invoice_pdf(unpaid)[:4] == b"%PDF"
    assert build_invoice_pdf(paid)[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# Hero slides (the storefront's rotating banner)
# ---------------------------------------------------------------------------


def test_hero_slides_are_public_to_read_and_sorted(client, db_session):
    make_admin(db_session, email="heroadmin1@example.com", password="password123")
    headers = auth_header(client, "heroadmin1@example.com", "password123")

    # Created out of order on purpose - the banner plays them by sort_order.
    client.post("/hero-slides/", data={"heading": "Second", "sort_order": 2}, headers=headers)
    client.post("/hero-slides/", data={"heading": "First", "sort_order": 1}, headers=headers)

    # No Authorization header at all: the home page is served to strangers.
    resp = client.get("/hero-slides/")
    assert resp.status_code == 200
    assert [s["heading"] for s in resp.json()] == ["First", "Second"]


def test_parked_hero_slides_are_hidden_from_the_storefront_but_not_the_admin(client, db_session):
    """is_active is what lets a seasonal slide be switched off instead of deleted -
    deleting it would lose the copy and the uploaded artwork. The storefront asks for
    active_only; the admin screen doesn't, because switching one back on is exactly
    what someone opens that screen to do."""
    make_admin(db_session, email="heroadmin2@example.com", password="password123")
    headers = auth_header(client, "heroadmin2@example.com", "password123")

    client.post("/hero-slides/", data={"heading": "Live One"}, headers=headers)
    parked = client.post(
        "/hero-slides/", data={"heading": "Off Season", "is_active": "false"}, headers=headers
    ).json()
    assert parked["is_active"] is False

    assert [s["heading"] for s in client.get("/hero-slides/?active_only=true").json()] == ["Live One"]
    assert len(client.get("/hero-slides/").json()) == 2

    # ...and switching it back on is a one-field partial update.
    resumed = client.put(
        f"/hero-slides/{parked['id']}", json={"is_active": True}, headers=headers
    ).json()
    assert resumed["is_active"] is True
    assert resumed["heading"] == "Off Season"
    assert len(client.get("/hero-slides/?active_only=true").json()) == 2


def test_creating_a_hero_slide_stores_its_artwork_and_copy(client, db_session):
    make_admin(db_session, email="heroadmin3@example.com", password="password123")
    headers = auth_header(client, "heroadmin3@example.com", "password123")

    resp = client.post(
        "/hero-slides/",
        data={
            "heading": "Equip Your Practice with",
            "heading_highlight": "Excellence",
            "subheading": "High-quality instruments from world-class brands.",
            "badge_label": "Premium Dental Supply",
            "badge_icon": "fa-tooth",
            "button_label": "Explore Products",
            "button_url": "/products",
            "sort_order": 3,
        },
        files={"file": ("hero.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    slide = resp.json()
    # The heading is split in two so the storefront can colour the tail without
    # anyone typing a <span> into an admin form.
    assert slide["heading"] == "Equip Your Practice with"
    assert slide["heading_highlight"] == "Excellence"
    assert slide["button_url"] == "/products"
    assert slide["sort_order"] == 3
    # New slides are live unless someone says otherwise.
    assert slide["is_active"] is True
    assert slide["slide_image"]


def test_hero_slide_optional_fields_can_be_cleared(client, db_session):
    """The blank-means-two-things trap: a form posts an untouched field as "", and an
    admin removing a slide's badge or button has to actually remove it."""
    make_admin(db_session, email="heroadmin4@example.com", password="password123")
    headers = auth_header(client, "heroadmin4@example.com", "password123")

    created = client.post(
        "/hero-slides/",
        data={
            "heading": "Partner with",
            "heading_highlight": "Industry Leaders",
            "badge_label": "Trusted Brands",
            "button_label": "Browse",
            "button_url": "/products",
        },
        headers=headers,
    ).json()

    # Empty strings on create are stored as NULL, not as "".
    blank = client.post(
        "/hero-slides/",
        data={"heading": "Bare", "subheading": "", "badge_label": "", "button_url": ""},
        headers=headers,
    ).json()
    assert blank["subheading"] is None
    assert blank["badge_label"] is None
    assert blank["button_url"] is None

    resp = client.put(
        f"/hero-slides/{created['id']}",
        json={"heading_highlight": None, "badge_label": None, "button_label": None},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["heading_highlight"] is None
    assert resp.json()["badge_label"] is None
    assert resp.json()["button_label"] is None
    # An omitted key still means "leave it alone".
    assert resp.json()["heading"] == "Partner with"
    assert resp.json()["button_url"] == "/products"


def test_editing_a_hero_slides_copy_keeps_its_artwork(client, db_session):
    """The PUT carries no file, so rewording a headline must not blank the picture -
    that's what the separate /image endpoint is for."""
    make_admin(db_session, email="heroadmin5@example.com", password="password123")
    headers = auth_header(client, "heroadmin5@example.com", "password123")

    slide = client.post(
        "/hero-slides/",
        data={"heading": "Advanced Technology for"},
        files={"file": ("hero.png", _png_bytes(), "image/png")},
        headers=headers,
    ).json()
    original_image = slide["slide_image"]
    assert original_image

    reworded = client.put(
        f"/hero-slides/{slide['id']}", json={"heading": "Better Technology for"}, headers=headers
    ).json()
    assert reworded["heading"] == "Better Technology for"
    assert reworded["slide_image"] == original_image

    # Replacing the artwork is its own request, and does change it.
    replaced = client.post(
        f"/hero-slides/{slide['id']}/image",
        files={"file": ("new.png", _png_bytes(), "image/png")},
        headers=headers,
    ).json()
    assert replaced["slide_image"] != original_image


def test_writing_hero_slides_requires_product_management(client, db_session):
    """Deliberately NOT the `admin` gate the contact page's QR cards use: a hero slide
    is shop-window marketing, the same job as a promotion, so it answers to
    product_management. An owner holding only `admin` can look but not write."""
    _admin_only_staff(db_session, "heronotpm@example.com")
    headers = auth_header(client, "heronotpm@example.com", "password123")

    resp = client.post("/hero-slides/", data={"heading": "Sneaky"}, headers=headers)
    assert resp.status_code == 403
    assert "product_management" in resp.json()["detail"]

    # Reading stays open to everyone, including this account.
    assert client.get("/hero-slides/", headers=headers).status_code == 200


def test_deleting_a_hero_slide_removes_it_from_the_banner(client, db_session):
    make_admin(db_session, email="heroadmin6@example.com", password="password123")
    headers = auth_header(client, "heroadmin6@example.com", "password123")

    slide = client.post("/hero-slides/", data={"heading": "Temporary"}, headers=headers).json()
    assert client.delete(f"/hero-slides/{slide['id']}", headers=headers).status_code == 204
    assert client.get("/hero-slides/").json() == []
    assert client.get(f"/hero-slides/{slide['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------
# What these are actually protecting: the log is written by a flush listener
# (app/core/activity.py), not by the routers, so nothing in a router's own tests
# would notice it silently stopping. Each test below drives a real endpoint and
# then asks the log what it saw.


def _log(client, headers, **params):
    resp = client.get("/activity/", params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _entries_for(client, headers, entity_type, entity_id):
    resp = client.get(f"/activity/entity/{entity_type}/{entity_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_activity_log_records_create_update_and_delete(client, db_session):
    """The whole lifecycle of a row, including the part `updated_by_user_id` cannot
    reach: after the delete, the log is the only place the brand ever existed."""
    make_admin(db_session, email="actlog1@example.com", password="password123")
    headers = auth_header(client, "actlog1@example.com", "password123")

    brand_id = client.post("/brands/", data={"brand_name": "Trackable"}, headers=headers).json()["id"]
    client.put(f"/brands/{brand_id}", json={"brand_name": "Trackable Renamed"}, headers=headers)
    assert client.delete(f"/brands/{brand_id}", headers=headers).status_code == 204

    entries = _log(client, headers, entity_type="brands")["items"]
    # Newest first.
    assert [e["action"] for e in entries] == ["delete", "update", "create"]

    created, updated, deleted = entries[2], entries[1], entries[0]
    assert created["changes"]["brand_name"] == [None, "Trackable"]
    assert updated["changes"]["brand_name"] == ["Trackable", "Trackable Renamed"]
    # The row is gone; its last known name is not.
    assert deleted["entity_label"] == "Trackable Renamed"
    assert deleted["changes"]["brand_name"] == ["Trackable Renamed", None]


def test_activity_log_records_the_previous_value_after_an_intervening_commit(client, db_session):
    """The subtle one. Attribute history alone loses the old value once a commit has
    expired the object, so a second edit in the same request would file
    "price: null -> 88.00". app/core/activity.py falls back to reading the pre-flush
    row; without that fallback this assertion fails and nothing else does."""
    make_admin(db_session, email="actlog2@example.com", password="password123")
    headers = auth_header(client, "actlog2@example.com", "password123")

    brand_id = client.post("/brands/", data={"brand_name": "PriceCo"}, headers=headers).json()["id"]
    product = client.post(
        "/products/",
        json={"product_name": "Priced Item", "price": "100.00", "brand_id": brand_id},
        headers=headers,
    ).json()
    client.put(f"/products/{product['id']}", json={"price": "88.00"}, headers=headers)

    latest = _log(client, headers, entity_type="products")["items"][0]
    assert latest["changes"]["price"] == ["100.00", "88.00"]


def test_activity_log_never_stores_a_password(client, db_session):
    """A record of who changed a password must not become a record of what it became.
    The change is still logged - only the values are replaced."""
    make_admin(db_session, email="actlog3@example.com", password="password123")
    headers = auth_header(client, "actlog3@example.com", "password123")

    staff = client.post(
        "/users/",
        json={
            "user_name": "New Hire",
            "email": "newhire@example.com",
            "password": "hunter2hunter2",
            "role_title": "Sales",
        },
        headers=headers,
    ).json()

    entry = _entries_for(client, headers, "users", staff["id"])[0]
    assert entry["action"] == "create"
    assert entry["changes"]["hashed_password"] == ["***", "***"]
    serialized = str(entry)
    assert "hunter2hunter2" not in serialized
    # Nor the hash itself, which is worth just as little in a log and just as much to
    # somebody with an offline cracker.
    assert "$2b$" not in serialized


def test_activity_log_ignores_bookkeeping_only_writes(client, db_session):
    """A sign-in writes `last_login`, and `updated_at` moves on every flush. Neither
    is a change anybody made, and a log that recorded them would be unreadable."""
    make_admin(db_session, email="actlog4@example.com", password="password123")
    headers = auth_header(client, "actlog4@example.com", "password123")

    # Three more sign-ins, each of which writes last_login.
    for _ in range(3):
        auth_header(client, "actlog4@example.com", "password123")

    updates = _log(client, headers, entity_type="users", action="update")["items"]
    assert updates == []
    # The sign-ins themselves are recorded, just not as edits to the user row.
    logins = _log(client, headers, action="login")["items"]
    assert len(logins) >= 3
    assert all(e["actor_label"] == "Admin User" for e in logins)


def test_activity_log_records_a_refused_sign_in(client, db_session):
    """Committed on a request that raises 401 - a path where the session is closed
    without a commit, so the entry would roll back if auth.py didn't force one."""
    make_admin(db_session, email="actlog5@example.com", password="password123")

    resp = client.post(
        "/auth/login", data={"username": "actlog5@example.com", "password": "wrongpassword"}
    )
    assert resp.status_code == 401

    headers = auth_header(client, "actlog5@example.com", "password123")
    refused = _log(client, headers, action="login_failed")["items"]
    assert len(refused) == 1
    assert refused[0]["entity_label"] == "actlog5@example.com"
    assert refused[0]["actor_type"] == "system"


def test_activity_log_files_order_line_changes_under_the_order(client, db_session):
    """An order's lines are replaced wholesale (`order.items = built`), so the removals
    happen through a delete-orphan cascade that `session.deleted` never sees. Both
    sides have to show up, and both have to be filed under the order rather than under
    a table nobody can navigate to."""
    make_admin(db_session, email="actlog6@example.com", password="password123")
    headers = auth_header(client, "actlog6@example.com", "password123")

    brand_id = client.post("/brands/", data={"brand_name": "LineCo"}, headers=headers).json()["id"]
    first = client.post(
        "/products/",
        json={"product_name": "Line A", "price": "10.00", "brand_id": brand_id},
        headers=headers,
    ).json()
    second = client.post(
        "/products/",
        json={"product_name": "Line B", "price": "20.00", "brand_id": brand_id},
        headers=headers,
    ).json()

    order = client.post(
        "/orders/",
        json={
            "clinic_name": "Test Clinic",
            "phone": "012345678",
            "address": "Phnom Penh",
            "items": [{"product_id": first["id"], "qty": 1}],
        },
        headers=headers,
    ).json()

    # Creating the order must NOT also log its lines - the create entry covers them,
    # and fifteen "added line item" rows under one "created order" buries it.
    on_create = _entries_for(client, headers, "orders", order["id"])
    assert [e["action"] for e in on_create] == ["create"]

    client.put(
        f"/orders/{order['id']}",
        json={"items": [{"product_id": second["id"], "qty": 3}]},
        headers=headers,
    )

    notes = [e["note"] for e in _entries_for(client, headers, "orders", order["id"])]
    assert "Added line item" in notes
    assert "Removed line item" in notes

    added = next(
        e for e in _entries_for(client, headers, "orders", order["id"])
        if e["note"] == "Added line item"
    )
    assert added["entity_type"] == "orders"
    assert added["entity_id"] == order["id"]
    assert added["changes"]["product_name"] == [None, "Line B"]
    assert added["changes"]["qty"] == [None, 3]


def test_activity_log_attributes_a_customer_editing_their_own_profile(client, db_session):
    """The case `updated_by_user_id` structurally cannot record: there is no `User`
    involved, so that column correctly stays NULL and says nothing at all."""
    make_admin(db_session, email="actlog7@example.com", password="password123")
    make_customer(db_session, email="selfedit@example.com", password="customerpass1")

    customer_headers = customer_auth_header(client, "selfedit@example.com", "customerpass1")
    resp = client.put("/customers/me", json={"phone_num": "0999888777"}, headers=customer_headers)
    assert resp.status_code == 200, resp.text

    headers = auth_header(client, "actlog7@example.com", "password123")
    entry = _log(client, headers, entity_type="customers", actor_type="customer")["items"][0]
    assert entry["actor_type"] == "customer"
    assert entry["actor_label"] == "Test Customer"
    assert entry["actor_user_id"] is None
    assert entry["changes"]["phone_num"] == [None, "0999888777"]


def test_activity_log_filters_combine(client, db_session):
    make_admin(db_session, email="actlog8@example.com", password="password123")
    headers = auth_header(client, "actlog8@example.com", "password123")

    client.post("/brands/", data={"brand_name": "Zebrawood"}, headers=headers)
    client.post("/categories/", data={"category_name": "Maplewood"}, headers=headers)

    # Free text searches the recorded values, not just the labels.
    hits = _log(client, headers, q="Zebrawood")["items"]
    assert [e["entity_type"] for e in hits] == ["brands"]

    both = _log(client, headers, action="create")["items"]
    # The staff account the fixture created is in here too, and correctly so: the
    # listener is on the session, not on the HTTP layer, so a row written straight
    # through the ORM is logged like any other - with no actor, as "system".
    assert {e["entity_type"] for e in both} == {"brands", "categories", "users"}
    seeded = next(e for e in both if e["entity_type"] == "users")
    assert seeded["actor_type"] == "system"

    narrowed = _log(client, headers, action="create", entity_type="categories")["items"]
    assert len(narrowed) == 1
    assert narrowed[0]["entity_label"] == "Maplewood"

    # `total` counts what the filter matched, not what the page returned.
    page = _log(client, headers, action="create", limit=1)
    assert page["total"] == len(both)
    assert len(page["items"]) == 1


def test_activity_log_is_admin_only(client, db_session):
    """Not the price_listing-or-admin pair the rest of the Reports screen uses: the log
    spans staff accounts and permissions as well as prices. The per-record History
    panel answers to the same rule, so it can't be used as a way around the list."""
    from app.core.security import hash_password
    from app.models import User

    seller = User(
        user_name="Seller", email="actlog9@example.com",
        hashed_password=hash_password("password123"), role_title="Sales",
        is_active=True, is_verified=True, user_management=False, price_listing=True,
        product_management=True, customer_management=False, admin=False,
    )
    db_session.add(seller)
    db_session.commit()
    headers = auth_header(client, "actlog9@example.com", "password123")

    assert client.get("/activity/", headers=headers).status_code == 403
    assert client.get("/activity/filters", headers=headers).status_code == 403
    assert client.get("/activity/entity/products/1", headers=headers).status_code == 403
    # And anonymously.
    assert client.get("/activity/").status_code == 401


def test_activity_log_has_no_way_to_write_to_it(client, db_session):
    """Append-only is the point of the table, so the router offers no verb that could
    edit or clear it. A screen that could rewrite the log answers no question."""
    make_admin(db_session, email="actlog10@example.com", password="password123")
    headers = auth_header(client, "actlog10@example.com", "password123")

    client.post("/brands/", data={"brand_name": "Permanent"}, headers=headers)
    entry_id = _log(client, headers)["items"][0]["id"]

    for method, path in [
        ("post", "/activity/"),
        ("put", f"/activity/{entry_id}"),
        ("patch", f"/activity/{entry_id}"),
        ("delete", f"/activity/{entry_id}"),
    ]:
        resp = getattr(client, method)(path, headers=headers)
        assert resp.status_code in (404, 405), f"{method.upper()} {path} -> {resp.status_code}"


def test_entity_history_is_empty_rather_than_404_for_an_untouched_record(client, db_session):
    """Every row that existed before this table did has no history, and so does a
    record nobody has edited. "Nothing yet" is a real answer, not an error."""
    make_admin(db_session, email="actlog11@example.com", password="password123")
    headers = auth_header(client, "actlog11@example.com", "password123")

    assert _entries_for(client, headers, "products", 999999) == []
    assert _entries_for(client, headers, "not_a_table", 1) == []
