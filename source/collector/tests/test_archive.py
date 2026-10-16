"""Archive ledger copies the open ledger into archive.db and closes it."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from streamdonations.archive import ARCHIVE_NAME, read_archived_document, write_archived_ledger
from streamdonations.normalize import Donation
from streamdonations.store import _SCHEMA, Store


def _gift(key: str, author: str) -> Donation:
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
        created_at="2024-06-01T00:00:00Z",
        raw={"kept": True},
    )


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_archive_copies_the_open_ledger_and_closes_it(self):
        self.store.add_many([_gift("youtube:super_chat:kept", "Ada"), _gift("youtube:super_chat:hidden", "Bea")])
        hidden_id = self.store._conn.execute(
            "SELECT id FROM donations WHERE dedupe_key = ?",
            ("youtube:super_chat:hidden",),
        ).fetchone()["id"]
        self.store.set_entry_hidden(hidden_id, True)
        self.store.set_setting("stream_title", "Friday")
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("youtube_client_secret", "SECRET-MARKER")
        self.store.set_setting("rumble_api_url", "RUMBLE-URL-MARKER")
        self.store.set_setting("fx_response_json", "FX-MARKER")
        self.store.set_setting("streamer_presets", "PRESET-MARKER")
        self.store.set_setting("blue_scripts", "BLUE-SOURCE-MARKER")
        first = self.store.stream_tabs()["active_id"]
        second = self.store.add_stream()
        self.store.add_many([_gift("youtube:super_chat:other", "Cara")])
        self.store.switch_stream(first)

        saved = self.store.archive_open_ledger()

        self.assertEqual(saved, {"count": 2, "name": ARCHIVE_NAME})
        live = self.store.stream_tabs()
        self.assertEqual(live["active_id"], second)
        self.assertEqual([tab["id"] for tab in live["tabs"]], [second])
        rows, total = self.store.list_donations()
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["author_name"], "Cara")
        self.assertEqual(self.store.get_setting("youtube_client_secret"), "SECRET-MARKER")
        self.assertEqual(self.store.get_setting("blue_scripts"), "BLUE-SOURCE-MARKER")

        archive = Path(self.tmp.name) / ARCHIVE_NAME
        self.assertEqual(os.stat(archive).st_mode & 0o777, 0o600)
        blob = archive.read_bytes()
        for marker in ("SECRET-MARKER", "RUMBLE-URL-MARKER", "FX-MARKER", "PRESET-MARKER", "BLUE-SOURCE-MARKER"):
            self.assertNotIn(marker.encode(), blob)
        document = read_archived_document(archive)
        self.assertEqual(document["active_id"], first)
        self.assertEqual([tab["id"] for tab in document["tabs"]], [first])
        self.assertEqual(document["tabs"][0]["stream_title"], "Friday")
        self.assertEqual(document["tabs"][0]["youtube_watch"], "abcdefghijk")
        self.assertTrue(document["tabs"][0]["archived_at"])
        connection = sqlite3.connect(f"file:{archive}?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            copied = connection.execute(
                "SELECT author_name, hidden, stream_id FROM donations ORDER BY id"
            ).fetchall()
            keys = [row["key"] for row in connection.execute("SELECT key FROM settings")]
        finally:
            connection.close()
        self.assertEqual([(row["author_name"], row["hidden"], row["stream_id"]) for row in copied], [
            ("Ada", 0, first),
            ("Bea", 1, first),
        ])
        self.assertEqual(keys, ["streams"])
        messages = [entry["message"] for entry in self.store.recent_log()]
        self.assertIn("Saved 2 donations to the archive and closed that ledger.", messages)
        self.assertNotIn("Friday", "\n".join(messages))

    def test_the_last_ledger_is_archived_and_a_blank_one_opens(self):
        self.store.add_many([_gift("youtube:super_chat:only", "Ada")])
        self.store.set_setting("stream_title", "Friday")
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("youtube_client_secret", "SECRET-MARKER")
        only = self.store.stream_tabs()["active_id"]
        saved = self.store.archive_open_ledger()
        self.assertEqual(saved, {"count": 1, "name": ARCHIVE_NAME})
        fresh = self.store.stream_tabs()
        self.assertEqual(len(fresh["tabs"]), 1)
        self.assertNotEqual(fresh["active_id"], only)
        self.assertEqual(fresh["tabs"][0]["title"], "Stream")
        self.assertTrue(fresh["tabs"][0]["paused"])
        self.assertEqual(self.store.get_setting("stream_title"), "")
        self.assertEqual(self.store.get_setting("youtube_watch"), "")
        self.assertEqual(self.store.get_setting("youtube_client_secret"), "SECRET-MARKER")
        _rows, total = self.store.list_donations()
        self.assertEqual(total, 0)
        document = read_archived_document(Path(self.tmp.name) / ARCHIVE_NAME)
        self.assertEqual(document["active_id"], only)
        self.assertEqual(document["tabs"][0]["stream_title"], "Friday")
        self.assertEqual(document["tabs"][0]["youtube_watch"], "abcdefghijk")

    def test_a_second_ledger_is_added_to_the_same_database(self):
        self.store.add_many([_gift("youtube:super_chat:first", "Ada")])
        first = self.store.stream_tabs()["active_id"]
        self.store.add_stream()
        self.store.switch_stream(first)
        self.store.archive_open_ledger()
        self.store.add_many([_gift("youtube:super_chat:second", "Bea")])
        second = self.store.stream_tabs()["active_id"]
        self.store.add_stream()
        self.store.switch_stream(second)
        saved = self.store.archive_open_ledger()
        self.assertEqual(saved["count"], 1)
        document = read_archived_document(Path(self.tmp.name) / ARCHIVE_NAME)
        self.assertEqual([tab["id"] for tab in document["tabs"]], [first, second])
        self.assertEqual(document["active_id"], second)

    def test_saving_the_same_ledger_again_replaces_its_rows(self):
        path = Path(self.tmp.name) / ARCHIVE_NAME
        tab = {"id": "stream-1", "stream_title": "Friday", "archived_at": "2024-06-01T00:00:00Z"}
        row = {
            "id": 4,
            "dedupe_key": "youtube:super_chat:kept",
            "platform": "youtube",
            "kind": "super_chat",
            "external_id": "kept",
            "channel": "",
            "author_name": "Ada",
            "author_id": "",
            "message": "hello",
            "amount_value": 5,
            "amount_micros": 5_000_000,
            "currency": "USD",
            "created_at": "2024-06-01T00:00:00Z",
            "received_at": "2024-06-01T00:00:01Z",
            "processed": 0,
            "processed_at": None,
            "note": "",
            "raw_json": "{}",
            "hidden": 0,
            "stream_id": "stream-1",
        }
        self.assertEqual(write_archived_ledger(path, _SCHEMA, tab, [row, {**row, "id": 5, "dedupe_key": "other"}]), 2)
        replaced = {**row, "author_name": "Bea", "hidden": 1}
        self.assertEqual(write_archived_ledger(path, _SCHEMA, tab, [replaced]), 1)
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            names = [item[0] for item in connection.execute("SELECT author_name FROM donations")]
        finally:
            connection.close()
        self.assertEqual(names, ["Bea"])
        document = read_archived_document(path)
        self.assertEqual(json.loads(json.dumps(document["tabs"][0]["stream_title"])), "Friday")
