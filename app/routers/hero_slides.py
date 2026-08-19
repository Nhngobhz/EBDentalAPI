"""
Hero slides: the big rotating banner on the storefront's home page and catalog.

Reading is public - those pages are served to anonymous visitors - while writes need
`product_management`, the same permission that governs Promotions and Sets. A hero
slide is a piece of shop-window marketing, authored by whoever writes the promotions,
not a system setting like the contact page's QR cards.

Create accepts multipart/form-data so a slide and its artwork arrive in one request,
exactly like POST /qr-codes/; later edits are JSON plus the separate image endpoint.
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.audit import stamp_updated_by
from app.core.deps import require_permission
from app.core.files import save_image
from app.core.query import Limit, Skip
from app.database import get_db
from app.models import HeroSlide, User
from app.schemas import HeroSlideOut, HeroSlideUpdate

router = APIRouter(prefix="/hero-slides", tags=["Hero Slides"])

_perm = Depends(require_permission("product_management"))


def _blank_to_none(value: str | None) -> str | None:
    """An HTML form posts an untouched optional field as "", not as absent. Storing
    that would leave a slide carrying an empty badge/button that the storefront then
    has to distinguish from a missing one, so it becomes NULL here."""
    value = (value or "").strip()
    return value or None


def _get(db: Session, slide_id: int) -> HeroSlide:
    slide = db.query(HeroSlide).filter(HeroSlide.id == slide_id).first()
    if not slide:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hero slide not found")
    return slide


@router.get("/", response_model=list[HeroSlideOut])
def list_hero_slides(
    active_only: bool = False,
    skip: Skip = 0,
    limit: Limit = 50,
    db: Session = Depends(get_db),
):
    """Public: the storefront renders this for signed-out visitors.

    `active_only` is what the storefront passes, so a parked slide disappears from the
    carousel; the admin list omits it and sees everything, switched on or not.

    Ordered by sort_order then id, so slides added later without an explicit position
    land at the end instead of in an arbitrary spot.
    """
    query = db.query(HeroSlide)
    if active_only:
        query = query.filter(HeroSlide.is_active.is_(True))
    return query.order_by(HeroSlide.sort_order, HeroSlide.id).offset(skip).limit(limit).all()


@router.get("/{slide_id}", response_model=HeroSlideOut)
def get_hero_slide(slide_id: int, db: Session = Depends(get_db)):
    return _get(db, slide_id)


@router.post("/", response_model=HeroSlideOut, status_code=status.HTTP_201_CREATED)
async def create_hero_slide(
    heading: str = Form(..., min_length=1, max_length=200),
    heading_highlight: str | None = Form(None, max_length=120),
    subheading: str | None = Form(None, max_length=400),
    badge_label: str | None = Form(None, max_length=60),
    badge_icon: str | None = Form(None, max_length=60),
    button_label: str | None = Form(None, max_length=60),
    button_url: str | None = Form(None, max_length=500),
    is_active: bool = Form(True),
    sort_order: int = Form(0),
    file: UploadFile | None = File(None),
    current_user: User = _perm,
    db: Session = Depends(get_db),
):
    slide = HeroSlide(
        heading=heading.strip(),
        heading_highlight=_blank_to_none(heading_highlight),
        subheading=_blank_to_none(subheading),
        badge_label=_blank_to_none(badge_label),
        badge_icon=_blank_to_none(badge_icon),
        button_label=_blank_to_none(button_label),
        button_url=_blank_to_none(button_url),
        is_active=is_active,
        sort_order=sort_order,
    )
    if file is not None:
        # save_image, not save_named_image: the named variant re-encodes as JPEG at a
        # fixed quality, and this artwork is displayed full-bleed across the widest
        # element on the site - the one place that recompression would actually show.
        slide.slide_image = await save_image(file, "hero")
    stamp_updated_by(slide, current_user)
    db.add(slide)
    db.commit()
    db.refresh(slide)
    return slide


@router.put("/{slide_id}", response_model=HeroSlideOut)
def update_hero_slide(
    slide_id: int,
    payload: HeroSlideUpdate,
    current_user: User = _perm,
    db: Session = Depends(get_db),
):
    slide = _get(db, slide_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(slide, field, value)
    stamp_updated_by(slide, current_user)
    db.commit()
    db.refresh(slide)
    return slide


@router.post("/{slide_id}/image", response_model=HeroSlideOut)
async def upload_hero_slide_image(
    slide_id: int, file: UploadFile, current_user: User = _perm, db: Session = Depends(get_db)
):
    slide = _get(db, slide_id)
    slide.slide_image = await save_image(file, "hero")
    stamp_updated_by(slide, current_user)
    db.commit()
    db.refresh(slide)
    return slide


@router.delete("/{slide_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_hero_slide(slide_id: int, _: User = _perm, db: Session = Depends(get_db)):
    slide = _get(db, slide_id)
    db.delete(slide)
    db.commit()
    return None
