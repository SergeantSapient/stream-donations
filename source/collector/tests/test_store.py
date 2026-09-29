"""Ledger storage, processing flags, and spreadsheet-safe export."""

import tempfile
import unittest
from pathlib import Path

from streamdonations.normalize import normalize_super_chat_event
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

    def test_database_file_is_private(self):
        mode = self.store.path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
