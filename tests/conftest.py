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
# Uploads must never leave this machine during a test run. app/core/storage.py falls
# back to local disk only when R2 is UNconfigured, and a developer's .env normally has
# real R2 credentials in it - so without these five lines every image/PDF upload test
# quietly pushes objects into the live Cloudflare bucket, over the network. That made
# the upload tests both slow (network round trips, ~30s each) and failure-prone, and it
# wrote test fixtures into production storage.
os.environ["R2_ACCOUNT_ID"] = ""
os.environ["R2_ACCESS_KEY_ID"] = ""
os.environ["R2_SECRET_ACCESS_KEY"] = ""
os.environ["R2_BUCKET_NAME"] = ""
os.environ["R2_PUBLIC_BASE_URL"] = ""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import ratelimit
from app.database import Base, SessionLocal, engine
from app.main import app
from app.core.security import hash_password
from app.services import app_settings


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session", autouse=True)
def _clean_uploads():
    """Delete the files the upload tests write, once the run is over.

    The five R2 lines at the top of this file keep test uploads off Cloudflare by
    putting storage into local-disk mode - but local-disk mode is a real write, to
    settings.UPLOAD_DIR, which is the developer's own static/uploads tree. Truncating
    the tables between tests removes the ROWS that pointed at those files and nothing
    else, so every full run left ~20 stub PNGs and PDFs behind, orphaned and
    indistinguishable at a glance from real product photography.

    Snapshot-then-remove-what-is-new, rather than emptying the directory: that tree
    holds the actual uploaded images of a working dev database (124 of them here), and
    a fixture that wiped it would destroy a day of someone's cataloguing work the first
    time it ran. Anything already present when the session starts is left strictly
    alone, so the worst case of a bug in here is that test files survive - which is
    where we started.
    """
    upload_dir = settings.UPLOAD_DIR

    def snapshot():
        found = set()
        for dirpath, _, files in os.walk(upload_dir):
            found.update(os.path.join(dirpath, f) for f in files)
        return found

    before = snapshot()
    yield
    for path in snapshot() - before:
        try:
            os.remove(path)
        except OSError:
            # Never fail a passing run over cleanup - a locked file on Windows is
            # worth a leftover stub, not a red suite.
            pass


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate all tables between tests so each test starts from empty."""
    yield
    # The login throttle (app/core/ratelimit.py) is process-global, so without this
    # one test's deliberate bad-password attempts would count against the next
    # test's legitimate logins from the same TestClient address.
    ratelimit.reset()
    # Same reasoning for the settings cache (app/services/app_settings.py): it memoizes
    # the merged settings dict process-wide for 30s, so a test that changes a setting
    # would otherwise leak that value into the next test even after this truncate.
    app_settings.invalidate()
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
        # Mirrors migration a3d81f6c94e2, which grants `admin` to every account that
        # already held all four flags above. A test "admin" should be the same thing a
        # real deployment's admin is.
        admin=True,
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
