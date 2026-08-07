"""Save and validate expense receipt image uploads."""

from __future__ import annotations

import io
import os
import uuid
from typing import TYPE_CHECKING

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

if TYPE_CHECKING:
    from flask import Flask

# Extensions we accept after content inspection (not just the filename).
ALLOWED_RECEIPT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ALLOWED_RECEIPT_MIMETYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

MIN_RECEIPT_BYTES = 100
# Guard against decompression bombs and absurdly large scans.
MAX_IMAGE_PIXELS = 20_000_000  # e.g. 4472×4472


class ReceiptValidationError(ValueError):
    """Receipt file failed validation."""


def receipt_upload_dir(app: Flask) -> str:
    """Absolute filesystem path for receipt files (under Flask static_folder)."""
    subdir = app.config.get("RECEIPT_UPLOAD_SUBDIR", "receipts")
    return os.path.join(app.root_path, app.static_folder, subdir)


def _format_size_limit(max_bytes: int) -> str:
    return f"{max_bytes // (1024 * 1024)}MB"


def validate_receipt_file(file: FileStorage, max_bytes: int) -> None:
    """Validate an uploaded receipt before reading or saving it."""
    if not file or not file.filename:
        return

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_RECEIPT_EXTENSIONS:
        raise ReceiptValidationError(
            "Receipt must be an image (JPEG, PNG, GIF, or WebP)."
        )

    mime = (file.mimetype or "").split(";")[0].strip().lower()
    if mime and mime not in ALLOWED_RECEIPT_MIMETYPES:
        raise ReceiptValidationError(
            "Receipt must be an image (JPEG, PNG, GIF, or WebP)."
        )

    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size < MIN_RECEIPT_BYTES:
        raise ReceiptValidationError("Receipt image is empty or too small.")
    if size > max_bytes:
        raise ReceiptValidationError(
            f"Receipt image must be {_format_size_limit(max_bytes)} or smaller."
        )


def validate_receipt_bytes(image_bytes: bytes, max_bytes: int) -> None:
    """Validate raw bytes (size + decodable image) before OCR or storage."""
    if not image_bytes:
        raise ReceiptValidationError("Receipt image is empty.")
    if len(image_bytes) < MIN_RECEIPT_BYTES:
        raise ReceiptValidationError("Receipt image is empty or too small.")
    if len(image_bytes) > max_bytes:
        raise ReceiptValidationError(
            f"Receipt image must be {_format_size_limit(max_bytes)} or smaller."
        )
    _open_and_check_pixels(image_bytes)


def _open_and_check_pixels(image_bytes: bytes):
    """Open with Pillow and enforce pixel limits. Raises ReceiptValidationError."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise ReceiptValidationError(
            "Image processing is unavailable (Pillow not installed)."
        ) from exc

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
            if width <= 0 or height <= 0:
                raise ReceiptValidationError("Receipt image has invalid dimensions.")
            if width * height > MAX_IMAGE_PIXELS:
                raise ReceiptValidationError(
                    "Receipt image is too large. Use a smaller photo."
                )
            if image.format not in ("JPEG", "PNG", "GIF", "WEBP"):
                raise ReceiptValidationError(
                    "Receipt must be a JPEG, PNG, GIF, or WebP image."
                )
    except ReceiptValidationError:
        raise
    except Exception as exc:
        raise ReceiptValidationError(
            "Could not read receipt image. Upload a valid photo file."
        ) from exc


def sanitize_receipt_bytes(image_bytes: bytes) -> bytes:
    """Re-encode image to strip EXIF/metadata and reject polyglot payloads.

    Returns JPEG bytes suitable for OCR and filesystem storage.
    """
    validate_receipt_bytes(image_bytes, max_bytes=len(image_bytes))
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as image:
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=88, optimize=True)
        return buffer.getvalue()


def save_receipt_file(app: Flask, file: FileStorage) -> str | None:
    """Validate, sanitize, store under static/receipts, return relative path."""
    if not file or not file.filename:
        return None

    max_bytes = app.config.get("RECEIPT_MAX_BYTES", 5 * 1024 * 1024)
    validate_receipt_file(file, max_bytes)

    raw = file.read()
    if len(raw) > max_bytes:
        raise ReceiptValidationError(
            f"Receipt image must be {_format_size_limit(max_bytes)} or smaller."
        )
    safe_bytes = sanitize_receipt_bytes(raw)

    upload_dir = receipt_upload_dir(app)
    os.makedirs(upload_dir, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}.jpg"
    absolute_path = os.path.join(upload_dir, stored_name)
    with open(absolute_path, "wb") as handle:
        handle.write(safe_bytes)

    subdir = app.config.get("RECEIPT_UPLOAD_SUBDIR", "receipts")
    return f"{subdir}/{stored_name}"
