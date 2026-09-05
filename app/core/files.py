"""
Shared helper for handling image / video / PDF uploads.

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
import re
import uuid

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.core.storage import save_object

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_PDF_TYPES = {"application/pdf"}

# Product-gallery videos. Deliberately just the two containers every browser can play
# from a plain <video> tag with no plugin and no transcoding step: nothing in this
# stack can re-encode, so anything accepted here is served back byte-for-byte and
# either plays or doesn't.
#
# QuickTime (.mov, what an iPhone records) is NOT in the list even though it usually
# holds ordinary H.264. Its playability depends on the codecs inside the container
# rather than on the container, so accepting it would mean storing files that upload
# cleanly, cost 60 MB and then show a black box on half the machines in the clinic.
# Rejecting it produces an explicit "Unsupported file type" the admin can act on -
# export/convert to MP4 - which is the better of the two failures.
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm"}

# The extension a stored file gets, chosen from the content type we already
# validated rather than from the client-supplied filename.
#
# This matters because of the local-disk fallback (app/core/storage.py): those
# files are served back by StaticFiles, which picks the response Content-Type
# purely from the extension on disk. Taking the extension from `file.filename`
# meant an uploader could store active content - "payload.html", "payload.svg" -
# under an image/* content type and get it served as HTML from this API's own
# origin. Mapping the extension from the validated type instead makes that
# impossible without changing anything about legitimate uploads.
_EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}

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

    ext = _EXTENSION_BY_CONTENT_TYPE.get(file.content_type, ".bin")
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


async def save_video(file: UploadFile, category: str) -> str:
    """A gallery video, stored exactly as uploaded under a uuid filename.

    No `save_named_video` counterpart on purpose. The named variant exists so that
    re-uploading a picture for an item overwrites the old one instead of orphaning it,
    which only works because one item has exactly one primary image; a product can
    carry several clips, so a name-derived key would make the second upload silently
    replace the first. Same reasoning as the gallery photos - see save_image's use in
    upload_product_gallery_images.

    Nothing re-encodes or even inspects the contents: `save_named_image` can lean on
    Pillow to prove a JPEG is really a JPEG, and there is no equivalent here without
    pulling in ffmpeg. The content-type check plus the extension mapping in
    save_upload is what keeps an upload from being served back as active content."""
    return await save_upload(file, category, ALLOWED_VIDEO_TYPES, settings.MAX_VIDEO_SIZE_MB)


async def save_pdf(file: UploadFile, category: str) -> str:
    return await save_upload(file, category, ALLOWED_PDF_TYPES, settings.MAX_PDF_SIZE_MB)
