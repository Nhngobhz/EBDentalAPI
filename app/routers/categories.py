from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import stamp_updated_by
from app.core.deps import require_permission
from app.core.query import Limit, Skip
from app.database import get_db
from app.models import Category, User
from app.schemas import CategoryOut, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["Categories"])

_perm = Depends(require_permission("product_management"))


@router.get("/", response_model=list[CategoryOut])
def list_categories(skip: Skip = 0, limit: Limit = 50, db: Session = Depends(get_db)):
    """Public: the product catalog is meant to be browsable without an account."""
    return db.query(Category).order_by(Category.category_name).offset(skip).limit(limit).all()


@router.get("/{category_id}", response_model=CategoryOut)
def get_category(category_id: int, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return category


@router.post("/", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(
    category_name: str = Form(..., min_length=1, max_length=150),
    category_icon: str | None = Form(None, max_length=60),
    current_user: User = _perm,
    db: Session = Depends(get_db),
):
    """Still multipart/form-data, though there is no longer a file to attach: the
    admin screen posts this as an ordinary HTML form, and changing the content type
    here would only move that requirement somewhere else.

    The category image is gone (see models.Category.category_icon). A category tile
    on the storefront draws a glyph, never a photograph, so what an admin sets here
    is the glyph."""
    if db.query(Category).filter(Category.category_name == category_name).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category name already exists")
    category = Category(category_name=category_name, category_icon=(category_icon or "").strip() or None)
    stamp_updated_by(category, current_user)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.put("/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: int, payload: CategoryUpdate, current_user: User = _perm, db: Session = Depends(get_db)
):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(category, field, value)
    stamp_updated_by(category, current_user)
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
