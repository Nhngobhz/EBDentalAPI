"""
Site-wide settings: what the Settings screen in the admin panel reads and writes.

Three levels of access, deliberately:

  GET  /settings/public   no auth   - the handful of values the storefront needs to
                                      render for a signed-out visitor (footer, contact
                                      page, printed quote letterhead)
  GET  /settings/         admin     - every value, plus the form spec and the
                                      read-only integration status panel
  PUT  /settings/         admin     - save a partial dict of values
  POST /settings/image/{key} admin  - upload the picture for an `image`-typed setting
  POST /settings/reset    admin     - put keys (or a whole group) back on their defaults

The public endpoint exists because the Flask app calls this API with whatever token the
visitor's session holds - and an anonymous visitor has none, while still needing the
footer's phone number. It only ever serves keys the spec marks `public`.
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import settings as env_settings
from app.core import settings_spec
from app.core.deps import require_permission
from app.core.files import save_image
from app.core.settings_spec import SettingError
from app.database import get_db
from app.models import User
from app.schemas import SettingsReset, SettingsUpdate
from app.services import app_settings

router = APIRouter(prefix="/settings", tags=["Settings"])

_admin = Depends(require_permission("admin"))


def _integration_status() -> dict:
    """Read-only view of the credential-backed integrations.

    Deliberately booleans and never values: these come from the environment (see
    app/config.py) and stay there. The Settings screen shows an admin *whether* KHQR or
    Telegram is wired up - which is the question they actually have - without putting a
    live API key on a web page, and without creating a second place a credential could
    be changed from.
    """
    return {
        "environment": env_settings.ENVIRONMENT,
        "debug": env_settings.DEBUG,
        "khqr": {
            "provider": env_settings.qr_provider or None,
            "configured": env_settings.khqr_configured,
            "payway_configured": env_settings.payway_configured,
            "bakong_ready": bool(
                env_settings.BAKONG_ACCOUNT_ID or env_settings.KHQR_STATIC_TEMPLATE
            ),
            # Automatic payment confirmation needs one of these; without them staff
            # confirm by hand with "Mark as Paid".
            "auto_confirm": bool(
                env_settings.payway_configured or env_settings.BAKONG_API_TOKEN
            ),
            # Merchant name/city and the QR expiry moved into the editable KHQR
            # Payments group, so they are no longer echoed here - a read-only copy of a
            # field the same page lets you edit is just something else to keep in step.
        },
        "telegram": {
            "configured": bool(
                env_settings.TELEGRAM_BOT_TOKEN and env_settings.TELEGRAM_CHAT_ID
            ),
            "webhook_secret_set": bool(env_settings.TELEGRAM_WEBHOOK_SECRET),
        },
        "email": {
            # An empty MAIL_USERNAME puts outbound mail in dry-run mode: messages are
            # logged, not sent. Worth surfacing - it looks identical to "working" from
            # the admin side otherwise.
            "configured": bool(env_settings.MAIL_USERNAME),
            "from_address": env_settings.MAIL_FROM,
            "server": env_settings.MAIL_SERVER,
        },
        "storage": {
            "r2_configured": env_settings.r2_configured,
            "mode": "Cloudflare R2" if env_settings.r2_configured else "Local disk",
            "max_image_mb": env_settings.MAX_IMAGE_SIZE_MB,
            "max_pdf_mb": env_settings.MAX_PDF_SIZE_MB,
            "max_video_mb": env_settings.MAX_VIDEO_SIZE_MB,
        },
        "google_signin": {"configured": env_settings.google_auth_configured},
        "session": {
            "token_expire_minutes": env_settings.ACCESS_TOKEN_EXPIRE_MINUTES,
            "customer_token_expire_minutes": env_settings.CUSTOMER_TOKEN_EXPIRE_MINUTES,
        },
    }


@router.get("/public")
def read_public_settings(db: Session = Depends(get_db)):
    """Unauthenticated. Only the keys the spec marks public - the storefront shell needs
    these before anyone signs in."""
    return app_settings.get_public(db)


@router.get("/")
def read_settings(_: User = _admin, db: Session = Depends(get_db)):
    return {
        "values": app_settings.get_all(db),
        "defaults": settings_spec.DEFAULTS,
        "groups": settings_spec.describe(),
        "status": _integration_status(),
    }


@router.put("/")
def update_settings(
    payload: SettingsUpdate, current_user: User = _admin, db: Session = Depends(get_db)
):
    try:
        values = app_settings.save(db, payload.values, user_id=current_user.id)
    except SettingError as exc:
        # 400 with the spec's own message, which names the field in the admin's words
        # ("Quote validity (days) must be at least 1") rather than by key.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"values": values}


@router.post("/image/{key}")
async def upload_setting_image(
    key: str,
    file: UploadFile,
    current_user: User = _admin,
    db: Session = Depends(get_db),
):
    """Replace the picture behind one `image`-typed setting (today: the quotation's
    payment QR).

    The value itself is still an ordinary setting - a string holding the stored URL -
    so nothing else in this module has to know a file was involved. What can't go
    through PUT is the upload, since these values are never typed.

    save_image, deliberately, and not save_named_image: that one re-encodes as JPEG,
    and JPEG ringing around a QR's hard black/white edges is exactly what makes a small
    printed code fail to scan. Same reasoning as POST /qr-codes/{id}/image.
    """
    spec = settings_spec.SETTINGS.get(key)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown setting '{key}'"
        )
    if spec.type != "image":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{key}' is not a picture setting",
        )

    url = await save_image(file, "settings")
    values = app_settings.save(db, {key: url}, user_id=current_user.id)
    return {"value": url, "values": values}


@router.post("/reset")
def reset_settings(
    payload: SettingsReset, current_user: User = _admin, db: Session = Depends(get_db)
):
    keys = list(payload.keys or [])
    if payload.group:
        group = next((g for g in settings_spec.GROUPS if g.id == payload.group), None)
        if group is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown settings group '{payload.group}'",
            )
        keys.extend(s.key for s in group.settings)
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to reset - send either `group` or `keys`",
        )
    try:
        values = app_settings.reset(db, keys, user_id=current_user.id)
    except SettingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"values": values}
