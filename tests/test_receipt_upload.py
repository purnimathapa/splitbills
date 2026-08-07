"""Tests for receipt upload validation and sanitization."""

import io
import unittest

from receipt_upload import (
    ReceiptValidationError,
    sanitize_receipt_bytes,
    validate_receipt_bytes,
)


def _jpeg_bytes() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (32, 32), color=(255, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _png_bytes() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (24, 24), color=(0, 128, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ReceiptUploadValidationTests(unittest.TestCase):
    def test_rejects_empty_bytes(self):
        with self.assertRaises(ReceiptValidationError):
            validate_receipt_bytes(b"", max_bytes=1024)

    def test_rejects_garbage_bytes(self):
        with self.assertRaises(ReceiptValidationError):
            validate_receipt_bytes(b"not-an-image-at-all", max_bytes=1024)

    def test_accepts_valid_jpeg(self):
        validate_receipt_bytes(_jpeg_bytes(), max_bytes=1024 * 1024)

    def test_sanitize_returns_jpeg(self):
        raw = _png_bytes()
        cleaned = sanitize_receipt_bytes(raw)
        self.assertTrue(cleaned.startswith(b"\xff\xd8"))
        self.assertGreater(len(cleaned), 100)


if __name__ == "__main__":
    unittest.main()
