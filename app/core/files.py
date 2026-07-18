"""
Shared helper for handling image/PDF uploads.

Every *_image / pdf field in the schema is stored as a plain string - a
Cloudflare R2 URL such as https://pub-xxxx.r2.dev/products/<uuid>.jpg (or a
local /static/uploads/... path when R2 isn't configured, see
app/core/storage.py). You can either:
  (a) PUT/POST the JSON field directly with a URL you already host
      elsewhere, or
  (b) use the dedicated `POST .../{id}/image` (or `/pdf`) upload endpoint,
      which uploads the file to storage under <category>/ and stores the
      resulting URL/path for you.
"""
import io
import os
import re
import uuid

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.core.storage import save_object

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_PDF_TYPES = {"application/pdf"}

# Longest side an uploaded image is downscaled to before saving, to keep
# compressed file sizes down regardless of what the customer/staff uploads.
IMAGE_MAX_DIMENSION = 1600
IMAGE_JPEG_QUALITY = 82

_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


async def save_upload(
    file: UploadFile,
    category: str,
    allowed_types: set[str],
    max_size_mb: int,
) -> str:
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{file.content_type}'. Allowed: {sorted(allowed_types)}",
        )

    contents = await file.read()
    max_bytes = max_size_mb * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {max_size_mb} MB.",
        )

    ext = os.path.splitext(file.filename or "")[1].lower() or ".bin"
    filename = f"{uuid.uuid4().hex}{ext}"
    key = f"{category}/{filename}"

    return save_object(key, contents, file.content_type)


async def save_image(file: UploadFile, category: str) -> str:
    return await save_upload(
        file, category, ALLOWED_IMAGE_TYPES, settings.MAX_IMAGE_SIZE_MB
    )


async def save_named_image(file: UploadFile, category: str, name: str) -> str:
    """Like `save_image`, but re-compresses the upload as a JPEG and names
    it after `name` instead of a random uuid, e.g. "Widget 3000 image.JPEG".
    Re-saving under a fixed, human-readable name also means re-uploading a
    picture for the same item overwrites its old picture instead of
    accumulating orphans on disk."""
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '{file.content_type}'. Allowed: {sorted(ALLOWED_IMAGE_TYPES)}",
        )

    contents = await file.read()
    max_bytes = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {settings.MAX_IMAGE_SIZE_MB} MB.",
        )

    try:
        image = Image.open(io.BytesIO(contents))
        image.load()
    except UnidentifiedImageError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is not a valid image"
        )

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.thumbnail((IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION), Image.LANCZOS)

    safe_name = _UNSAFE_FILENAME_CHARS.sub("-", name).strip() or "untitled"
    filename = f"{safe_name} image.JPEG"
    key = f"{category}/{filename}"

    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True)

    return save_object(key, buffer.getvalue(), "image/jpeg")


async def save_pdf(file: UploadFile, category: str) -> str:
    return await save_upload(file, category, ALLOWED_PDF_TYPES, settings.MAX_PDF_SIZE_MB)
