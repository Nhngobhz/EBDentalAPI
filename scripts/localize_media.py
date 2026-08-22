"""
Move every uploaded file off Cloudflare R2 and onto this machine's disk.

Written for the move to a self-hosted Windows server, where the whole system
(Postgres, this API, the Flask site and the media) lives on one box and there
is no bucket to reach out to. It does two separate jobs, in this order:

  1. MIRROR - copy every object in the R2 bucket down into UPLOAD_DIR, keeping
     its key as the relative path ("products/Widget image.JPEG" ->
     static/uploads/products/Widget image.JPEG). The WHOLE bucket, not just the
     files some row currently points at: an image nobody references today is
     still the only copy that exists, and once the bucket is gone it is gone.
  2. REWRITE - repoint every media column in the database at the local copy,
     turning "https://pub-xxxx.r2.dev/products/x.JPEG" into
     "/static/uploads/products/x.JPEG". That is exactly the shape
     app/core/storage.py already writes when R2 is unconfigured, and that
     app/main.py's StaticFiles mount already serves, so nothing downstream has
     to learn a new format - the Flask site's resolve_image_url() turns it back
     into an absolute URL against STORE_API_BASE_URL on its own.

Files hosted somewhere else entirely (the stock photos the hero slides shipped
with, say) are downloaded over plain HTTP into the same tree, so "everything is
local" really means everything.

Usage, from the project root with the virtualenv active:

    python -m scripts.localize_media --dry-run    # report, change nothing
    python -m scripts.localize_media              # do it

Safe to re-run: an object already on disk with the same byte count is left
alone, and a column already holding a local path is skipped.

AFTER running this, clear R2_ACCESS_KEY_ID in .env so new uploads are written
to disk too - otherwise the next upload puts a fresh file back in the bucket
and the database grows new R2 URLs behind you.
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import unquote, urlsplit

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models import (
    Brand,
    Category,
    Customer,
    HeroSlide,
    Manual,
    Product,
    ProductImage,
    Promotion,
    QrCode,
    Set,
    User,
)

# (model, column, fallback folder for files that came from somewhere other than
# our own bucket and so have no key of their own).
MEDIA_COLUMNS = [
    (Brand, "brand_image", "brands"),
    (Category, "category_image", "categories"),
    (Product, "product_image", "products"),
    (ProductImage, "image", "products"),
    (Manual, "manual_image", "manuals"),
    (Manual, "pdf", "manuals"),
    (Promotion, "promotion_image", "promotions"),
    (Set, "set_image", "sets"),
    (Set, "detail_image", "sets"),
    (User, "user_image", "users"),
    (Customer, "customer_image", "customers"),
    (QrCode, "qr_image", "qr"),
    (HeroSlide, "slide_image", "hero"),
]


def _upload_root() -> str:
    return settings.UPLOAD_DIR.replace("\\", "/").strip("/")


def local_path_for(key: str) -> str:
    """Bucket key -> the path stored on the record. Always forward slashes:
    this string ends up in a URL, not on a Windows command line."""
    return f"/{_upload_root()}/{key}"


def disk_path_for(key: str) -> str:
    return os.path.join(settings.UPLOAD_DIR, *key.split("/"))


# ---------------------------------------------------------------------------
# 1. Mirror the bucket
# ---------------------------------------------------------------------------
def mirror_bucket(dry_run: bool) -> tuple[int, int, int]:
    """Returns (downloaded, already_present, failed)."""
    if not settings.r2_configured:
        print("R2 is not configured - nothing to mirror.")
        return (0, 0, 0)

    from app.core.storage import _r2_client

    client = _r2_client()
    downloaded = present = failed = 0

    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.R2_BUCKET_NAME):
        for obj in page.get("Contents", []):
            key, size = obj["Key"], obj["Size"]
            if key.endswith("/"):
                continue  # a "folder" marker, not a file
            target = disk_path_for(key)

            # Same name and same byte count is treated as the same file. A
            # checksum would be stricter, but these are write-once uploads -
            # re-uploading a picture writes a new uuid name (or overwrites the
            # named one wholesale, changing its size).
            if os.path.exists(target) and os.path.getsize(target) == size:
                present += 1
                continue

            if dry_run:
                print(f"  would download {key} ({size} bytes)")
                downloaded += 1
                continue

            try:
                body = client.get_object(
                    Bucket=settings.R2_BUCKET_NAME, Key=key
                )["Body"].read()
            except Exception as exc:  # noqa: BLE001 - one bad object must not stop the migration
                print(f"  FAILED {key}: {exc}")
                failed += 1
                continue

            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(body)
            downloaded += 1

    return (downloaded, present, failed)


# ---------------------------------------------------------------------------
# 2. Rewrite the database
# ---------------------------------------------------------------------------
def _key_from_url(url: str) -> str | None:
    """The bucket key for one of OUR R2 URLs, or None if this URL points at
    some other host. Percent-decoded, because a key with a space in it
    ("KP SmileScan image.JPEG") arrives as %20 and has to hit the disk under
    its real name."""
    base = (settings.R2_PUBLIC_BASE_URL or "").rstrip("/")
    if base and url.startswith(base + "/"):
        return unquote(url[len(base) + 1 :])
    return None


def _download_foreign(url: str, folder: str, dry_run: bool) -> str | None:
    """Pull a file hosted on some third-party site into our own tree and return
    its new local path. Keeps the original filename where the URL has a usable
    one, so the file stays recognisable on disk."""
    name = os.path.basename(unquote(urlsplit(url).path)) or "file"
    if "." not in name:
        name += ".jpg"
    key = f"{folder}/{name}"
    target = disk_path_for(key)

    if os.path.exists(target):
        return local_path_for(key)
    if dry_run:
        print(f"  would fetch {url} -> {key}")
        return local_path_for(key)

    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED {url}: {exc}")
        return None

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as handle:
        handle.write(response.content)
    print(f"  fetched {url} -> {key}")
    return local_path_for(key)


def rewrite_columns(db, dry_run: bool) -> tuple[int, int]:
    """Returns (rewritten, missing_on_disk)."""
    rewritten = missing = 0

    for model, column, folder in MEDIA_COLUMNS:
        attribute = getattr(model, column)
        rows = db.query(model).filter(attribute.isnot(None), attribute != "").all()

        for row in rows:
            value = getattr(row, column)
            if not value.startswith(("http://", "https://")):
                continue  # already a local path

            key = _key_from_url(value)
            if key is None:
                new_value = _download_foreign(value, folder, dry_run)
                if new_value is None:
                    continue
            else:
                new_value = local_path_for(key)
                if not os.path.exists(disk_path_for(key)):
                    # The row points at an object the bucket no longer holds.
                    # Left alone on purpose: replacing it with a local path
                    # would turn a working remote URL into a 404.
                    print(
                        f"  MISSING on disk, left pointing at R2: "
                        f"{model.__tablename__}.{column} id={row.id} -> {key}"
                    )
                    missing += 1
                    continue

            if not dry_run:
                setattr(row, column, new_value)
            rewritten += 1

    return (rewritten, missing)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would happen without downloading or writing anything",
    )
    parser.add_argument(
        "--skip-mirror",
        action="store_true",
        help="only rewrite the database, assuming the files are already on disk",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN - nothing will be downloaded or saved.\n")

    if not args.skip_mirror:
        print(f"Mirroring bucket '{settings.R2_BUCKET_NAME}' -> {settings.UPLOAD_DIR}/")
        downloaded, present, failed = mirror_bucket(args.dry_run)
        print(f"  {downloaded} downloaded, {present} already present, {failed} failed\n")
        if failed:
            print("Refusing to rewrite the database while objects are missing.")
            print("Fix the failures above and re-run.")
            sys.exit(1)

    print("Repointing database media columns at the local copies")
    db = SessionLocal()
    try:
        rewritten, missing = rewrite_columns(db, args.dry_run)
        if not args.dry_run:
            db.commit()
    finally:
        db.close()

    print(f"\n  {rewritten} columns rewritten, {missing} left on R2 (file missing)")
    if not args.dry_run:
        print(
            "\nDone. Now clear R2_ACCESS_KEY_ID in .env (and restart the API) so new "
            "uploads are written to disk as well."
        )


if __name__ == "__main__":
    main()
