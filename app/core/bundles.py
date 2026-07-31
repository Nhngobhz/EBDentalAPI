"""Helpers shared by the three "contains these products" collections:
Promotion.items, Set.items and Product.free_items (see BundleItemMixin in
app/models.py).

All three are edited the same way - the client sends a list of
{product_id, qty} and it REPLACES whatever was there. Omitting the field
entirely leaves the existing contents alone; that distinction is made by the
callers (an Optional[...] = None field on the *Update schemas), not here.
"""
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Product
from app.schemas import BundleItemIn


def combined_contents_price(rows) -> Decimal | None:
    """What a bundle's contents would cost bought separately, at each member's
    current selling price. None when there are no contents."""
    if not rows:
        return None
    total = Decimal("0")
    for row in rows:
        if row.product is None:  # defensive: ON DELETE CASCADE should have removed the row
            continue
        total += row.product.price * row.qty
    return total


def bundle_old_price(bundle) -> Decimal | None:
    """The "was" price of a Promotion/Set - what the deal is measured against.

    For a bundle WITH contents this is computed, not stored: the members priced
    separately are exactly what the customer would otherwise have paid, so that
    combined figure is the honest before-price and it can never drift out of
    date as member prices change. The manually entered `old_price` column is the
    fallback only for a bundle that lists no contents.

    Contents win even when they add up to LESS than the bundle's own price
    (user's explicit call, 2026-07-31): the number stays truthful about what's
    inside rather than disappearing, and it's a useful signal to whoever priced
    the bundle. Nothing downstream can turn that into a negative discount -
    create_order only treats a POSITIVE old_price-minus-price difference as one,
    so such a bundle simply books at its own price with no discount.
    """
    combined = combined_contents_price(bundle.items)
    if combined is not None:
        return combined
    return bundle.old_price


def build_bundle_rows(
    db: Session,
    items: list[BundleItemIn],
    model,
    *,
    exclude_product_id: int | None = None,
):
    """Validate a submitted contents list and turn it into join rows.

    400s (never 500s) on an unknown product_id or the same product listed
    twice - the DB's own unique constraint would otherwise surface the
    duplicate as an opaque IntegrityError. `exclude_product_id` is for
    Product.free_items, where a product can't come free with itself (the
    ck_product_free_item_not_self check constraint backs this up).
    """
    rows = []
    seen: set[int] = set()
    for item in items:
        if item.product_id in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"product_id {item.product_id} is listed more than once",
            )
        seen.add(item.product_id)
        if exclude_product_id is not None and item.product_id == exclude_product_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A product cannot come free with itself",
            )
        if not db.query(Product).filter(Product.id == item.product_id).first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"product_id {item.product_id} does not exist",
            )
        rows.append(model(product_id=item.product_id, qty=item.qty))
    return rows


def replace_bundle_rows(
    db: Session,
    owner,
    attr: str,
    items: list[BundleItemIn],
    model,
    *,
    exclude_product_id: int | None = None,
) -> None:
    """Swap a bundle's contents for a newly submitted list.

    The clear-flush-assign dance is load-bearing, not ceremony. Assigning
    straight over the collection lets SQLAlchemy INSERT the incoming rows
    BEFORE it DELETEs the ones that dropped out, so any edit that keeps an
    existing member re-inserts a (owner, product_id) pair that's still in the
    table and dies on the unique constraint (`uq_set_item` and friends) as a
    500. Flushing the deletes first makes "add one product to a set that
    already has two" - the most ordinary edit there is - actually work.

    Rows are built (and validated) before anything is cleared, so a bad
    product_id 400s without touching what's already saved.
    """
    rows = build_bundle_rows(db, items, model, exclude_product_id=exclude_product_id)
    setattr(owner, attr, [])
    db.flush()
    setattr(owner, attr, rows)
