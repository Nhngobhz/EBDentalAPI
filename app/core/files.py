"""
Shared helper for handling image/PDF uploads.

Every *_image / pdf field in the schema is stored as a plain string (a
path such as /static/uploads/products/<uuid>.jpg). You can either:
  (a) PUT/POST the JSON field directly with a URL you already host
      elsewhere, or
  (b) use the dedicated `POST .../{id}/image` (or `/pdf`) upload endpoint,
      which saves the file locally under static/uploads/<category>/ and
      stores the resulting path for you.
"""
import os
import uuid

from fastapi import HTTPException, UploadFile, status

from app.config import settings

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_PDF_TYPES = {"application/pdf"}


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

    directory = os.path.join(settings.UPLOAD_DIR, category)
    os.makedirs(directory, exist_ok=True)
    full_path = os.path.join(directory, filename)

    with open(full_path, "wb") as f:
        f.write(contents)

    # Stored/returned as a web-servable path (see static mount in main.py) -
    # always forward slashes, regardless of the OS path separator used above.
    return "/" + full_path.replace(os.sep, "/")


async def save_image(file: UploadFile, category: str) -> str:
    return await save_upload(
        file, category, ALLOWED_IMAGE_TYPES, settings.MAX_IMAGE_SIZE_MB
    )


async def save_pdf(file: UploadFile, category: str) -> str:
    return await save_upload(file, category, ALLOWED_PDF_TYPES, settings.MAX_PDF_SIZE_MB)
