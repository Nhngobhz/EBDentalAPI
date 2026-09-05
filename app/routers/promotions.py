from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.bundles import build_bundle_rows, bundle_old_price, replace_bundle_rows
from app.core.audit import stamp_updated_by
from app.core.deps import get_price_visibility, require_permission
from app.core.files import save_named_image
from app.core.query import Limit, Skip
from app.database import get_db
from app.models import Promotion, PromotionItem, User
from app.schemas import PromotionCreate, PromotionOut, PromotionUpdate, SectionFilter

router = APIRouter(prefix="/promotions", tags=["Promotions"])

# Editing a promotion is a catalogue change, not a pricing one: it decides what the
# store advertises and which products come in the bundle, so it gates on
# product_management exactly as products/brands/categories do. A price_listing-only
# staffer can still read every promotion (the GETs below are public) and sell it -
# they just can't author it.
_perm = Depends(require_permission("product_management"))

_MASKED_PRICE = "XXXX"


def _serialize_promotion(promotion: Promotion, can_view_price: bool) -> dict:
    """Same masking rule as products (see app.routers.products._serialize_product):
    only staff and customers with access_permission=True get the real
    price/old_price."""
    data = PromotionOut.model_validate(promotion).model_dump()
    # A promotion that lists its contents prices its "was" figure off them
    # rather than off the stored column - see bundle_old_price.
    data["old_price"] = bundle_old_price(promotion)
    if not can_view_price:
        data["price"] = _MASKED_PRICE
        data["old_price"] = None
    return data


@router.get("/", response_model=list[PromotionOut])
def list_promotions(
    skip: Skip = 0,
    limit: Limit = 50,
    active_only: bool = False,
    section: SectionFilter = "all",
    can_view_price: bool = Depends(get_price_visibility),
    db: Session = Depends(get_db),
):
    """Public: promotions power the storefront and should be visible to
    anyone. Pass active_only=true to only get promotions currently
    running (start_date <= now <= end_date). Price/old_price are masked
    unless the caller is staff or a customer with access_permission=True.

    `section` narrows the list to one shop's deals. Defaults to "all" (unlike
    GET /products/) for the same reason GET /hero-slides/ does: every promotion
    predating the column is machinery, so an unfiltered call returns what it always
    returned, and the storefront pages pass their own section explicitly."""
    query = db.query(Promotion)
    if active_only:
        now = datetime.now(timezone.utc)
        query = query.filter(Promotion.start_date <= now, Promotion.end_date >= now)
    if section != "all":
        query = query.filter(Promotion.section == section)
    promotions = query.order_by(Promotion.start_date.desc()).offset(skip).limit(limit).all()
    return [_serialize_promotion(p, can_view_price) for p in promotions]


@router.get("/{promotion_id}", response_model=PromotionOut)
def get_promotion(
    promotion_id: int,
    can_view_price: bool = Depends(get_price_visibility),
    db: Session = Depends(get_db),
):
    promotion = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promotion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")
    return _serialize_promotion(promotion, can_view_price)


@router.post("/", response_model=PromotionOut, status_code=status.HTTP_201_CREATED)
def create_promotion(payload: PromotionCreate, current_user: User = _perm, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"items"})
    promotion = Promotion(**data, items=build_bundle_rows(db, payload.items, PromotionItem))
    stamp_updated_by(promotion, current_user)
    db.add(promotion)
    db.commit()
    db.refresh(promotion)
    # Serialized (not returned raw) so old_price is the contents-derived figure
    # here too, exactly as a later GET would report it. can_view_price=True: every
    # write endpoint below is staff, and staff always see real prices.
    return _serialize_promotion(promotion, True)


@router.put("/{promotion_id}", response_model=PromotionOut)
def update_promotion(
    promotion_id: int,
    payload: PromotionUpdate,
    current_user: User = _perm,
    db: Session = Depends(get_db),
):
    promotion = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promotion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")

    data = payload.model_dump(exclude_unset=True)
    new_start = data.get("start_date", promotion.start_date)
    new_end = data.get("end_date", promotion.end_date)
    if new_end <= new_start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_date must be after start_date")

    # Contents are replaced wholesale when sent and left alone when omitted -
    # see replace_bundle_rows, which reconciles rather than re-creating.
    if "items" in data:
        replace_bundle_rows(db, promotion, "items", payload.items or [], PromotionItem)
        del data["items"]

    for field, value in data.items():
        setattr(promotion, field, value)
    stamp_updated_by(promotion, current_user)
    db.commit()
    db.refresh(promotion)
    return _serialize_promotion(promotion, True)


@router.post("/{promotion_id}/image", response_model=PromotionOut)
async def upload_promotion_image(
    promotion_id: int, file: UploadFile, _: User = _perm, db: Session = Depends(get_db)
):
    """The deal's CARD artwork - the square tiles, the promotions page, the admin
    thumbnail. The wide hero banner is the separate endpoint below."""
    promotion = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promotion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")
    promotion.promotion_image = await save_named_image(file, "promotions", promotion.promotion_name)
    db.commit()
    db.refresh(promotion)
    return _serialize_promotion(promotion, True)


@router.post("/{promotion_id}/banner", response_model=PromotionOut)
async def upload_promotion_banner(
    promotion_id: int, file: UploadFile, _: User = _perm, db: Session = Depends(get_db)
):
    """The deal's WIDE artwork, for the storefront's hero slide only - see
    models.Promotion.banner_image for why that is a different picture rather than a
    different crop of the card one.

    Named " banner" (with the trailing suffix save_named_image appends) so the two
    pictures for one promotion can't overwrite each other: both are keyed off the
    promotion's name, and without the distinct suffix uploading a banner would replace
    the card image on disk while both columns went on pointing at the same file."""
    promotion = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promotion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")
    promotion.banner_image = await save_named_image(
        file, "promotions", f"{promotion.promotion_name} banner"
    )
    db.commit()
    db.refresh(promotion)
    return _serialize_promotion(promotion, True)


@router.delete("/{promotion_id}/banner", response_model=PromotionOut)
def delete_promotion_banner(
    promotion_id: int, _: User = _perm, db: Session = Depends(get_db)
):
    """Drop the hero banner and go back to the card image up top.

    Uploads elsewhere have no counterpart to this because a primary image has no
    "unset" that leaves anything behind - clearing one just leaves a placeholder. This
    one has a real fallback (promotion_image), so removing a banner is a meaningful
    thing to want, and there is otherwise no way back: the file input can replace a
    banner but never clear one."""
    promotion = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promotion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")
    promotion.banner_image = None
    db.commit()
    db.refresh(promotion)
    return _serialize_promotion(promotion, True)


@router.delete("/{promotion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_promotion(promotion_id: int, _: User = _perm, db: Session = Depends(get_db)):
    promotion = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promotion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")
    db.delete(promotion)
    db.commit()
    return None
