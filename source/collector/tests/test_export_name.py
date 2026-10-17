"""CSV and JSONL downloads are named MMDDYYYY-Ledger."""

import tempfile
import unittest
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from streamdonations.normalize import Donation
from streamdonations.store import Store
from streamdonations.util import content_disposition, download_filename

EASTERN = ZoneInfo("America/New_York")


def _gift(key: str, author: str, when: str) -> Donation:
    return Donation(
        dedupe_key=key,
        platform="youtube",
        kind="super_chat",
        external_id=key,
        channel="",
        author_name=author,
        author_id="",
        message="hello",
        amount_value=5,
        amount_micros=5_000_000,
        currency="USD",
        created_at=when,
        raw={"kept": True},
    )


class DownloadFilenameTests(unittest.TestCase):
    def test_date_then_name(self):
        self.assertEqual(
            download_filename("Friday Night", "2024-06-01T12:00:00Z", "csv", timezone.utc),
            "06012024-Friday Night.csv",
        )
        self.assertEqual(
            download_filename("Friday Night", "2024-06-01T12:00:00Z", "jsonl", timezone.utc),
            "06012024-Friday Night.jsonl",
        )

    def test_local_day_can_be_the_day_before_utc(self):
        # 03:30 UTC is still May 31 in New York.
        self.assertEqual(
            download_filename("Friday", "2024-06-01T03:30:00Z", "csv", EASTERN),
            "05312024-Friday.csv",
        )

    def test_invalid_characters_are_replaced_for_windows_linux_and_mac(self):
        self.assertEqual(
            download_filename('A/B\\C:D*E?F"G<H>I|J', "2024-06-01T12:00:00Z", "csv", timezone.utc),
            "06012024-A_B_C_D_E_F_G_H_I_J.csv",
        )
        self.assertEqual(
            download_filename("Friday.\n", "", "csv", timezone.utc),
            "Friday.csv",
        )
        self.assertEqual(download_filename("   ", "", "jsonl", timezone.utc), "Stream.jsonl")
        self.assertEqual(download_filename(":::", "", "csv", timezone.utc), "Stream.csv")
        self.assertEqual(download_filename("CON", "", "csv", timezone.utc), "CON_.csv")
        self.assertEqual(
            download_filename("CON", "2024-06-01T12:00:00Z", "csv", timezone.utc),
            "06012024-CON.csv",
        )

    def test_a_long_name_stays_under_255_bytes_and_keeps_the_extension(self):
        name = download_filename("😀" * 80, "2024-06-01T12:00:00Z", "jsonl", timezone.utc)
        self.assertEqual(name, "06012024-" + ("😀" * 60) + ".jsonl")
        self.assertLessEqual(len(name.encode("utf-8")), 255)
        header = content_disposition(name)
        self.assertTrue(header.isascii())
        self.assertIn("filename*=UTF-8''", header)
        self.assertIn("06012024-", header)

    def test_a_ledger_name_uses_its_oldest_visible_donation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "donations.db")
            try:
                store.set_setting("stream_title", "Friday/Night")
                store.add_many([
                    _gift("late", "Bea", "2024-06-02T15:00:00Z"),
                    _gift("early", "Ada", "2024-06-01T03:30:00Z"),
                ])
                hidden = store._conn.execute(
                    "SELECT id FROM donations WHERE dedupe_key = ?",
                    ("early",),
                ).fetchone()["id"]
                store.set_entry_hidden(hidden, True)
                self.assertEqual(
                    store.export_download_name("csv", zone=EASTERN),
                    "06022024-Friday_Night.csv",
                )
                other = store.add_stream()
                store.add_many([_gift("other", "Cara", "2024-01-01T12:00:00Z")])
                store.switch_stream(store.stream_tabs()["tabs"][0]["id"])
                self.assertNotEqual(store.stream_tabs()["active_id"], other)
                self.assertEqual(
                    store.export_download_name("jsonl", zone=EASTERN),
                    "06022024-Friday_Night.jsonl",
                )
                self.assertEqual(
                    store.export_download_name("csv", zone=EASTERN, query="nobody"),
                    "Friday_Night.csv",
                )
            finally:
                store.close()
