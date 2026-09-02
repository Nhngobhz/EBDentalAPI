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


def build_option_groups(db: Session, groups, group_model, choice_model):
    """Validate submitted option groups and turn them into ORM rows.

    Same 400-not-500 contract as build_bundle_rows: an unknown product, or the
    same product listed twice inside one group, is a client mistake and is
    reported as one rather than surfacing as an opaque IntegrityError from
    uq_set_option_choice.

    Two rules are applied rather than merely checked, because both have a single
    sensible answer and rejecting would just make the admin form annoying:
      * a group with no default gets its first choice flagged as one, so every
        group always has a baseline configuration;
      * a second default in the same group is dropped to non-default, which the
        partial unique index would otherwise reject outright.
    A group with no choices at all IS rejected - it would render as an empty set
    of radio buttons that the customer can't answer.
    """
    rows = []
    for position, group in enumerate(groups):
        if not group.choices:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Option group '{group.name}' must have at least one choice",
            )

        seen: set[int] = set()
        choice_rows = []
        default_seen = False
        for index, choice in enumerate(group.choices):
            if choice.product_id in seen:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"product_id {choice.product_id} is listed more than once "
                        f"in option group '{group.name}'"
                    ),
                )
            seen.add(choice.product_id)
            if not db.query(Product).filter(Product.id == choice.product_id).first():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"product_id {choice.product_id} does not exist",
                )
            is_default = choice.is_default and not default_seen
            default_seen = default_seen or is_default
            choice_rows.append(
                choice_model(
                    product_id=choice.product_id,
                    qty=choice.qty,
                    price_delta=choice.price_delta,
                    is_default=is_default,
                    sort_order=index,
                )
            )
        if not default_seen:
            choice_rows[0].is_default = True

        rows.append(
            group_model(name=group.name, sort_order=position, choices=choice_rows)
        )
    return rows


def replace_option_groups(db: Session, set_, groups, group_model, choice_model) -> None:
    """Swap a set's option groups for a newly submitted list.

    The clear-flush-assign that replace_bundle_rows used to do, and load-bearing
    for the same reason: re-submitting a group that keeps one of its existing
    products would otherwise re-insert a (group_id, product_id) pair that is
    still in the table and die on uq_set_option_choice. Rows are built and
    validated first, so a bad payload 400s without touching what's saved.

    Not reconciled the way bundle rows now are, and the reason is that a group
    has no stable identity to reconcile ON. A bundle member is identified by its
    product_id; a group is a name and an ordered list of choices, either of which
    the edit may have been about, so matching an incoming group to a saved one
    would be a guess. The cost is that editing a set's option groups still fills
    the activity log with the whole structure rather than the part that moved -
    worth revisiting if that ever becomes the noisy screen.
    """
    rows = build_option_groups(db, groups, group_model, choice_model)
    set_.option_groups = []
    db.flush()
    set_.option_groups = rows


def default_choice(group):
    """The choice a group falls back to when the buyer picks nothing.

    The flagged default, or - for a group that somehow has none - its first
    choice, so a half-configured set still prices instead of 500ing. Returns
    None only for a group with no choices at all, which the caller treats as
    "nothing to pick here".
    """
    for choice in group.choices:
        if choice.is_default:
            return choice
    return group.choices[0] if group.choices else None


def choice_price_delta(choice, group) -> Decimal:
    """What picking `choice` adds to its set's price.

    The stored `price_delta` when there is one, otherwise the live difference
    between this choice's contents and the group default's - see the column
    comment on SetOptionChoice.price_delta for why NULL means "derive it".

    The default choice is always 0: it's the configuration the set's own price
    already covers, so it can't also be an upcharge on itself.
    """
    base = default_choice(group)
    if base is None or choice.id == base.id:
        return Decimal("0")
    if choice.price_delta is not None:
        return Decimal(choice.price_delta)
    if choice.product is None or base.product is None:
        return Decimal("0")
    return (choice.product.price * choice.qty) - (base.product.price * base.qty)


def resolve_set_options(set_, selections):
    """Work out which choice each of a set's option groups ends up on.

    `selections` is what the buyer sent - {group_id: choice_id} - and may be
    partial or empty; any group it doesn't mention falls back to its default, so
    an unconfigured purchase of a configurable set is still a valid one.

    Returns (chosen, total_delta): the SetOptionChoice per group in group order,
    and what they add to the set price combined.

    Raises 400 on a choice that isn't in the group it's claimed for, or a group
    that isn't in this set - both mean the client is working from a stale copy of
    the set, and silently substituting the default would reprice their order
    without telling them.
    """
    chosen = []
    total_delta = Decimal("0")
    group_ids = {g.id for g in set_.option_groups}

    for group_id in selections:
        if group_id not in group_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"option group {group_id} does not belong to set "
                    f"'{set_.set_name}' - reload the set and choose again"
                ),
            )

    for group in set_.option_groups:
        picked_id = selections.get(group.id)
        if picked_id is None:
            choice = default_choice(group)
        else:
            choice = next((c for c in group.choices if c.id == picked_id), None)
            if choice is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"choice {picked_id} is not an option for '{group.name}' - "
                        "reload the set and choose again"
                    ),
                )
        if choice is None:  # group with no choices at all - nothing to include
            continue
        chosen.append(choice)
        total_delta += choice_price_delta(choice, group)

    return chosen, total_delta


def set_contents(set_, chosen):
    """What a configured set actually contains: its fixed items, with any item a
    slot has taken over replaced by that slot's chosen product.

    A group CLAIMS the item its default choice names. "Includes a Smart Ray" plus
    a slot whose standard choice is that same Smart Ray describes one x-ray, not
    two - the slot is an upgrade path for the included item, not a second copy of
    it. Without this the standard build lists Smart Ray twice, and upgrading
    lists both Smart Ray and Smart Ray Pro, telling the customer they get a
    machine they aren't getting.

    A slot whose default names a product that ISN'T in `items` claims nothing and
    simply adds its choice, which is the other legitimate way to build a set.
    """
    claimed = set()
    for group in getattr(set_, "option_groups", []):
        base = default_choice(group)
        if base is not None:
            claimed.add(base.product_id)

    fixed = [item for item in set_.items if item.product_id not in claimed]
    return fixed + list(chosen or [])


def bundle_old_price(bundle, contents=None) -> Decimal | None:
    """The "was" price of a Promotion/Set - what the deal is measured against.

    `contents` overrides `bundle.items` for a configurable set, and should be
    what set_contents() returned: the fixed items with any slot-claimed one
    swapped for the chosen product. Option choices price exactly like fixed
    contents - both expose `.product` and `.qty` - so upgrading to a dearer
    laptop raises the "was" figure on its own and the saving printed on the quote
    stays honest without anyone maintaining a second number.

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
    combined = combined_contents_price(bundle.items if contents is None else contents)
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
    """Reconcile a bundle's contents with a newly submitted list.

    Only what actually differs is touched: a member that survives the edit keeps
    its row (and its id), a member that dropped out is deleted, a new one is
    inserted, and a member whose quantity changed is UPDATEd in place.

    That is a change of method, and the reason is the activity log. This used to
    clear the collection, flush, and assign the whole list back - which meant
    "add one product to a promotion that already has three" wrote four DELETEs
    and four INSERTs, and the log dutifully filed seven "Removed/Added included
    product" entries around the one thing anyone did. Worse, the one entry that
    was true said nothing the other six didn't. A log is only worth scrolling if
    an ordinary edit leaves an ordinary trace.

    The clear-flush-assign it replaces existed to dodge a real 500: assigning
    straight over the collection lets SQLAlchemy INSERT the incoming rows BEFORE
    it DELETEs the ones that dropped out, so re-submitting a member that is
    already saved re-inserts an (owner, product_id) pair still in the table and
    trips the unique constraint (`uq_promotion_item` and friends). Reconciling
    closes that at the source rather than working around it - a row is only ever
    inserted for a product that is NOT currently a member, so an insert can never
    collide with a pending delete and no intermediate flush is needed.

    The one behaviour given up: these collections order by row id (see
    Promotion.items), so re-submitting the same members in a different order no
    longer re-numbers them into that order. Reordering a bundle's contents by
    deleting and re-adding every member was never something the picker offered.

    Rows are still built (and validated) before anything is touched, so a bad
    product_id 400s without disturbing what's already saved.
    """
    rows = build_bundle_rows(db, items, model, exclude_product_id=exclude_product_id)
    current = {row.product_id: row for row in getattr(owner, attr)}

    keep = []
    for row in rows:
        existing = current.pop(row.product_id, None)
        if existing is None:
            keep.append(row)
            continue
        if existing.qty != row.qty:
            # An in-place UPDATE, which the activity log reads as "Edited included
            # product, qty 1 -> 3". The old path could only say removed-then-added.
            existing.qty = row.qty
        keep.append(existing)

    # Whatever is left in `current` was not resubmitted; assigning the merged list
    # orphans exactly those rows and the delete-orphan cascade removes them.
    setattr(owner, attr, keep)
