"""Receipt OCR and line-item parsing (optional enhancement for itemized expenses).

Pipeline
--------
upload → validate (receipt_upload) → preprocess → Tesseract OCR → parse text
→ validation hints → JSON to browser → **user reviews form** → expense create

OCR never writes to the database. ``scan_receipt_image`` only returns suggestions.

Uses Tesseract locally when installed:
  macOS:   brew install tesseract
  Ubuntu:  sudo apt install tesseract-ocr

Set TESSERACT_CMD in .env if the binary is not on PATH (Windows typical).
"""

from __future__ import annotations

import io
import re
import shutil
from dataclasses import dataclass, field

# Max pixels fed to Tesseract (downscale larger photos first).
MAX_OCR_PIXELS = 16_000_000
# Upscale narrow phone photos — small text fails OCR without this.
MIN_OCR_WIDTH = 1200


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
    requires_review: bool = True
    suggested_total: float | None = None
    items_subtotal: float | None = None
    validation_warning: str = ""

    def to_dict(self) -> dict:
        payload = {
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
            "requires_review": self.requires_review,
            "validation_warning": self.validation_warning,
        }
        if self.suggested_total is not None:
            payload["suggested_total"] = round(self.suggested_total, 2)
        if self.items_subtotal is not None:
            payload["items_subtotal"] = round(self.items_subtotal, 2)
        return payload


SKIP_LINE_PATTERN = re.compile(
    r"\b(sub\s*total|subtotal|total|tax|vat|gst|tip|gratuity|service\s*charge|"
    r"change|balance\s*due|amount\s*due|cash|card|visa|mastercard|approved|"
    r"thank\s*you|merchant|receipt|invoice|date|time|tel|phone|www\.)\b",
    re.I,
)

# Decimal prices at line end, optional currency prefix.
PRICE_AT_END = re.compile(
    r"(?:Rs\.?|NPR|रू|\$|USD|€|EUR)?\s*(\d{1,6}[.,]\d{2})\s*$",
    re.I,
)

# Whole-rupee amounts (common on Nepali receipts), e.g. "Dal Bhat 180".
PRICE_WHOLE_AT_END = re.compile(
    r"(?:Rs\.?|NPR|रू)?\s*(\d{2,6})\s*$",
    re.I,
)

QTY_PREFIX = re.compile(
    r"^(\d+(?:\.\d+)?)\s*[x×@]\s*(.+)$",
    re.I,
)

TOTAL_LINE_PATTERN = re.compile(
    r"\b(?:grand\s*)?total\b[^0-9]{0,20}"
    r"(?:Rs\.?|NPR|रू|\$)?\s*(\d{1,6}(?:[.,]\d{2})?)",
    re.I,
)

MIN_NAME_LEN = 2
MAX_NAME_LEN = 120
MAX_PARSED_ITEMS = 80


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


def preprocess_for_ocr(image) -> object:
    """Prepare a receipt photo for Tesseract.

    Receipts are mostly dark text on light paper — grayscale + contrast
    helps more than colour. We also normalize size: phone thumbnails are
    too small for reliable OCR; huge photos are downscaled for speed.
    """
    from PIL import Image, ImageOps

    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")

    if image.mode == "RGB":
        image = image.convert("L")

    width, height = image.size
    pixels = width * height

    if pixels > MAX_OCR_PIXELS:
        scale = (MAX_OCR_PIXELS / pixels) ** 0.5
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )
        width, height = image.size

    if width < MIN_OCR_WIDTH:
        scale = MIN_OCR_WIDTH / width
        new_w = MIN_OCR_WIDTH
        new_h = max(1, int(height * scale))
        if new_w * new_h <= MAX_OCR_PIXELS:
            image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # cutoff=2 ignores extreme outliers from shadows/crumple folds.
    return ImageOps.autocontrast(image, cutoff=2)


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

    with Image.open(io.BytesIO(image_bytes)) as image:
        prepared = preprocess_for_ocr(image.copy())
        # psm 6 = single uniform block of text (typical receipt column).
        config = "--psm 6"
        return pytesseract.image_to_string(prepared, config=config) or ""


def _parse_price(raw: str) -> float | None:
    normalized = raw.replace(",", ".")
    try:
        value = float(normalized)
    except ValueError:
        return None
    if value <= 0 or value > 999_999:
        return None
    return round(value, 2)


def _extract_line_price(name_part: str) -> tuple[float | None, int | None]:
    """Return (price, match_start_index) from the end of a line."""
    match = PRICE_AT_END.search(name_part)
    if match:
        return _parse_price(match.group(1)), match.start()

    whole = PRICE_WHOLE_AT_END.search(name_part)
    if whole:
        return _parse_price(whole.group(1)), whole.start()
    return None, None


def parse_line_items_from_text(text: str) -> tuple[list[ParsedReceiptItem], float]:
    """Parse OCR text into line items. Returns (items, confidence_score 0-1)."""
    items: list[ParsedReceiptItem] = []
    seen_names: set[str] = set()

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
            if quantity <= 0 or quantity > 999:
                quantity = 1.0
            name_part = qty_match.group(2).strip()

        price, price_start = _extract_line_price(name_part)
        if price is None or price_start is None:
            continue

        name = name_part[:price_start].strip(" .-\t")
        name = re.sub(r"^[\d.]+\s+", "", name)
        if len(name) < MIN_NAME_LEN or len(name) > MAX_NAME_LEN:
            continue
        if SKIP_LINE_PATTERN.search(name):
            continue

        key = name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)

        items.append(ParsedReceiptItem(name=name, price=price, quantity=quantity))
        if len(items) >= MAX_PARSED_ITEMS:
            break

    if len(items) >= 3:
        confidence = 0.9
    elif len(items) == 2:
        confidence = 0.75
    elif len(items) == 1:
        confidence = 0.45
    else:
        confidence = 0.0

    return items, confidence


def extract_total_from_text(text: str) -> float | None:
    """Best-effort receipt total for cross-checking parsed line items."""
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line or "subtotal" in line.lower():
            continue
        match = TOTAL_LINE_PATTERN.search(line)
        if match:
            total = _parse_price(match.group(1))
            if total is not None:
                return total
    return None


def guess_merchant_from_text(text: str) -> str:
    """Best-effort venue name from the top of receipt OCR text."""
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) < 3 or len(line) > 64:
            continue
        if SKIP_LINE_PATTERN.search(line):
            continue
        if PRICE_AT_END.search(line) or PRICE_WHOLE_AT_END.search(line):
            continue
        if re.match(r"^[\d\s\-/:.]+$", line):
            continue
        return line[:120]
    return ""


def _items_subtotal(items: list[ParsedReceiptItem]) -> float:
    return round(sum(item.price * item.quantity for item in items), 2)


def _total_mismatch_warning(
    items: list[ParsedReceiptItem],
    suggested_total: float | None,
) -> str:
    if suggested_total is None or not items:
        return ""
    subtotal = _items_subtotal(items)
    if subtotal <= 0:
        return ""
    diff = abs(subtotal - suggested_total)
    if diff <= max(0.02, suggested_total * 0.05):
        return ""
    return (
        f"Line items add up to {subtotal:.2f} but the receipt total looks like "
        f"{suggested_total:.2f}. Adjust items before saving."
    )


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
            requires_review=False,
        )

    try:
        text = run_ocr_on_image(image_bytes, tesseract_cmd=tesseract_cmd)
    except RuntimeError as exc:
        return ReceiptScanResult(
            success=False,
            confidence="none",
            message=str(exc),
            requires_review=False,
        )
    except Exception:
        return ReceiptScanResult(
            success=False,
            confidence="none",
            message="Could not read receipt image. Try a clearer photo or add items manually.",
            requires_review=False,
        )

    if not text.strip():
        return ReceiptScanResult(
            success=False,
            confidence="none",
            message="No text detected on the receipt. Enter items manually.",
            requires_review=False,
        )

    items, score = parse_line_items_from_text(text)
    merchant = guess_merchant_from_text(text)
    suggested_total = extract_total_from_text(text)
    subtotal = _items_subtotal(items) if items else None
    validation_warning = _total_mismatch_warning(items, suggested_total)

    if not items:
        return ReceiptScanResult(
            success=False,
            confidence="none",
            message=(
                "Could not detect line items with prices. "
                "Try a clearer photo or tap + Add item."
            ),
            suggested_merchant=merchant,
            suggested_total=suggested_total,
            requires_review=False,
        )

    review_note = " Review every item and price before saving."

    if score >= min_high_confidence:
        level = "high"
        message = (
            f"Found {len(items)} item(s). Check the list below, then save.{review_note}"
        )
        return ReceiptScanResult(
            success=True,
            confidence=level,
            items=items,
            message=message,
            suggested_merchant=merchant,
            requires_review=True,
            suggested_total=suggested_total,
            items_subtotal=subtotal,
            validation_warning=validation_warning,
        )

    if score >= min_low_confidence:
        level = "low"
        message = (
            f"Found {len(items)} item(s) with low confidence.{review_note}"
        )
        return ReceiptScanResult(
            success=False,
            confidence=level,
            items=items,
            message=message,
            suggested_merchant=merchant,
            requires_review=True,
            suggested_total=suggested_total,
            items_subtotal=subtotal,
            validation_warning=validation_warning,
        )

    return ReceiptScanResult(
        success=False,
        confidence="none",
        items=items,
        message=(
            "Could not read items reliably. Fix the list below or try another photo."
            + review_note
        ),
        suggested_merchant=merchant,
        requires_review=True,
        suggested_total=suggested_total,
        items_subtotal=subtotal,
        validation_warning=validation_warning,
    )
