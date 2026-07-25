"""Tests for receipt OCR text parsing (no Tesseract required)."""

import unittest

from receipt_ocr import parse_line_items_from_text


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


if __name__ == "__main__":
    unittest.main()
