"""
Department QR codes: the cards on the storefront's contact page.

Reading is public - the contact page is served to anonymous visitors - while every
write needs the `admin` permission, the same one the Settings screen uses. That's
deliberate: these captions and pictures used to BE settings (group "qr", removed in
migration d3b7f1c5a92e), so "who may rewrite what the contact page says" shouldn't
change just because the storage did.

Create accepts multipart/form-data so a card and its picture arrive in one request,
exactly like POST /brands/; later edits are JSON plus the separate image endpoint.
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.audit import stamp_updated_by
from app.core.deps import require_permission
from app.core.files import save_image
from app.core.query import Limit, Skip
from app.database import get_db
from app.models import QrCode, User
from app.schemas import QrBadgeVariant, QrCodeOut, QrCodeUpdate

router = APIRouter(prefix="/qr-codes", tags=["QR Codes"])

_admin = Depends(require_permission("admin"))


def _blank_to_none(value: str | None) -> str | None:
    """An HTML form posts an untouched optional field as "", not as absent. Storing
    that would leave a card carrying an empty subtitle/badge that the storefront then
    has to distinguish from a missing one, so it becomes NULL here.

    NOT applied to badge_variant, where "" is a real value - the default cyan pill.
    """
    value = (value or "").strip()
    return value or None


def _get(db: Session, qr_id: int) -> QrCode:
    qr = db.query(QrCode).filter(QrCode.id == qr_id).first()
    if not qr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QR code not found")
    return qr


@router.get("/", response_model=list[QrCodeOut])
def list_qr_codes(skip: Skip = 0, limit: Limit = 50, db: Session = Depends(get_db)):
    """Public: the contact page renders this for signed-out visitors.

    Ordered by sort_order then id, so cards added later without an explicit
    position land at the end of their group instead of in an arbitrary spot.
    """
    return (
        db.query(QrCode)
        .order_by(QrCode.sort_order, QrCode.id)
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{qr_id}", response_model=QrCodeOut)
def get_qr_code(qr_id: int, db: Session = Depends(get_db)):
    return _get(db, qr_id)


@router.post("/", response_model=QrCodeOut, status_code=status.HTTP_201_CREATED)
async def create_qr_code(
    title: str = Form(..., min_length=1, max_length=150),
    subtitle: str | None = Form(None, max_length=200),
    badge_label: str | None = Form(None, max_length=60),
    badge_variant: QrBadgeVariant = Form(""),
    badge_icon: str | None = Form(None, max_length=60),
    sort_order: int = Form(0),
    file: UploadFile | None = File(None),
    current_user: User = _admin,
    db: Session = Depends(get_db),
):
    qr = QrCode(
        title=title.strip(),
        subtitle=_blank_to_none(subtitle),
        badge_label=_blank_to_none(badge_label),
        badge_variant=badge_variant,
        badge_icon=_blank_to_none(badge_icon),
        sort_order=sort_order,
    )
    if file is not None:
        # save_image, not save_named_image: that one re-encodes as JPEG, and JPEG
        # ringing around the hard black/white edges of a QR is exactly what makes a
        # small printed code fail to scan. The original PNG is stored as uploaded.
        qr.qr_image = await save_image(file, "qr")
    stamp_updated_by(qr, current_user)
    db.add(qr)
    db.commit()
    db.refresh(qr)
    return qr


@router.put("/{qr_id}", response_model=QrCodeOut)
def update_qr_code(
    qr_id: int, payload: QrCodeUpdate, current_user: User = _admin, db: Session = Depends(get_db)
):
    qr = _get(db, qr_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(qr, field, value)
    stamp_updated_by(qr, current_user)
    db.commit()
    db.refresh(qr)
    return qr


@router.post("/{qr_id}/image", response_model=QrCodeOut)
async def upload_qr_image(
    qr_id: int, file: UploadFile, current_user: User = _admin, db: Session = Depends(get_db)
):
    qr = _get(db, qr_id)
    qr.qr_image = await save_image(file, "qr")
    stamp_updated_by(qr, current_user)
    db.commit()
    db.refresh(qr)
    return qr


@router.delete("/{qr_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_qr_code(qr_id: int, _: User = _admin, db: Session = Depends(get_db)):
    qr = _get(db, qr_id)
    db.delete(qr)
    db.commit()
    return None
