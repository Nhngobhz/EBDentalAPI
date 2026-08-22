"""
Report tooling for the admin Reports screen.

Nothing here reads or writes the database - these endpoints take a file the staff member
already has and hand back a tidier version of it. The work itself lives in
app/services/, this module is only the HTTP edge: the upload limits, the permission
gate, and turning a service-level "that file is wrong" into a 400 the Flask side can
flash at the person who picked it.

Gated on price_listing OR admin, the same pair the admin Orders screen uses (see the
sidebar in the EB Web Project): a merchant transaction report is money, so whoever may
look at orders and prices may run one, and the owner - who holds `admin` - always can.
"""
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status

from app.config import settings
from app.core.deps import require_any_permission
from app.models import User
from app.services.aba_report import AbaReportError, refine_aba_report

router = APIRouter(prefix="/reports", tags=["Reports"])

_reporter = Depends(require_any_permission("price_listing", "admin"))

# ABA's own export is well under a megabyte even for a busy month; the PDF ceiling is
# reused rather than adding a setting of its own, purely as a "somebody uploaded a video"
# backstop.
_MAX_UPLOAD_MB = settings.MAX_PDF_SIZE_MB

# Excel's own type, plus the two generic ones browsers fall back to when Windows has no
# handler registered for .xlsx. The real check is that the bytes parse as a workbook
# (refine_aba_report), which is why this list can afford to be forgiving - and why a
# request that declares no type at all is let through rather than rejected.
_ALLOWED_UPLOAD_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
    "application/zip",
}


@router.post("/aba", response_class=Response)
async def aba_transaction_report(
    file: UploadFile = File(...),
    current_user: User = _reporter,
):
    """Raw ABA merchant transaction export (.xlsx) in, refined PDF out.

    Streams straight back rather than being stored anywhere: the source spreadsheet is
    already in the staff member's downloads and the PDF is reproducible from it, so
    there is nothing here worth keeping (and nothing to clean up later)."""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload the .xlsx file ABA gives you.",
        )
    if file.content_type and file.content_type not in _ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload the .xlsx file ABA gives you.",
        )

    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {_MAX_UPLOAD_MB} MB.",
        )

    try:
        pdf, download_name = refine_aba_report(contents, file.filename)
    except AbaReportError as exc:
        # AbaReportError's message is written for the person who picked the file, so it
        # goes back verbatim - see the class docstring in services/aba_report.py.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )
