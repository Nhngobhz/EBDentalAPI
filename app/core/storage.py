"""
Object storage backend for uploaded files: Cloudflare R2 (S3-compatible),
with a local-disk fallback when R2 isn't configured.

If R2_ACCESS_KEY_ID (and friends) aren't set, save_object() writes to disk
under UPLOAD_DIR instead - the same dry-run-style fallback app/core/email.py
uses for SMTP, so uploads still work locally/in tests without real
credentials.
"""
import os

import boto3
from botocore.client import Config

from app.config import settings

_client = None


def _r2_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
    return _client


def save_object(key: str, data: bytes, content_type: str) -> str:
    """Persist `data` under `key` (e.g. "products/widget.jpg") and return
    the URL/path to store on the record: a full R2 URL if R2 is configured,
    otherwise a local /static/... path."""
    if settings.r2_configured:
        _r2_client().put_object(
            Bucket=settings.R2_BUCKET_NAME,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{key}"

    full_path = os.path.join(settings.UPLOAD_DIR, key)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(data)
    return "/" + full_path.replace(os.sep, "/")
