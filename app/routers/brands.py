from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.core.files import save_image
from app.database import get_db
from app.models import Brand, User
from app.schemas import BrandOut, BrandUpdate

router = APIRouter(prefix="/brands", tags=["Brands"])

_perm = Depends(require_permission("product_management"))


@router.get("/", response_model=list[BrandOut])
def list_brands(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """Public: the product catalog is meant to be browsable without an account."""
    return db.query(Brand).order_by(Brand.brand_name).offset(skip).limit(limit).all()


@router.get("/{brand_id}", response_model=BrandOut)
def get_brand(brand_id: int, db: Session = Depends(get_db)):
    brand = db.query(Brand).filter(Brand.id == brand_id).first()
    if not brand:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")
    return brand


@router.post("/", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
async def create_brand(
    brand_name: str = Form(..., min_length=1, max_length=150),
    file: UploadFile | None = File(None),
    _: User = _perm,
    db: Session = Depends(get_db),
):
    """Accepts multipart/form-data so the brand image can be attached in
    the same request - no separate POST /{id}/image call needed. That
    endpoint still exists for replacing the image later."""
    if db.query(Brand).filter(Brand.brand_name == brand_name).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Brand name already exists")
    brand = Brand(brand_name=brand_name)
    if file is not None:
        brand.brand_image = await save_image(file, "brands")
    db.add(brand)
    db.commit()
    db.refresh(brand)
    return brand


@router.put("/{brand_id}", response_model=BrandOut)
def update_brand(
    brand_id: int, payload: BrandUpdate, _: User = _perm, db: Session = Depends(get_db)
):
    brand = db.query(Brand).filter(Brand.id == brand_id).first()
    if not brand:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(brand, field, value)
    db.commit()
    db.refresh(brand)
    return brand


@router.post("/{brand_id}/image", response_model=BrandOut)
async def upload_brand_image(
    brand_id: int, file: UploadFile, _: User = _perm, db: Session = Depends(get_db)
):
    brand = db.query(Brand).filter(Brand.id == brand_id).first()
    if not brand:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")
    brand.brand_image = await save_image(file, "brands")
    db.commit()
    db.refresh(brand)
    return brand


@router.delete("/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_brand(brand_id: int, _: User = _perm, db: Session = Depends(get_db)):
    brand = db.query(Brand).filter(Brand.id == brand_id).first()
    if not brand:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found")
    try:
        db.delete(brand)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a brand that still has products assigned to it",
        )
    return None
