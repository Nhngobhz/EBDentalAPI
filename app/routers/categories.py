from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.core.files import save_image
from app.database import get_db
from app.models import Category, User
from app.schemas import CategoryOut, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["Categories"])

_perm = Depends(require_permission("product_management"))


@router.get("/", response_model=list[CategoryOut])
def list_categories(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """Public: the product catalog is meant to be browsable without an account."""
    return db.query(Category).order_by(Category.category_name).offset(skip).limit(limit).all()


@router.get("/{category_id}", response_model=CategoryOut)
def get_category(category_id: int, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return category


@router.post("/", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    category_name: str = Form(..., min_length=1, max_length=150),
    file: UploadFile | None = File(None),
    _: User = _perm,
    db: Session = Depends(get_db),
):
    """Accepts multipart/form-data so the category image can be attached in
    the same request - no separate POST /{id}/image call needed. That
    endpoint still exists for replacing the image later."""
    if db.query(Category).filter(Category.category_name == category_name).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name already exists")
    category = Category(category_name=category_name)
    if file is not None:
        category.category_image = await save_image(file, "categories")
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.put("/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: int, payload: CategoryUpdate, _: User = _perm, db: Session = Depends(get_db)
):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(category, field, value)
    db.commit()
    db.refresh(category)
    return category


@router.post("/{category_id}/image", response_model=CategoryOut)
async def upload_category_image(
    category_id: int, file: UploadFile, _: User = _perm, db: Session = Depends(get_db)
):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    category.category_image = await save_image(file, "categories")
    db.commit()
    db.refresh(category)
    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, _: User = _perm, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    try:
        db.delete(category)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a category that still has products assigned to it",
        )
    return None
