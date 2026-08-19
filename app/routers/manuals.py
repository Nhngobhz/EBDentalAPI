from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload

from app.core.audit import stamp_updated_by
from app.core.deps import require_permission
from app.core.files import save_image, save_pdf
from app.core.query import Limit, OptionalInt, Skip
from app.database import get_db
from app.models import Manual, Product, User
from app.schemas import ManualOut, ManualUpdate

router = APIRouter(prefix="/manuals", tags=["Manuals"])

_perm = Depends(require_permission("product_management"))


def _get_manual_or_404(db: Session, manual_id: int) -> Manual:
    manual = (
        db.query(Manual)
        .options(joinedload(Manual.product))
        .filter(Manual.id == manual_id)
        .first()
    )
    if not manual:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manual not found")
    return manual


@router.get("/", response_model=list[ManualOut])
def list_manuals(
    skip: Skip = 0,
    limit: Limit = 50,
    product_id: OptionalInt = None,
    db: Session = Depends(get_db),
):
    """Public: support documentation should be reachable without an account."""
    query = db.query(Manual).options(joinedload(Manual.product))
    if product_id is not None:
        query = query.filter(Manual.product_id == product_id)
    # Grouped by product, then oldest-first within a product, so a product's
    # documents always come back in the order they were added rather than
    # shuffling as titles are edited.
    return (
        query.order_by(Manual.product_id, Manual.id).offset(skip).limit(limit).all()
    )


@router.get("/{manual_id}", response_model=ManualOut)
def get_manual(manual_id: int, db: Session = Depends(get_db)):
    return _get_manual_or_404(db, manual_id)


@router.post("/", response_model=ManualOut, status_code=status.HTTP_201_CREATED)
async def create_manual(
    product_id: int = Form(...),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    file: UploadFile | None = File(None),
    current_user: User = _perm,
    db: Session = Depends(get_db),
):
    """Accepts multipart/form-data so the PDF can be attached in the same
    request - no separate POST /{id}/pdf call needed. That endpoint still
    exists for replacing the PDF later."""
    if not db.query(Product).filter(Product.id == product_id).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="product_id does not exist")
    # Built from an explicit field list, NOT payload.model_dump - a new column
    # added only to the schema would validate here and then be thrown away.
    manual = Manual(product_id=product_id, title=title, description=description)
    if file is not None:
        manual.pdf = await save_pdf(file, "manuals")
    stamp_updated_by(manual, current_user)
    db.add(manual)
    db.commit()
    db.refresh(manual)
    return _get_manual_or_404(db, manual.id)


@router.put("/{manual_id}", response_model=ManualOut)
def update_manual(
    manual_id: int, payload: ManualUpdate, current_user: User = _perm, db: Session = Depends(get_db)
):
    manual = db.query(Manual).filter(Manual.id == manual_id).first()
    if not manual:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manual not found")

    data = payload.model_dump(exclude_unset=True)
    if "product_id" in data and not db.query(Product).filter(Product.id == data["product_id"]).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="product_id does not exist")

    for field, value in data.items():
        setattr(manual, field, value)
    stamp_updated_by(manual, current_user)
    db.commit()
    return _get_manual_or_404(db, manual_id)


@router.post("/{manual_id}/image", response_model=ManualOut)
async def upload_manual_image(
    manual_id: int, file: UploadFile, current_user: User = _perm, db: Session = Depends(get_db)
):
    manual = db.query(Manual).filter(Manual.id == manual_id).first()
    if not manual:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manual not found")
    manual.manual_image = await save_image(file, "manuals")
    stamp_updated_by(manual, current_user)
    db.commit()
    return _get_manual_or_404(db, manual_id)


@router.post("/{manual_id}/pdf", response_model=ManualOut)
async def upload_manual_pdf(
    manual_id: int, file: UploadFile, current_user: User = _perm, db: Session = Depends(get_db)
):
    manual = db.query(Manual).filter(Manual.id == manual_id).first()
    if not manual:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manual not found")
    manual.pdf = await save_pdf(file, "manuals")
    stamp_updated_by(manual, current_user)
    db.commit()
    return _get_manual_or_404(db, manual_id)


@router.delete("/{manual_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_manual(manual_id: int, _: User = _perm, db: Session = Depends(get_db)):
    manual = db.query(Manual).filter(Manual.id == manual_id).first()
    if not manual:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manual not found")
    db.delete(manual)
    db.commit()
    return None
