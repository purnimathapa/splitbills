"""Tests for signed guest payment link tokens."""

import unittest

from payment_links import (
    build_signed_payment_token,
    decode_payment_token,
)


class PaymentLinkTokenTests(unittest.TestCase):
    secret = "test-secret-key"

    def test_sign_and_decode_payload(self):
        token = build_signed_payment_token(
            self.secret,
            link_uuid="550e8400-e29b-41d4-a716-446655440000",
            expense_id=12,
            user_id=3,
        )
        payload = decode_payment_token(self.secret, token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["expense_id"], 12)
        self.assertEqual(payload["user_id"], 3)

    def test_tampered_token_rejected(self):
        token = build_signed_payment_token(
            self.secret,
            link_uuid="550e8400-e29b-41d4-a716-446655440000",
            expense_id=12,
            user_id=3,
        )
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        self.assertIsNone(decode_payment_token(self.secret, tampered))

    def test_wrong_secret_rejected(self):
        token = build_signed_payment_token(
            self.secret,
            link_uuid="550e8400-e29b-41d4-a716-446655440000",
            expense_id=1,
            user_id=2,
        )
        self.assertIsNone(decode_payment_token("other-secret", token))


if __name__ == "__main__":
    unittest.main()
