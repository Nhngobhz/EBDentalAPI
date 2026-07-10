"""
Bootstrap script: create the very first admin account, bypassing the API.

This solves the chicken-and-egg problem where creating a User via the API
normally requires an existing user with user_management=True.

Usage:
    python -m scripts.create_admin
    (run from the project root, with the virtualenv activated and
     DATABASE_URL pointing at your Postgres instance)

You will be prompted for name/email/password. The created account gets
ALL FOUR permissions set to True and is marked as already verified/active,
so it can log in immediately.
"""
import argparse
import getpass
import sys

from app.database import SessionLocal
from app.core.security import hash_password
from app.models import User


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first admin user")
    parser.add_argument("--name", help="Full name")
    parser.add_argument("--email", help="Login email")
    parser.add_argument("--password", help="Password (omit to be prompted securely)")
    args = parser.parse_args()

    name = args.name or input("Admin name: ").strip()
    email = args.email or input("Admin email: ").strip()
    password = args.password or getpass.getpass("Admin password: ")

    if not name or not email or not password:
        print("Name, email and password are all required.", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            print(f"A user with email {email} already exists.", file=sys.stderr)
            sys.exit(1)

        admin = User(
            user_name=name,
            email=email,
            hashed_password=hash_password(password),
            role_title="Admin",
            is_active=True,
            is_verified=True,  # bootstrap account skips email confirmation
            user_management=True,
            price_listing=True,
            product_management=True,
            customer_management=True,
        )
        db.add(admin)
        db.commit()
        print(f"Admin user '{email}' created successfully (id={admin.id}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
