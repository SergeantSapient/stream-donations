"""Ledger storage, processing flags, and spreadsheet-safe export."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from streamdonations.normalize import Donation, normalize_super_chat_event
from streamdonations.store import Store


def sample(event_id, message):
    return normalize_super_chat_event(
        {
            "id": event_id,
            "snippet": {
                "channelId": "UCb",
                "supporterDetails": {"displayName": "Fan", "channelId": "UCf"},
                "commentText": message,
                "createdAt": "2024-06-01T12:00:00Z",
                "amountMicros": "1000000",
                "currency": "USD",
            },
        }
    )


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_dedupe_process_and_export(self):
        first = sample("a", "=HYPERLINK(\"http://evil\")")
        self.assertEqual(self.store.add_many([first, first]), 1)
        self.assertEqual(self.store.add_many([first]), 0)
        self.assertEqual(self.store.add_many([sample("b", "thanks")]), 1)
        rows, total = self.store.list_donations(processed="0")
        self.assertEqual(total, 2)
        updated = self.store.update_donation(rows[0]["id"], processed=True, note="thanked on stream")
        self.assertTrue(updated["processed"])
        self.assertEqual(updated["note"], "thanked on stream")
        waiting, waiting_total = self.store.list_donations(processed="0")
        self.assertEqual(waiting_total, 1)
        self.assertEqual(self.store.update_many([item["id"] for item in waiting], processed=True), 1)
        exported = self.store.export_csv()
        self.assertIn("'=HYPERLINK", exported)
        self.assertNotIn("\n=HYPERLINK", "\n" + exported)
        summary = self.store.summary()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["unprocessed"], 0)
        self.assertEqual(summary["by_currency"][0]["currency"], "USD")
        self.store.set_setting("kept", "yes")
        self.assertEqual(self.store.clear_donations(), 2)
        _rows, total = self.store.list_donations()
        self.assertEqual(total, 0)
        self.assertEqual(self.store.get_setting("kept"), "yes")

    def test_running_total_follows_the_whole_ledger(self):
        self.store.add_manual_entry(
            author_name="@Ada",
            platform="youtube",
            kind="super_chat",
            amount=1,
            created_at="2024-01-01T12:00:00Z",
        )
        self.store.add_manual_entry(
            author_name="Ben",
            platform="rumble",
            kind="membership",
            amount=10,
            created_at="2024-01-02T12:00:00Z",
        )
        self.store.add_many(
            [
                Donation(
                    dedupe_key="gift:1",
                    platform="youtube",
                    kind="gift_membership_received",
                    external_id="g1",
                    channel="",
                    author_name="@Recipient",
                    author_id="",
                    message="received",
                    amount_value=None,
                    amount_micros=None,
                    currency=None,
                    created_at="2024-01-02T18:00:00Z",
                    raw={},
                )
            ]
        )
        self.store.add_manual_entry(
            author_name="Cam",
            platform="youtube",
            kind="super_chat",
            amount=1.5,
            created_at="2024-01-03T12:00:00Z",
        )
        rows, total = self.store.list_donations(rates={"USD": 1.0})
        self.assertEqual(total, 4)
        self.assertEqual([row["author_name"] for row in rows], ["Cam", "Recipient", "Ben", "Ada"])
        self.assertEqual([row["running_usd"] for row in rows], [12.5, 11.0, 11.0, 1.0])
        filtered, filtered_total = self.store.list_donations(kind="super_chat", rates={"USD": 1.0})
        self.assertEqual(filtered_total, 2)
        self.assertEqual([row["running_usd"] for row in filtered], [12.5, 1.0])
        page, page_total = self.store.list_donations(limit=1, offset=1, rates={"USD": 1.0})
        self.assertEqual(page_total, 4)
        self.assertEqual(page[0]["author_name"], "Recipient")
        self.assertEqual(page[0]["running_usd"], 11.0)
        self.assertEqual(page[0]["amount_value"], None)

    def test_search_prefixes_and_plain_words(self):
        self.store.add_manual_entry(
            author_name="@Ada",
            platform="youtube",
            kind="super_chat",
            amount=1,
            message="thanks",
            created_at="2024-01-01T00:00:00Z",
        )
        self.store.add_manual_entry(
            author_name="Ben",
            platform="kick",
            kind="membership",
            amount=4.99,
            message="Smashers",
            created_at="2024-01-02T00:00:00Z",
        )
        self.store.add_manual_entry(
            author_name="Cam",
            platform="other",
            kind="donation",
            amount=12,
            message="cash from @Ada",
            created_at="2024-01-03T00:00:00Z",
        )
        rates = {"USD": 1.0}

        def names(query):
            rows, _total = self.store.list_donations(query=query, rates=rates)
            return [row["author_name"] for row in rows]

        self.assertEqual(names("@ada"), ["Ada"])
        self.assertEqual(names("$4.99"), ["Ben"])
        self.assertEqual(names("$1"), ["Ada"])
        self.assertEqual(names("#member"), ["Ben"])
        self.assertEqual(names("#chat #member"), ["Ben", "Ada"])
        self.assertEqual(names("!thanks"), ["Ada"])
        self.assertEqual(names("!@Ada"), ["Cam"])
        self.assertEqual(names("@ada #chat"), ["Ada"])
        self.assertEqual(names("cash"), ["Cam"])
        self.assertEqual(names("#sticker"), [])
        rows, total = self.store.list_donations(query="@ada", limit=1, offset=1, rates=rates)
        self.assertEqual(total, 1)
        self.assertEqual(rows, [])
        matched, _total = self.store.list_donations(query="@ada", rates=rates)
        self.assertEqual(matched[0]["running_usd"], 1.0)
        exported = self.store.export_csv(rates=rates, query="@ada")
        self.assertIn("Ada", exported)
        self.assertNotIn("@Ada", exported)
        self.assertNotIn("Ben", exported)
        self.assertNotIn("Cam", exported)

    @unittest.skipIf(os.name == "nt", "Windows has no Unix permission bits")
    def test_database_file_is_private(self):
        mode = self.store.path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_hidden_automatic_entries_drop_out_until_counted_again(self):
        self.store.add_manual_entry(
            author_name="Ada",
            platform="youtube",
            kind="super_chat",
            amount=1,
            created_at="2024-01-01T12:00:00Z",
        )
        automatic = sample("hidden-fan", "from chat")
        self.store.add_many([automatic])
        self.store.add_manual_entry(
            author_name="Cam",
            platform="youtube",
            kind="super_chat",
            amount=1.5,
            created_at="2024-06-02T12:00:00Z",
        )
        rows, _total = self.store.list_donations(rates={"USD": 1.0})
        hidden_id = next(row["id"] for row in rows if row["author_name"] == "Fan")
        manual_id = next(row["id"] for row in rows if row["author_name"] == "Ada")
        self.assertTrue(next(row["manual"] for row in rows if row["id"] == manual_id))
        self.assertFalse(next(row["manual"] for row in rows if row["id"] == hidden_id))

        with self.assertRaises(ValueError):
            self.store.delete_manual_entry(hidden_id)
        with self.assertRaises(ValueError):
            self.store.set_entry_hidden(manual_id, True)

        hidden = self.store.set_entry_hidden(hidden_id, True)
        self.assertTrue(hidden["hidden"])
        visible, visible_total = self.store.list_donations(rates={"USD": 1.0})
        self.assertEqual(visible_total, 2)
        self.assertEqual([row["author_name"] for row in visible], ["Cam", "Ada"])
        self.assertEqual([row["running_usd"] for row in visible], [2.5, 1.0])
        self.assertEqual(self.store.total_donated({"USD": 1.0}, None), 2.5)
        self.assertEqual(self.store.summary()["total"], 3)
        self.assertEqual(self.store.summary()["by_currency"][0]["amount"], 2.5)
        report = self.store.stream_report({"USD": 1.0}, None)
        self.assertEqual(report["total_usd"], 2.5)
        self.assertEqual(report["count"], 2)
        self.assertNotIn("Fan", report["donor_bar"])
        exported = self.store.export_csv(rates={"USD": 1.0}, include_hidden=True)
        self.assertNotIn("Fan", exported)
        self.assertIn("Cam", exported)
        jsonl = self.store.export_jsonl(include_hidden=True)
        self.assertNotIn("from chat", jsonl)

        shown, shown_total = self.store.list_donations(rates={"USD": 1.0}, include_hidden=True)
        self.assertEqual(shown_total, 3)
        parked = next(row for row in shown if row["id"] == hidden_id)
        self.assertTrue(parked["hidden"])
        self.assertEqual(parked["running_usd"], 1.0)

        self.store.set_entry_hidden(hidden_id, False)
        restored, restored_total = self.store.list_donations(rates={"USD": 1.0})
        self.assertEqual(restored_total, 3)
        self.assertEqual(restored[0]["running_usd"], 3.5)
        self.assertEqual(self.store.total_donated({"USD": 1.0}, None), 3.5)
        self.assertIn("Fan", self.store.stream_report({"USD": 1.0}, None)["donor_bar"])

        self.store.set_entry_hidden(hidden_id, True)
        self.assertEqual(self.store.clear_donations(), 3)
        _rows, total = self.store.list_donations(include_hidden=True)
        self.assertEqual(total, 0)

    def test_manual_entries_can_be_deleted(self):
        manual_id = self.store.add_manual_entry(
            author_name="Ada",
            platform="other",
            kind="donation",
            amount=4,
            created_at="2024-01-01T12:00:00Z",
        )
        self.store.add_many([sample("keep", "stay")])
        self.assertTrue(self.store.delete_manual_entry(manual_id))
        self.assertFalse(self.store.delete_manual_entry(manual_id))
        rows, total = self.store.list_donations(rates={"USD": 1.0})
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["author_name"], "Fan")
        self.assertEqual(self.store.total_donated({"USD": 1.0}, None), 1.0)

    def test_deleted_manual_entry_can_be_restored(self):
        manual_id = self.store.add_manual_entry(
            author_name="Ada",
            platform="other",
            kind="donation",
            amount=4,
            currency="PHP",
            message="cash",
            created_at="2024-01-01T12:00:00Z",
        )
        self.store.update_donation(manual_id, processed=True, note="envelope")
        current = self.store.get_donation(manual_id)
        self.assertTrue(self.store.delete_manual_entry(manual_id))
        self.assertIsNone(self.store.get_donation(manual_id))
        restored_id = self.store.restore_manual_entry({
            "id": current["id"],
            "dedupe_key": current["dedupe_key"],
            "author_name": current["author_name"],
            "platform": current["platform"],
            "kind": current["kind"],
            "amount": current["amount_value"],
            "currency": current["currency"],
            "message": current["message"],
            "created_at": current["created_at"],
            "received_at": current["received_at"],
            "processed": current["processed"],
            "processed_at": current["processed_at"],
            "note": current["note"],
            "raw": {"token": "do-not-keep"},
            "hidden": True,
            "author_id": "forged",
            "channel": "forged",
        })
        self.assertEqual(restored_id, manual_id)
        row = self.store.get_donation(manual_id)
        self.assertEqual(row["author_name"], "Ada")
        self.assertEqual(row["currency"], "PHP")
        self.assertEqual(row["amount_value"], 4.0)
        self.assertEqual(row["message"], "cash")
        self.assertEqual(row["note"], "envelope")
        self.assertTrue(row["processed"])
        self.assertEqual(row["processed_at"], current["processed_at"])
        self.assertEqual(row["created_at"], "2024-01-01T12:00:00Z")
        self.assertEqual(row["received_at"], current["received_at"])
        self.assertEqual(row["dedupe_key"], current["dedupe_key"])
        self.assertFalse(row["hidden"])
        self.assertEqual(row["raw"], {"manual": True})
        self.assertEqual(row["author_id"], "")
        self.assertEqual(row["channel"], "")
        with self.assertRaises(ValueError):
            self.store.restore_manual_entry({
                "id": current["id"],
                "dedupe_key": current["dedupe_key"],
                "author_name": current["author_name"],
                "platform": current["platform"],
                "kind": current["kind"],
                "amount": current["amount_value"],
                "currency": current["currency"],
                "message": current["message"],
                "created_at": current["created_at"],
            })
        with self.assertRaises(ValueError):
            self.store.restore_manual_entry({"dedupe_key": "youtube:automatic"})
        self.assertEqual(self.store.total_donated({"PHP": 50.0}, None), 0.08)

    def test_older_ledger_gains_a_hidden_column(self):
        path = Path(self.tmp.name) / "old.db"
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE donations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL UNIQUE,
                platform TEXT NOT NULL,
                kind TEXT NOT NULL,
                external_id TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT '',
                author_name TEXT NOT NULL DEFAULT '',
                author_id TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                amount_value REAL,
                amount_micros INTEGER,
                currency TEXT,
                created_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                processed INTEGER NOT NULL DEFAULT 0,
                processed_at TEXT,
                note TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO donations (
                dedupe_key, platform, kind, author_name, amount_value, currency,
                created_at, received_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "youtube:old",
                "youtube",
                "super_chat",
                "OldFan",
                3,
                "USD",
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
                "{}",
            ),
        )
        conn.commit()
        conn.close()
        store = Store(path)
        try:
            rows, total = store.list_donations(rates={"USD": 1.0})
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["author_name"], "OldFan")
            self.assertFalse(rows[0]["hidden"])
            self.assertFalse(rows[0]["manual"])
            self.assertEqual(store.total_donated({"USD": 1.0}, None), 3.0)
            first = store.stream_tabs()["active_id"]
            store.add_stream()
            self.assertEqual(store.list_donations()[1], 0)
            self.assertEqual(store.summary()["total"], 0)
            store.switch_stream(first)
            self.assertEqual(store.list_donations()[1], 1)
            self.assertEqual(store.summary()["total"], 1)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
