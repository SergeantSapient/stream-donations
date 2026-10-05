"""Search prefixes for the ledger."""

import unittest

from streamdonations.search import matches_ledger_query


def row(**fields):
    base = {
        "author_name": "Fan",
        "message": "",
        "channel": "",
        "note": "",
        "platform": "kick",
        "platform_label": "Kick",
        "kind": "kicks",
        "kind_label": "Kicks",
        "currency": "KICKS",
        "amount_value": 500,
        "quoted_amount": 5,
        "quoted_currency": "USD",
        "amount_usd": 5,
    }
    base.update(fields)
    return base


class SearchTests(unittest.TestCase):
    def test_kick_amount_matches_coins_or_dollars(self):
        gift = row()
        self.assertTrue(matches_ledger_query(gift, "$5.00"))
        self.assertTrue(matches_ledger_query(gift, "$500"))
        self.assertFalse(matches_ledger_query(gift, "$5.01"))

    def test_type_prefix_uses_the_displayed_type(self):
        self.assertTrue(matches_ledger_query(row(), "#chat"))
        self.assertTrue(matches_ledger_query(row(amount_value=100, quoted_amount=1, amount_usd=1), "#sticker"))
        self.assertFalse(matches_ledger_query(row(), "#sticker"))

    def test_donor_prefix_ignores_the_message(self):
        gift = row(author_name="Ben", message="hello @Ada", kind="donation", currency="USD", amount_value=3, quoted_amount=3, amount_usd=3)
        self.assertFalse(matches_ledger_query(gift, "@ada"))
        self.assertTrue(matches_ledger_query(gift, "!@Ada"))
        self.assertTrue(matches_ledger_query(gift, "@ben"))

    def test_unknown_type_matches_nothing(self):
        self.assertFalse(matches_ledger_query(row(), "#nope"))


if __name__ == "__main__":
    unittest.main()
