from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.deps import get_price_visibility, require_permission
from app.core.files import save_named_image
from app.database import get_db
from app.models import Set, User
from app.schemas import SetCreate, SetOut, SetUpdate

router = APIRouter(prefix="/sets", tags=["Sets"])

_perm = Depends(require_permission("price_listing"))

_MASKED_PRICE = "XXXX"


def _serialize_set(set_: Set, can_view_price: bool) -> dict:
    """Same masking rule as products/promotions (see
    app.routers.products._serialize_product): only staff and customers with
    access_permission=True get the real price/old_price."""
    data = SetOut.model_validate(set_).model_dump()
    if not can_view_price:
        data["price"] = _MASKED_PRICE
        data["old_price"] = None
    return data


@router.get("/", response_model=list[SetOut])
def list_sets(
    skip: int = 0,
    limit: int = 50,
    can_view_price: bool = Depends(get_price_visibility),
    db: Session = Depends(get_db),
):
    """Public: sets power the storefront's Promotions page and should be
    visible to anyone. Price/old_price are masked unless the caller is
    staff or a customer with access_permission=True."""
    sets = db.query(Set).order_by(Set.created_at.desc()).offset(skip).limit(limit).all()
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
def create_set(payload: SetCreate, _: User = _perm, db: Session = Depends(get_db)):
    set_ = Set(**payload.model_dump())
    db.add(set_)
    db.commit()
    db.refresh(set_)
    return set_


@router.put("/{set_id}", response_model=SetOut)
def update_set(
    set_id: int,
    payload: SetUpdate,
    _: User = _perm,
    db: Session = Depends(get_db),
):
    set_ = db.query(Set).filter(Set.id == set_id).first()
    if not set_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Set not found")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(set_, field, value)
    db.commit()
    db.refresh(set_)
    return set_


@router.post("/{set_id}/image", response_model=SetOut)
async def upload_set_image(
    set_id: int, file: UploadFile, _: User = _perm, db: Session = Depends(get_db)
):
    set_ = db.query(Set).filter(Set.id == set_id).first()
    if not set_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Set not found")
    set_.set_image = await save_named_image(file, "sets", set_.set_name)
    db.commit()
    db.refresh(set_)
    return set_


@router.post("/{set_id}/detail-image", response_model=SetOut)
async def upload_set_detail_image(
    set_id: int, file: UploadFile, _: User = _perm, db: Session = Depends(get_db)
):
    """The optional second image shown under the name/description on the
    storefront set card (see Set.detail_image). Saved under a " detail"-suffixed
    name so it never overwrites the set's main image, which save_named_image
    would otherwise store under the exact same key."""
    set_ = db.query(Set).filter(Set.id == set_id).first()
    if not set_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Set not found")
    set_.detail_image = await save_named_image(file, "sets", f"{set_.set_name} detail")
    db.commit()
    db.refresh(set_)
    return set_


@router.delete("/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_set(set_id: int, _: User = _perm, db: Session = Depends(get_db)):
    set_ = db.query(Set).filter(Set.id == set_id).first()
    if not set_:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Set not found")
    db.delete(set_)
    db.commit()
    return None
