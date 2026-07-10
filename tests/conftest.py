import os

# Point at the dedicated test database BEFORE any `app.*` module is
# imported, since app.config builds `settings` at import time.
os.environ["DATABASE_URL"] = (
    "postgresql+psycopg2://store_user:store_password@localhost:5432/store_db_test"
)
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ["MAIL_USERNAME"] = ""
os.environ["MAIL_PASSWORD"] = ""

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.core.security import hash_password


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate all tables between tests so each test starts from empty."""
    yield
    db = SessionLocal()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def make_admin(db_session, email="admin@example.com", password="adminpass123"):
    from app.models import User

    admin = User(
        user_name="Admin User",
        email=email,
        hashed_password=hash_password(password),
        role_title="Admin",
        is_active=True,
        is_verified=True,
        user_management=True,
        price_listing=True,
        product_management=True,
        customer_management=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    return admin


def auth_header(client, email, password):
    resp = client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def make_customer(db_session, email="customer@example.com", password="customerpass1", access_permission=False):
    """A verified, login-capable Customer - bypasses the email confirmation
    step for tests that only care about what happens after login."""
    from app.models import Customer

    customer = Customer(
        customer_name="Test Customer",
        email=email,
        hashed_password=hash_password(password),
        access_permission=access_permission,
        is_active=True,
        is_verified=True,
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def customer_auth_header(client, email, password):
    resp = client.post("/auth/customer/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
