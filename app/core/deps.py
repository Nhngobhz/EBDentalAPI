"""
Reusable FastAPI dependencies: who is calling, and are they allowed to.

RBAC model used throughout the API:
  - `role_title` is a free-text label (e.g. "Sales Manager"). It is never
    checked for authorization - it's just for display.
  - The boolean columns on User (user_management, price_listing,
    product_management, customer_management, admin) are the actual source of
    truth. `require_permission("product_management")` (etc.) is used as a
    dependency on every mutating endpoint that needs it.
  - A user with all four of the original permissions set to True is a de-facto
    super admin. There is no separate `is_superuser` flag (not in the requested
    schema) - the bootstrap admin created by scripts/create_admin.py
    simply gets them all set to True.
  - `admin` was added later and gates site-wide configuration only (the Settings
    screen, app/routers/settings.py). It is NOT implied by the other four and is
    not a superset of them: an `admin` holder can change the store's phone number
    and the printed quote wording without gaining the ability to edit products or
    create staff accounts. Migration a3d81f6c94e2 grants it to accounts that
    already held all four, so existing owners keep working.
"""
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models import Customer, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

# Same token endpoint, but doesn't 401 when no token is supplied - used to
# let public endpoints (like product listing) optionally recognize a caller.
_optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

PERMISSION_NAMES = (
    "user_management",
    "price_listing",
    "product_management",
    "customer_management",
    "admin",
)


def principal_id_from_token(payload: dict, expected_type: str) -> int | None:
    """The `sub` claim as an int, or None if this token isn't a usable one of
    `expected_type`.

    `sub` is whatever was signed into the token, and a token signed with a
    non-numeric sub (an older format, a hand-rolled one, a bug elsewhere) used to
    reach `int(sub)` directly and raise ValueError - which surfaces as a 500 and a
    Telegram error alert instead of the 401 it actually is."""
    if payload.get("type") != expected_type:
        return None
    sub = payload.get("sub")
    try:
        return int(sub)
    except (TypeError, ValueError):
        return None


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise credentials_exception
    user_id = principal_id_from_token(payload, "user")
    if user_id is None:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated"
        )
    return user


def get_verified_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please confirm your email address before continuing",
        )
    return current_user


def get_current_customer(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> Customer:
    """Same shape as get_current_user, but for the separate customer login
    (POST /auth/customer/login). The "type" claim keeps a customer token
    from being usable as a staff token and vice versa - both tables use
    the same integer id space, so this isn't optional."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise credentials_exception
    customer_id = principal_id_from_token(payload, "customer")
    if customer_id is None:
        raise credentials_exception

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer is None:
        raise credentials_exception
    if not customer.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated"
        )
    return customer


def get_verified_customer(current_customer: Customer = Depends(get_current_customer)) -> Customer:
    if not current_customer.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please confirm your email address before continuing",
        )
    return current_customer


def get_price_visibility(
    token: str | None = Depends(_optional_oauth2_scheme), db: Session = Depends(get_db)
) -> bool:
    """Whether the caller may see real product price/discount values:
    any active staff user, or a customer with access_permission=True.
    Anonymous callers and customers without access_permission get masked
    prices - see ProductOut and app.routers.products._serialize_product.
    Never raises: an invalid/missing token just means masked prices, since
    product browsing itself stays public."""
    if not token:
        return False
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        return False

    user_id = principal_id_from_token(payload, "user")
    if user_id is not None:
        user = db.query(User).filter(User.id == user_id).first()
        return bool(user and user.is_active)
    customer_id = principal_id_from_token(payload, "customer")
    if customer_id is not None:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        return bool(customer and customer.is_active and customer.access_permission)
    return False


def require_permission(permission: str):
    """Dependency factory: Depends(require_permission("product_management"))"""
    if permission not in PERMISSION_NAMES:
        raise ValueError(f"Unknown permission: {permission}")

    def _checker(current_user: User = Depends(get_verified_user)) -> User:
        if not getattr(current_user, permission, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the '{permission}' permission",
            )
        return current_user

    return _checker


def require_any_permission(*permissions: str):
    """Like require_permission, but ANY one of them is enough.

    Exists because `admin` is a flag about the store itself rather than a job, so it
    isn't implied by - and doesn't imply - the four workaday permissions. The orders
    area needs both doors open: sales staff hold `price_listing`, while the owner may
    only hold `admin` and still has to be able to look at the day's sales and record a
    payment. Spelling that as two OR'd checks keeps `admin` from quietly becoming a
    superuser everywhere - each endpoint still names the doors it opens.
    """
    unknown = [p for p in permissions if p not in PERMISSION_NAMES]
    if unknown:
        raise ValueError(f"Unknown permission: {unknown[0]}")

    def _checker(current_user: User = Depends(get_verified_user)) -> User:
        if not any(getattr(current_user, p, False) for p in permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This action requires the "
                + " or ".join(f"'{p}'" for p in permissions)
                + " permission",
            )
        return current_user

    return _checker
