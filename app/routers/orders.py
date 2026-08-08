"""
Order / OrderItem router.

An Order is a finalized storefront quote (see partials/quote_drawer.html and the
QuoteCart JS object on the EB Web Project frontend). It only ever accepts product_id +
qty per line from the client - price/discount/name/code/uom are always looked up from
the current Product row and snapshotted onto the OrderItem server-side, never trusted
from the request body. This keeps historical orders accurate even if a product's price
later changes or the product itself is deleted, and prevents a tampered request from
recording a fabricated discount.

A submitted line can also produce extra lines the client never asked for: a
Promotion/Set is a collection of products, and a Product may come with freebies, so
each of those expands into $0 COMPONENT lines under the paid one (see _component_items
and OrderItem.parent_item_id). Components are priced at zero by construction, so they
can never be used to shift a total - the "free" flag is not something a client can set.

An order stays editable by staff (PUT /{id}: clinic details, terms, discount, and the
line list itself) right up until payment is recorded, at which point the row freezes -
no edits and no deletion, see _reject_if_paid. Edits re-price every line from scratch
through the same _build_order_lines the original purchase used, so "trusted only from
the server" holds for an edited order exactly as it does for a new one.

Creating an order accepts either a staff (User) or Customer bearer token, mirroring how
POST /auth/login tries both - see _get_ordering_principal. Whichever kind of account is
calling must also meet the same "can place an order" bar the frontend enforces before
even showing the quote-cart UI: staff need price_listing or product_management,
customers need access_permission. This is enforced here too, not just hidden in the UI.
"""
from datetime import datetime, timezone
from decimal import Decimal

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.core.bundles import bundle_old_price
from app.core.audit import stamp_updated_by
from app.core.deps import oauth2_scheme, principal_id_from_token, require_permission
from app.core.files import ALLOWED_PDF_TYPES
from app.core.security import decode_access_token
from app.core.query import Limit, Skip
from app.database import get_db
from app.models import Customer, Order, OrderItem, Product, Promotion, Set, User
from app.schemas import OrderCreate, OrderOut, OrderUpdate
from app.services.khqr import build_khqr, check_bakong_payment
from app.services.payway import PayWayError, check_payway_payment, create_payway_khqr
from app.services.telegram import (
    deliver_order_alert,
    resolve_pending_quotation_pdf,
    send_khqr_pending_alert,
)

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

    principal_type = payload.get("type")
    if principal_type not in ("user", "customer"):
        raise credentials_exception
    principal_id = principal_id_from_token(payload, principal_type)
    if principal_id is None:
        raise credentials_exception

    if principal_type == "user":
        user = db.query(User).filter(User.id == principal_id).first()
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

    customer = db.query(Customer).filter(Customer.id == principal_id).first()
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


def _component_items(rows, parent_qty: int) -> list[OrderItem]:
    """Expands a bundle's contents into the $0 lines that go on the order under
    the paid line - the member products of a Promotion/Set, or the freebies a
    Product comes with (see PromotionItem/SetItem/ProductFreeItem).

    Snapshotted like any other line (name/code/uom copied, never re-derived), but
    always at unit_price/discount/line_amount = 0: the bundle's own price already
    covers them, so they must not move subtotal, the discount base, or the grand
    total. Quantities multiply - 2 of a set that contains 3 gloves is 6 gloves.
    """
    components = []
    for row in rows:
        product = row.product
        if product is None:  # defensive: ON DELETE CASCADE should have removed the row
            continue
        components.append(
            OrderItem(
                product_id=product.id,
                product_name=product.product_name,
                product_code=product.product_code,
                uom=product.uom,
                unit_price=Decimal("0"),
                # A component line is free by construction, so there's no "before"
                # price either - list_price matches unit_price at zero.
                list_price=Decimal("0"),
                discount_type="percent",
                discount=Decimal("0"),
                qty=row.qty * parent_qty,
                line_amount=Decimal("0"),
            )
        )
    return components


def _next_order_number(db: Session) -> str:
    last = db.query(Order).order_by(Order.id.desc()).first()
    return f"{(last.id + 1) if last else 1:06d}"


def _build_order_lines(db: Session, lines) -> tuple[list[OrderItem], Decimal, Decimal]:
    """Turns the client's [{product_id|promotion_id|set_id, qty}] into real OrderItem
    rows and returns (items, subtotal, discountable_subtotal).

    Shared by create_order and update_order so an edited order is priced by exactly the
    same rules a new one is - every price/name/discount looked up from the current
    Product/Promotion/Set row and snapshotted, never taken from the request. Bundles
    and free-gift products expand into the $0 component lines described in
    _component_items; `discountable_subtotal` excludes promotion/set lines, which are
    already a special deal price the order-level discount must not stack on top of.
    """
    items: list[OrderItem] = []
    subtotal = Decimal("0")
    discountable_subtotal = Decimal("0")
    now = datetime.now(timezone.utc)

    def add_line(parent: OrderItem, contents) -> None:
        """Records a paid line plus the $0 component lines spelling out what it
        includes. Components go into BOTH parent.components (so SQLAlchemy
        inserts the parent first and fills in their parent_item_id) and the flat
        `items` list (so they get order_id and show up in OrderOut.items),
        immediately after their parent so id order == print order."""
        components = _component_items(contents, parent.qty)
        parent.components = components
        items.append(parent)
        items.extend(components)

    for line in lines:
        if line.product_id is not None:
            product = db.query(Product).filter(Product.id == line.product_id).first()
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"product_id {line.product_id} does not exist",
                )
            line_amount = product.price * line.qty
            # Whatever this product comes with for free rides along as $0 lines.
            add_line(
                OrderItem(
                    product_id=product.id,
                    product_name=product.product_name,
                    product_code=product.product_code,
                    uom=product.uom,
                    unit_price=product.price,
                    # Snapshotted like every other field on this row: the printed
                    # quote's "UP before Discount" reads it directly instead of
                    # dividing unit_price back out.
                    list_price=product.list_price,
                    discount_type=product.discount_type,
                    discount=product.discount,
                    qty=line.qty,
                    line_amount=line_amount,
                ),
                product.free_items,
            )
            subtotal += line_amount
            discountable_subtotal += line_amount
        elif line.promotion_id is not None:
            # A Promotion (the storefront's homepage/promotions-page marketing deal) is
            # bought the same way a product is - see OrderItemCreate/schemas.py.
            promotion = db.query(Promotion).filter(Promotion.id == line.promotion_id).first()
            if not promotion:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"promotion_id {line.promotion_id} does not exist",
                )
            if not (promotion.start_date <= now <= promotion.end_date):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This promotion is not currently active",
                )
            line_amount = promotion.price * line.qty
            # Promotion has a real old_price (unlike Product, which only has
            # discount/discount_type) - reproduce it via the same discount_type="cash"
            # snapshot shape a product line uses, so deriveOldUnitPrice()/
            # derive_old_price() reconstruct it identically without a schema change.
            # bundle_old_price (not the raw column): for a promotion that lists
            # its contents, the "was" price is what those members cost
            # separately, so the saving printed on the quote is the real one.
            old_price = bundle_old_price(promotion)
            discount = (
                old_price - promotion.price
                if old_price and old_price > promotion.price
                else Decimal("0")
            )
            # A promotion is a collection of products: its members go on the
            # order as $0 lines under it, so the quote lists what's inside.
            add_line(
                OrderItem(
                    promotion_id=promotion.id,
                    product_name=promotion.promotion_name,
                    product_code=None,
                    uom=None,
                    unit_price=promotion.price,
                    # For a bundle the "before" price is what its contents would
                    # have cost bought separately (or the entered old_price when it
                    # lists none) - already computed above as `old_price`.
                    list_price=old_price or promotion.price,
                    discount_type="cash",
                    discount=discount,
                    qty=line.qty,
                    line_amount=line_amount,
                ),
                promotion.items,
            )
            subtotal += line_amount
            # A promotion is already a special deal price - the order-level discount
            # never stacks on top of it, so it's excluded from discountable_subtotal.
        else:
            # A Set (bundle deal on the Promotions page) - same purchase shape as a
            # Promotion, just no start/end date to check since a set is never
            # time-boxed.
            set_ = db.query(Set).filter(Set.id == line.set_id).first()
            if not set_:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"set_id {line.set_id} does not exist",
                )
            line_amount = set_.price * line.qty
            # Same contents-derived "was" price as a promotion line above.
            old_price = bundle_old_price(set_)
            discount = (
                old_price - set_.price
                if old_price and old_price > set_.price
                else Decimal("0")
            )
            # Same collection-of-products expansion as a promotion above.
            add_line(
                OrderItem(
                    set_id=set_.id,
                    product_name=set_.set_name,
                    product_code=None,
                    uom=None,
                    unit_price=set_.price,
                    # Same contents-derived "before" price as a promotion line.
                    list_price=old_price or set_.price,
                    discount_type="cash",
                    discount=discount,
                    qty=line.qty,
                    line_amount=line_amount,
                ),
                set_.items,
            )
            subtotal += line_amount
            # Same reasoning as a promotion: already a fixed deal price, excluded from
            # discountable_subtotal.

    return items, subtotal, discountable_subtotal


def _persisted_totals(order: Order) -> tuple[Decimal, Decimal]:
    """(subtotal, discountable_subtotal) read back off an order's ALREADY-SAVED lines,
    for an edit that changes the discount without touching the items. Applies the same
    two rules _build_order_lines does: $0 component lines don't count (they're free by
    construction), and promotion/set lines are outside the order-level discount's base.
    """
    subtotal = Decimal("0")
    discountable_subtotal = Decimal("0")
    for item in order.items:
        if item.parent_item_id is not None:
            continue
        subtotal += item.line_amount
        if item.promotion_id is None and item.set_id is None:
            discountable_subtotal += item.line_amount
    return subtotal, discountable_subtotal


def _compute_discount(
    discount_type: str, discount_value: Decimal, discountable_subtotal: Decimal
) -> Decimal:
    """The actual $ figure taken off, from the raw value staff entered. A cash discount
    is capped at the discountable base so an order can never go negative."""
    if discount_type == "percent":
        return discountable_subtotal * discount_value / Decimal("100")
    return min(discount_value, discountable_subtotal)


def _reject_if_paid(order: Order) -> None:
    """A paid order is frozen - no edits, no deletion, by anyone.

    Once payment is recorded a receipt exists for those exact numbers (and a paid-order
    Telegram alert carrying it has gone out), so the row has stopped being a working
    document and become the record of a completed sale. Enforced here rather than only
    in the admin UI, because hiding a button is not a rule.
    """
    if order.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This order has been paid - it can no longer be edited or deleted.",
        )


def _generate_quote_code(db: Session) -> str:
    """"C. Code" - a readable yymmddhhmmss timestamp, e.g. "260722070145", instead of
    the old random 2-letters-6-digits code. Seconds are included specifically so two
    orders placed in the same minute don't already collide on the column's uniqueness
    constraint; a "-2", "-3", ... suffix is still appended on the rarer same-second
    collision - the base timestamp stays intact and readable, only same-second
    duplicates get the extra suffix."""
    base = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    code = base
    suffix = 1
    while db.query(Order).filter(Order.quote_code == code).first():
        suffix += 1
        code = f"{base}-{suffix}"
    return code


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

    items, subtotal, discountable_subtotal = _build_order_lines(db, payload.items)
    discount_amount = _compute_discount(
        payload.discount_type, payload.discount_value, discountable_subtotal
    )
    grand_total = max(Decimal("0"), subtotal - discount_amount)

    # Quote vs. real order. Staff carts ARE the quotation tool, so a staff-placed row is
    # always a quote (payment_method doesn't apply). A customer must pick how they'll
    # pay: Cash means payment happens offline later, so what they get is also a quote;
    # KHQR is a real order that starts "unpaid" and carries a generated Bakong QR
    # payload the customer pays against (receipt only exists once it flips to "paid").
    order_number = _next_order_number(db)
    khqr_string = khqr_md5 = None
    if user is not None:
        order_type = "quote"
        payment_method = None
        payment_status = None
    else:
        payment_method = payload.payment_method
        if payment_method is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please choose a payment method (Cash or KHQR).",
            )
        if payment_method == "khqr":
            if not settings.khqr_configured:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="QR payment is not available right now - please choose Cash instead.",
                )
            order_type = "order"
            payment_status = "unpaid"
            # Two providers produce the same thing (a KHQR payload string the modal
            # renders): ABA PayWay generates it upstream and keeps khqr_md5 NULL
            # (payment is later checked by tran_id = order_number), Bakong-direct
            # builds it locally and stores the md5 Bakong's check API keys on. The
            # md5's presence is also how check_payment_status below knows which
            # provider a historical order belongs to.
            if settings.qr_provider == "payway":
                try:
                    khqr_string = create_payway_khqr(grand_total, tran_id=order_number)
                except PayWayError:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="QR payment is temporarily unavailable - please choose Cash instead.",
                    )
            else:
                khqr_string, khqr_md5 = build_khqr(grand_total, bill_number=order_number)
        else:
            order_type = "quote"
            payment_status = None

    order = Order(
        order_number=order_number,
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
        order_type=order_type,
        payment_method=payment_method,
        payment_status=payment_status,
        khqr_string=khqr_string,
        khqr_md5=khqr_md5,
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
    #
    # Quotes (staff or customer-cash) alert immediately: deliver_order_alert() briefly
    # waits for the browser to upload its real rendered PDF (see
    # POST /{order_id}/quotation-pdf below) before falling back to a server-rendered
    # one. A KHQR order instead gets a text-only "awaiting payment" alert now - its
    # PDF-carrying alert (the receipt) only goes out once payment is confirmed, from
    # check_payment_status/update_order below.
    order_out = OrderOut.model_validate(order)
    if payment_method == "khqr":
        background_tasks.add_task(send_khqr_pending_alert, order_out)
    else:
        background_tasks.add_task(deliver_order_alert, order_out)
    return order


@router.post("/{order_id}/quotation-pdf", status_code=status.HTTP_204_NO_CONTENT)
async def upload_quotation_pdf(
    order_id: int,
    file: UploadFile = File(...),
    principal: tuple[Customer | None, User | None] = Depends(_get_ordering_principal),
    db: Session = Depends(get_db),
):
    """Hands over the REAL client-rendered quotation PDF (QuoteCart.confirmPurchase() ->
    exportPDF() in main.js, built with html2canvas right after this order was placed) so
    its Telegram alert can include the exact document the customer received instead of
    the server's fpdf2 approximation - see deliver_order_alert/resolve_pending_quotation_pdf
    in services/telegram.py. Gated to the same principal who placed the order, so one
    account can't attach an arbitrary PDF to somebody else's order alert. A 404/403 here
    still means the order was placed fine - this is a best-effort enhancement to its
    Telegram alert, never something the purchase flow itself depends on."""
    customer, user = principal
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    owns_order = (customer is not None and order.customer_id == customer.id) or (
        user is not None and order.created_by_user_id == user.id
    )
    if not owns_order:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your order")

    if file.content_type not in ALLOWED_PDF_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be a PDF")
    contents = await file.read()
    max_bytes = settings.MAX_PDF_SIZE_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {settings.MAX_PDF_SIZE_MB} MB.",
        )

    resolve_pending_quotation_pdf(order.id, contents)
    return None


@router.get("/{order_id}/payment-status")
async def check_payment_status(
    order_id: int,
    background_tasks: BackgroundTasks,
    principal: tuple[Customer | None, User | None] = Depends(_get_ordering_principal),
    db: Session = Depends(get_db),
):
    """Polled by the browser while the KHQR modal is on screen (see the frontend's
    QuoteCart KHQR flow). While the order is unpaid and a Bakong API token is
    configured, each poll asks Bakong whether the transaction for this order's KHQR
    MD5 has gone through; the first confirmed poll flips the order to "paid" and fires
    the paid-order Telegram alert (which then waits ~20s for the browser to upload the
    real receipt PDF it's about to render). Without a Bakong token this still works -
    it just only ever reports what staff set manually via PUT /orders/{id}
    ("Mark as Paid" on the admin Orders page).

    Callable by the principal who placed the order, and by back-office staff
    (price_listing) for any order - staff can now generate a QR against an existing
    order themselves (POST /{order_id}/khqr below) and need to watch that same order
    settle while the customer is standing in front of them. price_listing is already
    what it takes to read the whole order via GET /orders/{id}, so this exposes
    nothing new."""
    customer, user = principal
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    owns_order = (
        (customer is not None and order.customer_id == customer.id)
        or (user is not None and order.created_by_user_id == user.id)
        or (user is not None and user.price_listing)
    )
    if not owns_order:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your order")
    if order.payment_method != "khqr":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This order has no QR payment to check"
        )

    if order.payment_status != "paid":
        # A stored khqr_md5 marks a Bakong-direct order (the md5 is what Bakong's
        # check API keys on); a PayWay order has none and is checked by
        # tran_id = order_number instead. Branching on the order's own artifact (not
        # the current settings) keeps historical orders checkable even if the
        # configured provider changes later.
        if order.khqr_md5:
            paid = await check_bakong_payment(order.khqr_md5)
        else:
            paid = await check_payway_payment(order.order_number)
        if paid:
            order.payment_status = "paid"
            order.paid_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(order)
            background_tasks.add_task(deliver_order_alert, OrderOut.model_validate(order))

    return {"payment_status": order.payment_status}


@router.post("/{order_id}/khqr", response_model=OrderOut)
def generate_order_khqr(
    order_id: int,
    _: User = _perm,
    db: Session = Depends(get_db),
):
    """Puts a scannable KHQR on an EXISTING order so staff can take payment for it -
    the counter/phone-call case the customer-facing checkout never covered. A staff cart
    always produces a quote with no payment attached (create_order); this is how that
    quote later becomes something a customer can pay by scanning.

    The QR is built for the order's CURRENT grand_total and keyed to its order_number,
    exactly as a customer KHQR checkout would build it, and the row is set to
    khqr/unpaid so the existing payment machinery takes over unchanged: the admin page
    polls GET /{order_id}/payment-status, the first confirmed check flips it to paid,
    stamps paid_at and fires the paid-order alert with the receipt.

    Idempotent: re-opening the QR dialog returns the payload already on the order
    rather than minting a new one, so a customer mid-scan never ends up looking at a
    code the order no longer expects. An edit that moves grand_total clears the stored
    QR (update_order) precisely so the next call here builds a fresh one for the new
    amount.

    order_type is deliberately left alone: a quote that gets paid at the counter is
    still the quote it started as, and the Quotes/Orders tabs go on meaning "how this
    row came to exist" rather than shuffling rows between tabs behind staff's backs.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    # Not _reject_if_paid's wording - "already paid" is the useful thing to say when
    # somebody asks for a payment QR.
    if order.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This order has already been paid.",
        )
    if not settings.khqr_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="QR payment is not configured - collect payment another way.",
        )

    if order.khqr_string:
        return order

    # Same two providers, same rule as create_order: PayWay generates the payload
    # upstream and leaves khqr_md5 NULL (checked later by tran_id = order_number),
    # Bakong-direct builds it locally and stores the md5 its check API keys on.
    # NOTE for PayWay: tran_id is the order_number, so regenerating after an edit
    # re-uses an id PayWay has already seen for this order. Bakong-direct (the
    # configured provider here) has no such constraint.
    if settings.qr_provider == "payway":
        try:
            khqr_string = create_payway_khqr(order.grand_total, tran_id=order.order_number)
        except PayWayError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="QR payment is temporarily unavailable - collect payment another way.",
            )
        khqr_md5 = None
    else:
        khqr_string, khqr_md5 = build_khqr(order.grand_total, bill_number=order.order_number)

    order.khqr_string = khqr_string
    order.khqr_md5 = khqr_md5
    order.payment_method = "khqr"
    order.payment_status = "unpaid"
    db.commit()
    db.refresh(order)
    return order


@router.get("/", response_model=list[OrderOut])
def list_orders(
    skip: Skip = 0,
    limit: Limit = 50,
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


@router.get("/mine", response_model=list[OrderOut])
def list_my_orders(
    skip: Skip = 0,
    limit: Limit = 50,
    principal: tuple[Customer | None, User | None] = Depends(_get_ordering_principal),
    db: Session = Depends(get_db),
):
    """The caller's OWN orders - what the storefront's account drawer ("Orders" tab)
    lists. Deliberately not the same thing as GET /orders/: that one is the staff
    back-office view and needs price_listing, which a customer never has, so a customer
    could not otherwise see even their own history. Ownership is taken from the token
    (the same _get_ordering_principal that gates placing an order), never from a query
    parameter, so this can't be pointed at somebody else's account.

    A staff member sees the quotes they themselves recorded (created_by_user_id); the
    full list stays on the admin Orders page.

    MUST stay declared above GET /{order_id} - FastAPI matches in declaration order and
    would otherwise try to parse "mine" as an int order_id and 422."""
    customer, user = principal
    query = db.query(Order).options(joinedload(Order.items))
    if customer is not None:
        query = query.filter(Order.customer_id == customer.id)
    else:
        query = query.filter(Order.created_by_user_id == user.id)
    return query.order_by(Order.id.desc()).offset(skip).limit(limit).all()


@router.get("/mine/{order_id}", response_model=OrderOut)
def get_my_order(
    order_id: int,
    principal: tuple[Customer | None, User | None] = Depends(_get_ordering_principal),
    db: Session = Depends(get_db),
):
    """One of the caller's own orders, in full (line items included) - what the account
    drawer opens when an order is tapped, and what it re-prints the PDF from.

    Same ownership gate as the quotation-pdf upload and the payment-status poll: 404 (not
    403) for somebody else's order, so this can't be used to probe which order ids exist.
    A customer can't use GET /{order_id} for this - that one needs price_listing."""
    customer, user = principal
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id).first()
    owns_order = order is not None and (
        (customer is not None and order.customer_id == customer.id)
        or (user is not None and order.created_by_user_id == user.id)
    )
    if not owns_order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, _: User = _perm, db: Session = Depends(get_db)):
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


@router.put("/{order_id}", response_model=OrderOut)
def update_order(
    order_id: int,
    payload: OrderUpdate,
    background_tasks: BackgroundTasks,
    current_user: User = _perm,
    db: Session = Depends(get_db),
):
    """Staff edit of a still-unpaid order: clinic/contact details, terms, the
    order-level discount, the line items themselves, the workflow status, and the
    "payment received" flag - see OrderUpdate.

    Editing stops the moment the order is paid (_reject_if_paid). Everything the
    customer was given a receipt for has to keep matching what is on record, so a paid
    row is read-only here and undeletable below; correcting one means issuing a new
    order alongside it, not rewriting history.

    Line items are re-priced from scratch, never patched: `items` replaces the whole
    list through the same _build_order_lines the original purchase went through, so an
    edited order carries exactly the snapshot shape (and the $0 bundle/free-gift
    component lines) a freshly placed one does.
    """
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    _reject_if_paid(order)

    fields = payload.model_dump(exclude_unset=True)
    # payload.items rather than the dumped dicts - _build_order_lines wants the
    # validated OrderItemCreate objects.
    new_items = payload.items if fields.pop("items", None) is not None else None

    # Every field left in `fields` maps to a NOT NULL column except these four, so an
    # explicit null means "clear it" only for them; anywhere else it would be an attempt
    # to blank out something the order can't be without.
    nullable = {"contact_person", "payment_term", "install_term", "payment_status"}
    for field, value in fields.items():
        if value is None and field not in nullable:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{field} cannot be empty",
            )

    # Same gate as placing an order with a discount: handing out money off is a
    # product_management call, not something every price_listing staffer can do.
    if (fields.get("discount_value") or Decimal("0")) > 0 and not current_user.product_management:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A discount requires the 'product_management' permission",
        )

    # Recording that payment landed. No longer KHQR-only: staff take cash and bank
    # transfers against a quote too, and "Mark as Paid" is how that gets on the record
    # (it is also the manual fallback when automatic Bakong/PayWay checking isn't
    # configured). Flipping to paid stamps paid_at and fires the paid-order alert, whose
    # attached document is now a Receipt - a customer's still-polling browser sees
    # "paid" on its next tick and renders it too.
    newly_paid = fields.get("payment_status") == "paid"

    # Discount type and value are two halves of one figure: whichever half wasn't sent
    # keeps its current value, so changing just the percentage doesn't silently reset
    # the type (and vice versa).
    discount_changed = "discount_type" in fields or "discount_value" in fields
    discount_type = fields.get("discount_type", order.discount_type)
    discount_value = fields.get("discount_value", order.discount_value)
    if discount_type == "percent" and discount_value > 100:
        # OrderUpdate can only catch this when both halves arrive together - here the
        # effective pair is known, so a percent type sent against an existing cash
        # value of e.g. 500 is caught as well.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A percent discount cannot exceed 100",
        )

    for field, value in fields.items():
        setattr(order, field, value)

    if new_items is not None:
        built, subtotal, discountable_subtotal = _build_order_lines(db, new_items)
        # delete-orphan on Order.items: assigning the new list drops the old rows (and
        # their components, which cascade off their parent line).
        order.items = built
    elif discount_changed:
        subtotal, discountable_subtotal = _persisted_totals(order)
    else:
        subtotal = discountable_subtotal = None

    if subtotal is not None:
        order.discount_amount = _compute_discount(
            discount_type, discount_value, discountable_subtotal
        )
        order.subtotal = subtotal
        previous_total = order.grand_total
        order.grand_total = max(Decimal("0"), subtotal - order.discount_amount)
        if order.grand_total != previous_total:
            # Any KHQR already issued for this order encodes the OLD amount, so it
            # would collect the wrong sum. Dropping it here is what makes
            # generate_order_khqr build a fresh one on the next request instead of
            # handing back a stale payload.
            order.khqr_string = None
            order.khqr_md5 = None

    if newly_paid:
        order.paid_at = datetime.now(timezone.utc)
    # Records who last touched the order - now a real edit trail, not just who marked it
    # delivered/cancelled/paid.
    stamp_updated_by(order, current_user)
    db.commit()
    db.refresh(order)
    if newly_paid:
        background_tasks.add_task(deliver_order_alert, OrderOut.model_validate(order))
    return order


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(order_id: int, _: User = _perm, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    # A paid order is a permanent financial record - see _reject_if_paid. Cancelling one
    # is a `status` change, which is itself blocked once paid; what's left is issuing a
    # correcting document, never making the original disappear.
    _reject_if_paid(order)
    db.delete(order)
    db.commit()
    return None
