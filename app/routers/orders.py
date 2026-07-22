"""
Order / OrderItem router.

An Order is a finalized storefront quote (see partials/quote_drawer.html and the
QuoteCart JS object on the EB Web Project frontend). It only ever accepts product_id +
qty per line from the client - price/discount/name/code/uom are always looked up from
the current Product row and snapshotted onto the OrderItem server-side, never trusted
from the request body. This keeps historical orders accurate even if a product's price
later changes or the product itself is deleted, and prevents a tampered request from
recording a fabricated discount.

Creating an order accepts either a staff (User) or Customer bearer token, mirroring how
POST /auth/login tries both - see _get_ordering_principal. Whichever kind of account is
calling must also meet the same "can place an order" bar the frontend enforces before
even showing the quote-cart UI: staff need price_listing or product_management,
customers need access_permission. This is enforced here too, not just hidden in the UI.
"""
import secrets
import string
from decimal import Decimal

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.core.deps import oauth2_scheme, require_permission
from app.core.security import decode_access_token
from app.database import get_db
from app.models import Customer, Order, OrderItem, Product, User
from app.schemas import OrderCreate, OrderOut, OrderUpdate
from app.services.telegram import send_order_alert

router = APIRouter(prefix="/orders", tags=["Orders"])

# Viewing/managing orders and placing one both gate on price_listing - orders are a
# money concept like Promotions, which already uses this same flag. No 5th permission
# flag is introduced (README calls "four explicit permission flags" a deliberate design
# point).
_perm = Depends(require_permission("price_listing"))


def _get_ordering_principal(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> tuple[Customer | None, User | None]:
    """Returns (customer, user) - exactly one is set. Raises 401 for a missing/invalid
    token, 403 for a deactivated/unverified account or one that doesn't meet the
    order-placing bar described above. Returning the full row (not just an id) lets
    create_order derive salesperson/quoted_by_name and the cash-discount permission check
    without a second query."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise credentials_exception

    sub = payload.get("sub")
    principal_type = payload.get("type")
    if sub is None or principal_type not in ("user", "customer"):
        raise credentials_exception

    if principal_type == "user":
        user = db.query(User).filter(User.id == int(sub)).first()
        if not user:
            raise credentials_exception
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")
        if not user.is_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please confirm your email address before continuing",
            )
        if not (user.price_listing or user.product_management):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Placing an order requires the 'price_listing' or 'product_management' permission",
            )
        return None, user

    customer = db.query(Customer).filter(Customer.id == int(sub)).first()
    if not customer:
        raise credentials_exception
    if not customer.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")
    if not customer.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please confirm your email address before continuing",
        )
    if not customer.access_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Placing an order requires price-visible account access",
        )
    return customer, None


def _next_order_number(db: Session) -> str:
    last = db.query(Order).order_by(Order.id.desc()).first()
    return f"{(last.id + 1) if last else 1:06d}"


_QUOTE_CODE_ALPHABET = string.ascii_uppercase
_QUOTE_CODE_DIGITS = string.digits


def _generate_quote_code(db: Session) -> str:
    """Random "C. Code" - 2 letters + 6 digits, e.g. "QT483920". Looped with a uniqueness
    check (collisions are astronomically unlikely at this alphabet size, but a quote code
    silently colliding with an older quote would be a real, confusing bug if it ever
    happened)."""
    for _ in range(10):
        code = "".join(secrets.choice(_QUOTE_CODE_ALPHABET) for _ in range(2)) + "".join(
            secrets.choice(_QUOTE_CODE_DIGITS) for _ in range(6)
        )
        if not db.query(Order).filter(Order.quote_code == code).first():
            return code
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not generate a unique quote code")


@router.post("/", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreate,
    background_tasks: BackgroundTasks,
    principal: tuple[Customer | None, User | None] = Depends(_get_ordering_principal),
    db: Session = Depends(get_db),
):
    customer, user = principal

    # Salesperson/quoted_by_name are always derived here, never trusted from the client
    # (see OrderCreate - it doesn't even accept them). A customer placing their own order
    # is recorded as "Website" for salesperson but keeps their own name for quoted_by_name.
    if user is not None:
        salesperson = user.user_name
        quoted_by_name = user.user_name
    else:
        salesperson = "Website"
        quoted_by_name = customer.customer_name

    # Any order-level discount (percent or cash) is a real reduction handed out at staff
    # discretion - gated to product_management specifically. A customer, or a
    # price_listing-only staffer, can still place an order, just not apply a discount to
    # it. Mirrors the quote drawer's UI, which only renders the discount control at all
    # for product_management staff.
    if payload.discount_value > 0:
        if user is None or not user.product_management:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A discount requires the 'product_management' permission",
            )

    items: list[OrderItem] = []
    subtotal = Decimal("0")
    discountable_subtotal = Decimal("0")
    for line in payload.items:
        product = db.query(Product).filter(Product.id == line.product_id).first()
        if not product:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"product_id {line.product_id} does not exist",
            )
        line_amount = product.price * line.qty
        items.append(
            OrderItem(
                product_id=product.id,
                product_name=product.product_name,
                product_code=product.product_code,
                uom=product.uom,
                unit_price=product.price,
                discount_type=product.discount_type,
                discount=product.discount,
                qty=line.qty,
                line_amount=line_amount,
            )
        )
        subtotal += line_amount
        # Promotional products carry a fixed promo price - the order-level discount below
        # never applies to them, so they're excluded from the base it's computed against.
        if product.product_type != "promotional":
            discountable_subtotal += line_amount

    if payload.discount_type == "percent":
        discount_amount = discountable_subtotal * payload.discount_value / Decimal("100")
    else:
        discount_amount = min(payload.discount_value, discountable_subtotal)

    grand_total = max(Decimal("0"), subtotal - discount_amount)

    order = Order(
        order_number=_next_order_number(db),
        quote_code=_generate_quote_code(db),
        customer_id=customer.id if customer else None,
        created_by_user_id=user.id if user else None,
        clinic_name=payload.clinic_name,
        contact_person=payload.contact_person,
        phone=payload.phone,
        address=payload.address,
        payment_term=payload.payment_term,
        salesperson=salesperson,
        quoted_by_name=quoted_by_name,
        install_term=payload.install_term,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        discount_amount=discount_amount,
        subtotal=subtotal,
        grand_total=grand_total,
        items=items,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    # Snapshotted into a plain OrderOut (not the ORM object) before handing off to the
    # background task - the task runs after this request's db session may already be
    # torn down, so a lazy-loaded relationship access there would raise
    # DetachedInstanceError. OrderOut.model_validate() reads everything needed (incl.
    # items) right now, while the session is still live.
    background_tasks.add_task(send_order_alert, OrderOut.model_validate(order))
    return order


@router.get("/", response_model=list[OrderOut])
def list_orders(
    skip: int = 0,
    limit: int = 50,
    status: str | None = None,
    customer_id: int | None = None,
    _: User = _perm,
    db: Session = Depends(get_db),
):
    query = db.query(Order).options(joinedload(Order.items))
    if status:
        query = query.filter(Order.status == status)
    if customer_id is not None:
        query = query.filter(Order.customer_id == customer_id)
    return query.order_by(Order.id.desc()).offset(skip).limit(limit).all()


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, _: User = _perm, db: Session = Depends(get_db)):
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


@router.put("/{order_id}", response_model=OrderOut)
def update_order(order_id: int, payload: OrderUpdate, _: User = _perm, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(order, field, value)
    db.commit()
    db.refresh(order)
    return order


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(order_id: int, _: User = _perm, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    db.delete(order)
    db.commit()
    return None
