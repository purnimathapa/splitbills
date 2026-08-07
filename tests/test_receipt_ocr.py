"""Tests for receipt OCR text parsing (no Tesseract required)."""

import unittest
from unittest.mock import patch

from receipt_ocr import (
    extract_total_from_text,
    guess_merchant_from_text,
    parse_line_items_from_text,
    preprocess_for_ocr,
    scan_receipt_image,
)


class ReceiptOcrParseTests(unittest.TestCase):
    def test_parses_multiple_lines_with_prices(self):
        text = """
        Mario's Bistro
        Pasta Carbonara     14.50
        2 x Garlic Bread    6.00
        SUBTOTAL
        TOTAL 20.50
        """
        items, score = parse_line_items_from_text(text)
        self.assertGreaterEqual(len(items), 2)
        self.assertGreater(score, 0.4)
        names = [i.name.lower() for i in items]
        self.assertTrue(any("pasta" in n for n in names))

    def test_skips_total_line(self):
        text = "TOTAL 99.99\nThank you"
        items, score = parse_line_items_from_text(text)
        self.assertEqual(len(items), 0)
        self.assertEqual(score, 0.0)

    def test_parses_rs_prefix(self):
        text = "Dal Bhat  Rs 180.00"
        items, _ = parse_line_items_from_text(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].price, 180.0)

    def test_parses_whole_rupee_amount(self):
        text = "Momo Plate  180"
        items, _ = parse_line_items_from_text(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].price, 180.0)
        self.assertEqual(items[0].name, "Momo Plate")

    def test_deduplicates_same_item_name(self):
        text = "Tea 50.00\nTea 50.00"
        items, _ = parse_line_items_from_text(text)
        self.assertEqual(len(items), 1)

    def test_extract_total_from_text(self):
        text = "Subtotal 100.00\nTOTAL Rs 125.50\nThank you"
        self.assertEqual(extract_total_from_text(text), 125.5)

    def test_guess_merchant_from_header(self):
        text = "Himalayan Cafe\nThamel\nDal Bhat 180.00"
        self.assertEqual(guess_merchant_from_text(text), "Himalayan Cafe")


class ReceiptOcrScanTests(unittest.TestCase):
    @patch("receipt_ocr.tesseract_is_available", return_value=False)
    def test_unavailable_tesseract(self, _mock_avail):
        result = scan_receipt_image(b"fake")
        self.assertFalse(result.success)
        self.assertFalse(result.ocr_available)

    @patch("receipt_ocr.run_ocr_on_image", return_value="")
    @patch("receipt_ocr.tesseract_is_available", return_value=True)
    def test_empty_ocr_text(self, _avail, _ocr):
        result = scan_receipt_image(b"fake")
        self.assertFalse(result.success)
        self.assertIn("No text detected", result.message)

    @patch("receipt_ocr.run_ocr_on_image", return_value="TOTAL 50.00")
    @patch("receipt_ocr.tesseract_is_available", return_value=True)
    def test_no_line_items(self, _avail, _ocr):
        result = scan_receipt_image(b"fake")
        self.assertFalse(result.success)
        self.assertEqual(len(result.items), 0)

    @patch(
        "receipt_ocr.run_ocr_on_image",
        return_value="Cafe\nItem A 10.00\nItem B 20.00\nItem C 30.00\nTOTAL 60.00",
    )
    @patch("receipt_ocr.tesseract_is_available", return_value=True)
    def test_high_confidence_requires_review(self, _avail, _ocr):
        result = scan_receipt_image(b"fake")
        self.assertTrue(result.success)
        self.assertTrue(result.requires_review)
        self.assertGreaterEqual(len(result.items), 3)
        self.assertIn("Review", result.message)

    @patch(
        "receipt_ocr.run_ocr_on_image",
        return_value="Shop\nItem A 10.00\nTOTAL 100.00",
    )
    @patch("receipt_ocr.tesseract_is_available", return_value=True)
    def test_total_mismatch_warning(self, _avail, _ocr):
        result = scan_receipt_image(b"fake")
        self.assertTrue(result.validation_warning)
        self.assertEqual(result.suggested_total, 100.0)

    @patch("receipt_ocr.run_ocr_on_image", side_effect=RuntimeError("boom"))
    @patch("receipt_ocr.tesseract_is_available", return_value=True)
    def test_ocr_runtime_error(self, _avail, _ocr):
        result = scan_receipt_image(b"fake")
        self.assertFalse(result.success)
        self.assertEqual(result.confidence, "none")


class PreprocessTests(unittest.TestCase):
    def test_preprocess_upscales_narrow_image(self):
        from PIL import Image

        tiny = Image.new("RGB", (400, 600), color=(240, 240, 240))
        processed = preprocess_for_ocr(tiny)
        self.assertGreaterEqual(processed.size[0], 1200)


if __name__ == "__main__":
    unittest.main()
