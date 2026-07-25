"""Tests for Khalti server-side payment confirmation."""

import unittest
from unittest.mock import patch

from khalti_pay import (
    confirm_khalti_for_payment_link,
    khalti_purchase_order_id,
    parse_purchase_order_link_id,
)


class KhaltiConfirmTests(unittest.TestCase):
    def test_purchase_order_id_roundtrip(self):
        order = khalti_purchase_order_id(42)
        self.assertEqual(parse_purchase_order_link_id(order), 42)

    @patch("khalti_pay.lookup_khalti_payment")
    def test_confirm_success(self, mock_lookup):
        mock_lookup.return_value = {
            "status": "Completed",
            "total_amount": 2500,
            "purchase_order_id": "splitbills-link-7",
        }
        ok, msg, data = confirm_khalti_for_payment_link(
            secret_key="test_secret_key_x",
            pidx="pidx-abc",
            payment_link_id=7,
            amount_owed_rupees=25.0,
        )
        self.assertTrue(ok)
        self.assertEqual(msg, "confirmed")
        self.assertIsNotNone(data)

    @patch("khalti_pay.lookup_khalti_payment")
    def test_confirm_rejects_amount_mismatch(self, mock_lookup):
        mock_lookup.return_value = {
            "status": "Completed",
            "total_amount": 9999,
            "purchase_order_id": "splitbills-link-7",
        }
        ok, msg, _ = confirm_khalti_for_payment_link(
            secret_key="test_secret_key_x",
            pidx="pidx-abc",
            payment_link_id=7,
            amount_owed_rupees=25.0,
        )
        self.assertFalse(ok)
        self.assertIn("amount_mismatch", msg)


if __name__ == "__main__":
    unittest.main()
