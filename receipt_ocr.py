"""Receipt OCR and line-item parsing (optional enhancement for itemized expenses).

Uses Tesseract locally when installed:
  macOS:   brew install tesseract
  Ubuntu:  sudo apt install tesseract-ocr

Set TESSERACT_CMD in .env if the binary is not on PATH (Windows typical).
For production you could swap ``run_ocr_on_image`` with a cloud API; keep
``parse_line_items_from_text`` as the shared parsing layer.
"""

from __future__ import annotations

import io
import re
import shutil
from dataclasses import dataclass, field


@dataclass
class ParsedReceiptItem:
    name: str
    price: float
    quantity: float = 1.0


@dataclass
class ReceiptScanResult:
    success: bool
    confidence: str  # high | low | none
    items: list[ParsedReceiptItem] = field(default_factory=list)
    message: str = ""
    ocr_available: bool = True
    suggested_merchant: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "confidence": self.confidence,
            "items": [
                {
                    "name": item.name,
                    "price": round(item.price, 2),
                    "quantity": round(item.quantity, 2),
                }
                for item in self.items
            ],
            "message": self.message,
            "ocr_available": self.ocr_available,
            "suggested_merchant": self.suggested_merchant,
        }


SKIP_LINE_PATTERN = re.compile(
    r"\b(sub\s*total|subtotal|total|tax|vat|gst|tip|gratuity|service\s*charge|"
    r"change|balance\s*due|amount\s*due|cash|card|visa|mastercard|approved|"
    r"thank\s*you|merchant|receipt|invoice|date|time|tel|phone|www\.)\b",
    re.I,
)

PRICE_AT_END = re.compile(
    r"(?:Rs\.?|NPR|\$|USD|€|EUR)?\s*(\d{1,5}[.,]\d{2})\s*$",
    re.I,
)

QTY_PREFIX = re.compile(
    r"^(\d+(?:\.\d+)?)\s*[x×@]\s*(.+)$",
    re.I,
)

MIN_NAME_LEN = 2
MAX_NAME_LEN = 120


def _python_ocr_imports_ok() -> bool:
    try:
        import PIL  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def tesseract_is_available(tesseract_cmd: str | None = None) -> bool:
    if not _python_ocr_imports_ok():
        return False
    if tesseract_cmd:
        return bool(shutil.which(tesseract_cmd) or tesseract_cmd)
    return shutil.which("tesseract") is not None


def run_ocr_on_image(image_bytes: bytes, tesseract_cmd: str | None = None) -> str:
    """Return raw OCR text from image bytes."""
    try:
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        raise RuntimeError(
            "OCR dependencies missing. Install: pip install Pillow pytesseract"
        ) from exc

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    if not tesseract_is_available(tesseract_cmd):
        raise RuntimeError(
            "Tesseract is not installed. Install the tesseract binary "
            "(see receipt_ocr.py module docstring)."
        )

    image = Image.open(io.BytesIO(image_bytes))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    return pytesseract.image_to_string(image) or ""


def _parse_price(raw: str) -> float | None:
    normalized = raw.replace(",", ".")
    try:
        value = float(normalized)
    except ValueError:
        return None
    if value <= 0 or value > 999_999:
        return None
    return round(value, 2)


def parse_line_items_from_text(text: str) -> tuple[list[ParsedReceiptItem], float]:
    """Parse OCR text into line items. Returns (items, confidence_score 0-1)."""
    items: list[ParsedReceiptItem] = []

    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if len(line) < 4:
            continue
        if SKIP_LINE_PATTERN.search(line):
            continue

        quantity = 1.0
        name_part = line

        qty_match = QTY_PREFIX.match(line)
        if qty_match:
            try:
                quantity = float(qty_match.group(1))
            except ValueError:
                quantity = 1.0
            name_part = qty_match.group(2).strip()

        price_match = PRICE_AT_END.search(name_part)
        if not price_match:
            continue

        price = _parse_price(price_match.group(1))
        if price is None:
            continue

        name = name_part[: price_match.start()].strip(" .-\t")
        name = re.sub(r"^[\d.]+\s+", "", name)
        if len(name) < MIN_NAME_LEN or len(name) > MAX_NAME_LEN:
            continue
        if SKIP_LINE_PATTERN.search(name):
            continue

        items.append(ParsedReceiptItem(name=name, price=price, quantity=quantity))

    if len(items) >= 3:
        confidence = 0.9
    elif len(items) == 2:
        confidence = 0.75
    elif len(items) == 1:
        confidence = 0.45
    else:
        confidence = 0.0

    return items, confidence


def guess_merchant_from_text(text: str) -> str:
    """Best-effort venue name from the top of receipt OCR text."""
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) < 3 or len(line) > 64:
            continue
        if SKIP_LINE_PATTERN.search(line):
            continue
        if PRICE_AT_END.search(line):
            continue
        if re.match(r"^[\d\s\-/:.]+$", line):
            continue
        return line[:120]
    return ""


def scan_receipt_image(
    image_bytes: bytes,
    *,
    tesseract_cmd: str | None = None,
    min_high_confidence: float = 0.7,
    min_low_confidence: float = 0.4,
) -> ReceiptScanResult:
    """Run OCR and parse items; never raises — returns graceful fallback result."""
    if not tesseract_is_available(tesseract_cmd):
        return ReceiptScanResult(
            success=False,
            confidence="none",
            message=(
                "Receipt scanning is unavailable (Tesseract not installed). "
                "Add items manually or install Tesseract."
            ),
            ocr_available=False,
        )

    try:
        text = run_ocr_on_image(image_bytes, tesseract_cmd=tesseract_cmd)
    except Exception as exc:
        return ReceiptScanResult(
            success=False,
            confidence="none",
            message=f"Could not read receipt image: {exc}",
        )

    if not text.strip():
        return ReceiptScanResult(
            success=False,
            confidence="none",
            message="No text detected on the receipt. Enter items manually.",
        )

    items, score = parse_line_items_from_text(text)
    merchant = guess_merchant_from_text(text)
    if not items:
        return ReceiptScanResult(
            success=False,
            confidence="none",
            message=(
                "Could not detect line items with prices. "
                "Try a clearer photo or tap + Add item."
            ),
            suggested_merchant=merchant,
        )

    if score >= min_high_confidence:
        level = "high"
        message = f"Found {len(items)} item(s). Send links — each person taps what they ate."
    elif score >= min_low_confidence:
        level = "low"
        message = (
            f"Found {len(items)} item(s). Please review prices, then share guest links."
        )
    else:
        return ReceiptScanResult(
            success=False,
            confidence="none",
            items=items,
            message=(
                "Could not read items reliably. Fix the list below or try another photo."
            ),
            suggested_merchant=merchant,
        )

    return ReceiptScanResult(
        success=True,
        confidence=level,
        items=items,
        message=message,
        suggested_merchant=merchant,
    )
