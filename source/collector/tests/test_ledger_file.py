"""A JSONL export can be opened for viewing and is never written back."""

import json
import unittest

from streamdonations.ledger_file import parse_ledger_jsonl, present_archive
from streamdonations.settings_file import export_settings


def _line(row):
    return json.dumps(row, ensure_ascii=False)


def _view(body, price=None, rates=None):
    rows = parse_ledger_jsonl(body if isinstance(body, bytes) else body.encode())
    return present_archive(rows, membership_price=price, rates=rates or {})


class LedgerFileTests(unittest.TestCase):
    def test_one_object_is_one_row_and_drops_secrets(self):
        body = _line({
            "platform": "YouTube",
            "kind": "super_chat",
            "author_name": "@Ada",
            "amount_value": 3,
            "currency": "usd",
            "message": "hi",
            "dedupe_key": "manual:1",
            "youtube_client_secret": "ledger-archive-token",
            "note": "kept",
        })
        view = _view(body)
        self.assertEqual(view["count"], 1)
        row = view["donations"][0]
        self.assertEqual(row["id"], 1)
        self.assertEqual(row["author_name"], "Ada")
        self.assertEqual(row["platform"], "youtube")
        self.assertEqual(row["platform_label"], "YouTube")
        self.assertEqual(row["quoted_amount"], 3)
        self.assertEqual(row["quoted_currency"], "USD")
        self.assertEqual(row["amount_usd"], 3)
        self.assertEqual(view["total_usd"], 3)
        self.assertTrue(row["manual"])
        self.assertEqual(row["note"], "kept")
        self.assertNotIn("raw", row)
        self.assertNotIn("dedupe_key", row)
        self.assertNotIn("ledger-archive-token", json.dumps(view))

    def test_pretty_donation_object_is_still_one_row(self):
        body = json.dumps({
            "platform": "rumble",
            "kind": "rant",
            "author_name": "Bea",
            "amount_value": 4,
            "currency": "USD",
            "created_at": "2024-02-01T00:00:00Z",
        }, indent=2)
        view = _view(body)
        self.assertEqual(view["count"], 1)
        self.assertEqual(view["donations"][0]["kind_label"], "Super Chat")
        self.assertEqual(view["donations"][0]["author_name"], "Bea")

    def test_two_lines_run_oldest_first_and_show_newest_first(self):
        body = "\n".join([
            "",
            _line({
                "id": 1,
                "platform": "kick",
                "kind": "donation",
                "author_name": "Older",
                "amount_value": 2,
                "currency": "USD",
                "created_at": "2024-01-01T00:00:00Z",
            }),
            "",
            _line({
                "id": 2,
                "platform": "kick",
                "kind": "donation",
                "author_name": "Newer",
                "amount_value": 10,
                "currency": "USD",
                "created_at": "2024-06-01T00:00:00Z",
            }),
        ]) + "\n"
        view = _view(body)
        self.assertEqual(view["count"], 2)
        self.assertEqual(view["total_usd"], 12)
        self.assertEqual(view["donations"][0]["author_name"], "Newer")
        self.assertEqual(view["donations"][0]["running_usd"], 12)
        self.assertEqual(view["donations"][1]["author_name"], "Older")
        self.assertEqual(view["donations"][1]["running_usd"], 2)

    def test_hidden_row_stays_visible_and_adds_nothing(self):
        body = "\n".join([
            _line({
                "id": 1,
                "platform": "youtube",
                "kind": "super_chat",
                "author_name": "Shown",
                "amount_value": 5,
                "currency": "USD",
                "created_at": "2024-01-01T00:00:00Z",
            }),
            _line({
                "id": 2,
                "platform": "youtube",
                "kind": "super_chat",
                "author_name": "Quiet",
                "amount_value": 20,
                "currency": "USD",
                "created_at": "2024-06-01T00:00:00Z",
                "hidden": True,
            }),
        ]) + "\n"
        view = _view(body)
        self.assertEqual(view["count"], 2)
        self.assertEqual(view["total_usd"], 5)
        self.assertTrue(view["donations"][0]["hidden"])
        self.assertEqual(view["donations"][0]["author_name"], "Quiet")
        self.assertEqual(view["donations"][0]["amount_usd"], 20)
        self.assertEqual(view["donations"][0]["running_usd"], 5)
        self.assertEqual(view["donations"][1]["running_usd"], 5)

    def test_raw_prices_the_row_and_is_not_returned(self):
        body = _line({
            "platform": "youtube",
            "kind": "super_chat",
            "author_name": "Ada",
            "amount_value": None,
            "created_at": "2024-06-01T12:00:00Z",
            "access_token": "ledger-archive-token",
            "raw": {
                "access_token": "ledger-archive-token",
                "snippet": {"amountMicros": "2500000", "currency": "USD"},
            },
        }) + "\n"
        view = _view(body)
        self.assertEqual(view["donations"][0]["quoted_amount"], 2.5)
        self.assertEqual(view["donations"][0]["amount_usd"], 2.5)
        self.assertEqual(view["total_usd"], 2.5)
        encoded = json.dumps(view)
        self.assertNotIn("ledger-archive-token", encoded)
        self.assertNotIn("access_token", encoded)
        self.assertNotIn("raw", view["donations"][0])

    def test_null_membership_uses_the_price_it_is_shown_with(self):
        body = _line({
            "platform": "kick",
            "kind": "membership",
            "author_name": "Max",
            "amount_value": None,
            "message": "Subscribed",
            "created_at": "2024-06-01T00:00:00Z",
        }) + "\n"
        blank = _view(body, price=None)
        self.assertIsNone(blank["donations"][0]["amount_usd"])
        self.assertIsNone(blank["donations"][0]["quoted_amount"])
        self.assertEqual(blank["total_usd"], 0)
        priced = _view(body, price=7.25)
        self.assertEqual(priced["donations"][0]["quoted_amount"], 7.25)
        self.assertEqual(priced["donations"][0]["quoted_currency"], "USD")
        self.assertEqual(priced["donations"][0]["kind_label"], "Membership")
        self.assertEqual(priced["total_usd"], 7.25)

    def test_kick_coins_are_quoted_in_dollars(self):
        body = _line({
            "platform": "kick",
            "kind": "kicks",
            "author_name": "Max",
            "amount_value": 500,
            "currency": "KICKS",
            "created_at": "2024-06-01T00:00:00Z",
        })
        view = _view(body)
        row = view["donations"][0]
        self.assertEqual(row["quoted_amount"], 5)
        self.assertEqual(row["quoted_currency"], "USD")
        self.assertEqual(row["kind_label"], "Super Chat")
        self.assertEqual(view["total_usd"], 5)

    def test_gift_receipt_adds_nothing(self):
        body = "\n".join([
            _line({
                "platform": "youtube",
                "kind": "super_chat",
                "amount_value": 3,
                "currency": "USD",
                "created_at": "2024-01-01T00:00:00Z",
            }),
            _line({
                "platform": "youtube",
                "kind": "gift_membership_received",
                "amount_value": 5,
                "currency": "USD",
                "created_at": "2024-06-01T00:00:00Z",
            }),
        ])
        view = _view(body)
        self.assertEqual(view["count"], 2)
        self.assertEqual(view["total_usd"], 3)
        self.assertIsNone(view["donations"][0]["amount_usd"])

    def test_rejected_files(self):
        settings = export_settings({"membership_price": "4.99"})
        pretty = json.dumps(json.loads(settings), indent=2).encode()
        refused = [
            (settings, "This is a settings file, not a ledger."),
            (pretty, "This is a settings file, not a ledger."),
            (b"Date,Time,Donor,Amount\n", "CSV cannot be opened as a ledger."),
            (b"Date, Time, Donor\n", "CSV cannot be opened as a ledger."),
            (b"SQLite format 3\x00not-a-ledger", "A database file stays closed."),
            (b"", "This ledger file is empty."),
            (b"\n\n", "This ledger file is empty."),
            (b"[]", "A ledger archive is JSONL, one donation per line."),
            (b'[{"platform":"youtube","kind":"super_chat"}]', "A ledger archive is JSONL, one donation per line."),
            (
                _line({"platform": "twitch", "kind": "super_chat", "amount_value": 1}).encode(),
                "Line 1 is not a ledger row.",
            ),
            (
                _line({"platform": "youtube", "kind": "nope"}).encode(),
                "Line 1 is not a ledger row.",
            ),
            (
                _line({"platform": "youtube", "kind": "super_chat", "amount_value": True}).encode(),
                "Line 1 is not a ledger row.",
            ),
            (
                b'{"platform":"youtube","kind":"super_chat","amount_value":NaN}\n',
                "Line 1 is not a ledger row.",
            ),
            (
                _line({"platform": "youtube", "kind": "super_chat", "id": True}).encode(),
                "Line 1 is not a ledger row.",
            ),
            (
                _line({"platform": "youtube", "kind": "super_chat", "hidden": 1}).encode(),
                "Line 1 is not a ledger row.",
            ),
        ]
        for body, message in refused:
            with self.subTest(message=message, body=body[:40]):
                with self.assertRaises(ValueError) as caught:
                    parse_ledger_jsonl(body)
                self.assertEqual(str(caught.exception), message)
        huge = b" " * 1_000_001
        with self.assertRaises(ValueError) as caught:
            parse_ledger_jsonl(huge)
        self.assertEqual(str(caught.exception), "That ledger file is too large.")
        line = _line({"platform": "youtube", "kind": "super_chat", "amount_value": 1, "currency": "USD"})
        with self.assertRaises(ValueError) as caught:
            parse_ledger_jsonl(((line + "\n") * 5001).encode())
        self.assertEqual(str(caught.exception), "A ledger archive is limited to 5000 donations.")
