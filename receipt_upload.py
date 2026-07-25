"""Save and validate expense receipt image uploads."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

if TYPE_CHECKING:
    from flask import Flask

ALLOWED_RECEIPT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ALLOWED_RECEIPT_MIMETYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


def receipt_upload_dir(app: Flask) -> str:
    """Absolute filesystem path for receipt files (under Flask static_folder)."""
    subdir = app.config.get("RECEIPT_UPLOAD_SUBDIR", "receipts")
    return os.path.join(app.root_path, app.static_folder, subdir)


def validate_receipt_file(file: FileStorage, max_bytes: int) -> None:
    if not file or not file.filename:
        return

    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > max_bytes:
        raise ValueError(
            f"Receipt image must be {max_bytes // (1024 * 1024)}MB or smaller."
        )

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_RECEIPT_EXTENSIONS:
        raise ValueError("Receipt must be an image (JPEG, PNG, GIF, or WebP).")

    mime = (file.mimetype or "").split(";")[0].strip().lower()
    if mime and mime not in ALLOWED_RECEIPT_MIMETYPES:
        raise ValueError("Receipt must be an image (JPEG, PNG, GIF, or WebP).")


def save_receipt_file(app: Flask, file: FileStorage) -> str | None:
    """Validate, store under static/receipts, return path relative to static folder."""
    if not file or not file.filename:
        return None

    max_bytes = app.config.get("RECEIPT_MAX_BYTES", 5 * 1024 * 1024)
    validate_receipt_file(file, max_bytes)

    upload_dir = receipt_upload_dir(app)
    os.makedirs(upload_dir, exist_ok=True)

    safe_name = secure_filename(file.filename)
    ext = os.path.splitext(safe_name)[1].lower() or ".jpg"
    stored_name = f"{uuid.uuid4().hex}{ext}"
    absolute_path = os.path.join(upload_dir, stored_name)
    file.save(absolute_path)

    subdir = app.config.get("RECEIPT_UPLOAD_SUBDIR", "receipts")
    return f"{subdir}/{stored_name}"
