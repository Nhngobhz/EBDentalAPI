from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session, joinedload

from app.core.deps import require_permission
from app.core.files import save_image, save_pdf
from app.core.query import OptionalInt
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
    skip: int = 0,
    limit: int = 50,
    product_id: OptionalInt = None,
    db: Session = Depends(get_db),
):
    """Public: support documentation should be reachable without an account."""
    query = db.query(Manual).options(joinedload(Manual.product))
    if product_id is not None:
        query = query.filter(Manual.product_id == product_id)
    return query.order_by(Manual.id).offset(skip).limit(limit).all()


@router.get("/{manual_id}", response_model=ManualOut)
def get_manual(manual_id: int, db: Session = Depends(get_db)):
    return _get_manual_or_404(db, manual_id)


@router.post("/", response_model=ManualOut, status_code=status.HTTP_201_CREATED)
async def create_manual(
    product_id: int = Form(...),
    description: Optional[str] = Form(None),
    file: UploadFile | None = File(None),
    _: User = _perm,
    db: Session = Depends(get_db),
):
    """Accepts multipart/form-data so the PDF can be attached in the same
    request - no separate POST /{id}/pdf call needed. That endpoint still
    exists for replacing the PDF later."""
    if not db.query(Product).filter(Product.id == product_id).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="product_id does not exist")
    manual = Manual(product_id=product_id, description=description)
    if file is not None:
        manual.pdf = await save_pdf(file, "manuals")
    db.add(manual)
    db.commit()
    db.refresh(manual)
    return _get_manual_or_404(db, manual.id)


@router.put("/{manual_id}", response_model=ManualOut)
def update_manual(
    manual_id: int, payload: ManualUpdate, _: User = _perm, db: Session = Depends(get_db)
):
    manual = db.query(Manual).filter(Manual.id == manual_id).first()
    if not manual:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manual not found")

    data = payload.model_dump(exclude_unset=True)
    if "product_id" in data and not db.query(Product).filter(Product.id == data["product_id"]).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="product_id does not exist")

    for field, value in data.items():
        setattr(manual, field, value)
    db.commit()
    return _get_manual_or_404(db, manual_id)


@router.post("/{manual_id}/image", response_model=ManualOut)
async def upload_manual_image(
    manual_id: int, file: UploadFile, _: User = _perm, db: Session = Depends(get_db)
):
    manual = db.query(Manual).filter(Manual.id == manual_id).first()
    if not manual:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manual not found")
    manual.manual_image = await save_image(file, "manuals")
    db.commit()
    return _get_manual_or_404(db, manual_id)


@router.post("/{manual_id}/pdf", response_model=ManualOut)
async def upload_manual_pdf(
    manual_id: int, file: UploadFile, _: User = _perm, db: Session = Depends(get_db)
):
    manual = db.query(Manual).filter(Manual.id == manual_id).first()
    if not manual:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manual not found")
    manual.pdf = await save_pdf(file, "manuals")
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
