from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.database import get_db
from app.models import Promotion, User
from app.schemas import PromotionCreate, PromotionOut, PromotionUpdate

router = APIRouter(prefix="/promotions", tags=["Promotions"])

_perm = Depends(require_permission("price_listing"))


@router.get("/", response_model=list[PromotionOut])
def list_promotions(
    skip: int = 0,
    limit: int = 50,
    active_only: bool = False,
    db: Session = Depends(get_db),
):
    """Public: promotions power the storefront and should be visible to
    anyone. Pass active_only=true to only get promotions currently
    running (start_date <= now <= end_date)."""
    query = db.query(Promotion)
    if active_only:
        now = datetime.now(timezone.utc)
        query = query.filter(Promotion.start_date <= now, Promotion.end_date >= now)
    return query.order_by(Promotion.start_date.desc()).offset(skip).limit(limit).all()


@router.get("/{promotion_id}", response_model=PromotionOut)
def get_promotion(promotion_id: int, db: Session = Depends(get_db)):
    promotion = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promotion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")
    return promotion


@router.post("/", response_model=PromotionOut, status_code=status.HTTP_201_CREATED)
def create_promotion(payload: PromotionCreate, _: User = _perm, db: Session = Depends(get_db)):
    promotion = Promotion(**payload.model_dump())
    db.add(promotion)
    db.commit()
    db.refresh(promotion)
    return promotion


@router.put("/{promotion_id}", response_model=PromotionOut)
def update_promotion(
    promotion_id: int,
    payload: PromotionUpdate,
    _: User = _perm,
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

    for field, value in data.items():
        setattr(promotion, field, value)
    db.commit()
    db.refresh(promotion)
    return promotion


@router.delete("/{promotion_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_promotion(promotion_id: int, _: User = _perm, db: Session = Depends(get_db)):
    promotion = db.query(Promotion).filter(Promotion.id == promotion_id).first()
    if not promotion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")
    db.delete(promotion)
    db.commit()
    return None
