from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.bundles import (
    build_bundle_rows,
    build_option_groups,
    bundle_old_price,
    choice_price_delta,
    default_choice,
    replace_bundle_rows,
    replace_option_groups,
    set_contents,
)
from app.core.audit import stamp_updated_by
from app.core.deps import get_price_visibility, require_permission
from app.core.files import save_named_image
from app.core.query import Limit, OptionalInt, Skip
from app.database import get_db
from app.models import Brand, Set, SetItem, SetOptionChoice, SetOptionGroup, User
from app.schemas import SetCreate, SetOut, SetUpdate

router = APIRouter(prefix="/sets", tags=["Sets"])

# Same reasoning as promotions: a set is catalogue authoring, so product_management
# owns it. price_listing alone reads and sells sets but doesn't create or change them.
_perm = Depends(require_permission("product_management"))

_MASKED_PRICE = "XXXX"


def _check_brand(db: Session, brand_id: int | None) -> None:
    """400 rather than a raw IntegrityError 500 on an unknown brand_id - same
    guard create_product runs. None is valid here: a set need not have a brand."""
    if brand_id is not None and not db.query(Brand).filter(Brand.id == brand_id).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="brand_id does not exist")


def _serialize_set(set_: Set, can_view_price: bool) -> dict:
    """Same masking rule as products/promotions (see
    app.routers.products._serialize_product): only staff and customers with
    access_permission=True get the real price/old_price."""
    data = SetOut.model_validate(set_).model_dump()
    # A set that lists its contents prices its "was" figure off them rather than
    # off the stored column - see bundle_old_price. The default choice of each
    # option group counts as contents here, because the price shown beside it is
    # the price of the set AS STANDARD: leaving them out would advertise a "was"
    # that omits the very laptop and x-ray the buyer is being quoted, and would
    # disagree with what _build_order_lines puts on the actual quote.
    defaults = [c for g in set_.option_groups if (c := default_choice(g)) is not None]
    data["old_price"] = bundle_old_price(set_, contents=set_contents(set_, defaults))

    # effective_delta is what each choice actually adds, resolved here rather
    # than in the schema because it depends on its siblings (the group's default
    # is the baseline) and, when price_delta is NULL, on the products' current
    # prices. The storefront prices upgrades from this field alone.
    for group, group_data in zip(set_.option_groups, data.get("option_groups") or []):
        for choice, choice_data in zip(group.choices, group_data.get("choices") or []):
            choice_data["effective_delta"] = choice_price_delta(choice, group)
            if not can_view_price:
                # An upcharge is a price. Leaving it visible would tell an
                # unentitled viewer exactly what the upgrade costs.
                choice_data["effective_delta"] = None
                choice_data["price_delta"] = None

    if not can_view_price:
        data["price"] = _MASKED_PRICE
        data["old_price"] = None
    return data


@router.get("/", response_model=list[SetOut])
def list_sets(
    skip: Skip = 0,
    limit: Limit = 50,
    brand_id: OptionalInt = None,
    can_view_price: bool = Depends(get_price_visibility),
    db: Session = Depends(get_db),
):
    """Public: sets power the storefront's Promotions page and should be
    visible to anyone. Price/old_price are masked unless the caller is
    staff or a customer with access_permission=True.

    `brand_id` narrows the list to one brand, the same way GET /products does
    for the catalog (see Set.brand_id)."""
    # option_groups -> choices -> product in one go: without it, serializing a
    # page of configurable sets costs a query per choice (effective_delta reads
    # each choice's product price). selectinload, not joinedload, so groups and
    # contents don't multiply each other's rows.
    query = db.query(Set).options(
        joinedload(Set.brand),
        selectinload(Set.option_groups)
        .selectinload(SetOptionGroup.choices)
        .joinedload(SetOptionChoice.product),
    )
    if brand_id is not None:
        query = query.filter(Set.brand_id == brand_id)
    sets = query.order_by(Set.created_at.desc()).offset(skip).limit(limit).all()
    return [_serialize_set(s, can_view_price) for s in sets]


@router.get("/{set_id}", response_model=SetOut)
def get_set(
    set_id: int,
    can_view_price: bool = Depends(get_price_visibility),
    db: Session = Depends(get_db),
):
    set_ = db.query(Set).filter(Set.id == set_id).first()
    if not set_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Set not found")
    return _serialize_set(set_, can_view_price)


@router.post("/", response_model=SetOut, status_code=status.HTTP_201_CREATED)
def create_set(payload: SetCreate, current_user: User = _perm, db: Session = Depends(get_db)):
    _check_brand(db, payload.brand_id)
    data = payload.model_dump(exclude={"items", "option_groups"})
    set_ = Set(
        **data,
        items=build_bundle_rows(db, payload.items, SetItem),
        option_groups=build_option_groups(
            db, payload.option_groups, SetOptionGroup, SetOptionChoice
        ),
    )
    stamp_updated_by(set_, current_user)
    db.add(set_)
    db.commit()
    db.refresh(set_)
    # Serialized (not returned raw) so old_price is the contents-derived figure
    # here too, exactly as a later GET would report it. can_view_price=True: every
    # write endpoint below is staff, and staff always see real prices.
    return _serialize_set(set_, True)


@router.put("/{set_id}", response_model=SetOut)
def update_set(
    set_id: int,
    payload: SetUpdate,
    current_user: User = _perm,
    db: Session = Depends(get_db),
):
    set_ = db.query(Set).filter(Set.id == set_id).first()
    if not set_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Set not found")

    data = payload.model_dump(exclude_unset=True)
    if "brand_id" in data:
        _check_brand(db, data["brand_id"])
    # Contents are replaced wholesale when sent and left alone when omitted -
    # see replace_bundle_rows, which reconciles rather than re-creating.
    if "items" in data:
        replace_bundle_rows(db, set_, "items", payload.items or [], SetItem)
        del data["items"]
    # Same omitted-vs-sent rule as `items`.
    if "option_groups" in data:
        replace_option_groups(
            db, set_, payload.option_groups or [], SetOptionGroup, SetOptionChoice
        )
        del data["option_groups"]

    for field, value in data.items():
        setattr(set_, field, value)
    stamp_updated_by(set_, current_user)
    db.commit()
    db.refresh(set_)
    return _serialize_set(set_, True)


@router.post("/{set_id}/image", response_model=SetOut)
async def upload_set_image(
    set_id: int, file: UploadFile, current_user: User = _perm, db: Session = Depends(get_db)
):
    set_ = db.query(Set).filter(Set.id == set_id).first()
    if not set_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Set not found")
    set_.set_image = await save_named_image(file, "sets", set_.set_name)
    stamp_updated_by(set_, current_user)
    db.commit()
    db.refresh(set_)
    return _serialize_set(set_, True)


@router.post("/{set_id}/detail-image", response_model=SetOut)
async def upload_set_detail_image(
    set_id: int, file: UploadFile, current_user: User = _perm, db: Session = Depends(get_db)
):
    """The optional second image shown under the name/description on the
    storefront set card (see Set.detail_image). Saved under a " detail"-suffixed
    name so it never overwrites the set's main image, which save_named_image
    would otherwise store under the exact same key."""
    set_ = db.query(Set).filter(Set.id == set_id).first()
    if not set_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Set not found")
    set_.detail_image = await save_named_image(file, "sets", f"{set_.set_name} detail")
    stamp_updated_by(set_, current_user)
    db.commit()
    db.refresh(set_)
    return _serialize_set(set_, True)


@router.delete("/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_set(set_id: int, _: User = _perm, db: Session = Depends(get_db)):
    set_ = db.query(Set).filter(Set.id == set_id).first()
    if not set_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Set not found")
    db.delete(set_)
    db.commit()
    return None
