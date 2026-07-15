"""
Reusable FastAPI dependencies: who is calling, and are they allowed to.

RBAC model used throughout the API:
  - `role_title` is a free-text label (e.g. "Sales Manager"). It is never
    checked for authorization - it's just for display.
  - The four boolean columns on User (user_management, price_listing,
    product_management, customer_management) are the actual source of
    truth. `require_permission("product_management")` (etc.) is used as a
    dependency on every mutating endpoint that needs it.
  - A user with ALL FOUR permissions set to True is a de-facto super
    admin. There is no separate `is_superuser` flag (not in the requested
    schema) - the bootstrap admin created by scripts/create_admin.py
    simply gets all four set to True.
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
)


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
        user_id = payload.get("sub")
        if user_id is None or payload.get("type") != "user":
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == int(user_id)).first()
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
        customer_id = payload.get("sub")
        if customer_id is None or payload.get("type") != "customer":
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    customer = db.query(Customer).filter(Customer.id == int(customer_id)).first()
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

    sub = payload.get("sub")
    if sub is None:
        return False

    if payload.get("type") == "user":
        user = db.query(User).filter(User.id == int(sub)).first()
        return bool(user and user.is_active)
    if payload.get("type") == "customer":
        customer = db.query(Customer).filter(Customer.id == int(sub)).first()
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
