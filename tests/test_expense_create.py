"""Tests for expense input validation and split total guards."""

import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from expense_create import (
    parse_expense_amount,
    parse_expense_date,
    validate_category,
    validate_description,
    validate_remarks,
)
from expense_participants import validate_participants
from expense_split_logic import (
    ParsedLineItem,
    compute_exact_split,
    compute_percentage_split,
    to_decimal,
)
from money import assert_split_covers_total, quantize_money, split_sum_matches_total


class ExpenseValidationTests(unittest.TestCase):
    def test_validate_description_required(self):
        with self.assertRaises(ValueError):
            validate_description("   ")

    def test_validate_description_max_length(self):
        with self.assertRaises(ValueError):
            validate_description("x" * 256)

    def test_validate_category_defaults(self):
        self.assertEqual(validate_category(""), "General")

    def test_validate_remarks_max_length(self):
        with self.assertRaises(ValueError):
            validate_remarks("x" * 256)

    def test_parse_expense_amount_rejects_zero(self):
        with self.assertRaises(ValueError):
            parse_expense_amount("0")

    def test_parse_expense_amount_rejects_invalid(self):
        with self.assertRaises(ValueError):
            parse_expense_amount("abc")

    def test_parse_expense_amount_accepts_decimal_string(self):
        self.assertEqual(parse_expense_amount("12.50"), Decimal("12.50"))

    def test_parse_expense_date_optional(self):
        form = MagicMock()
        form.get.return_value = ""
        self.assertIsNone(parse_expense_date(form))

    def test_parse_expense_date_rejects_bad_format(self):
        form = MagicMock()
        form.get.return_value = "07/08/2026"
        with self.assertRaises(ValueError):
            parse_expense_date(form)

    def test_parse_expense_date_rejects_future(self):
        form = MagicMock()
        future = (date.today() + timedelta(days=5)).isoformat()
        form.get.return_value = future
        with self.assertRaises(ValueError):
            parse_expense_date(form)

    def test_assert_split_covers_total_passes(self):
        assert_split_covers_total(Decimal("100"), {1: Decimal("60"), 2: Decimal("40")})

    def test_assert_split_covers_total_fails(self):
        with self.assertRaises(ValueError):
            assert_split_covers_total(Decimal("100"), {1: Decimal("50"), 2: Decimal("40")})


class ParticipantValidationTests(unittest.TestCase):
    def test_requires_at_least_two_people(self):
        with self.assertRaises(ValueError):
            validate_participants(1, [1])

    def test_standalone_rejects_unknown_participant(self):
        with self.assertRaises(ValueError):
            validate_participants(1, [1, 99], allowed_user_ids={1, 2})

    @patch("expense_participants.get_trip_member_ids", return_value=[2, 3])
    def test_trip_requires_payer_membership(self, _mock_members):
        with self.assertRaises(ValueError):
            validate_participants(1, [1, 2], trip_id=5)


class SplitTotalEdgeCaseTests(unittest.TestCase):
    def test_exact_split_rejects_mismatch(self):
        with self.assertRaises(ValueError):
            compute_exact_split("100", {1: "40", 2: "50"})

    def test_percentage_split_rejects_over_100(self):
        with self.assertRaises(ValueError):
            compute_percentage_split("100", {1: "60", 2: "50"})

    def test_decimal_string_preserves_cents(self):
        owed = compute_exact_split("10.00", {1: "3.33", 2: "6.67"})
        self.assertTrue(split_sum_matches_total(owed, Decimal("10.00")))

    def test_itemized_uses_decimal_prices(self):
        from expense_split_logic import compute_itemized_split

        items = [
            ParsedLineItem(
                name="Tea",
                price=to_decimal("3.33"),
                quantity=to_decimal("3"),
                assigned_user_ids=[1],
            )
        ]
        owed, subtotal = compute_itemized_split(items, "0", [1, 2])
        self.assertEqual(subtotal, Decimal("9.99"))
        self.assertTrue(split_sum_matches_total(owed, subtotal))


if __name__ == "__main__":
    unittest.main()
