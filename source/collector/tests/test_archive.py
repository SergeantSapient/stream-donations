"""Archive ledger copies the open ledger into archive.db and closes it."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from streamdonations.archive import (
    ARCHIVE_NAME,
    _read_only_uri,
    read_archive_groups,
    read_archived_document,
    write_archived_ledger,
)
from streamdonations.normalize import Donation
from streamdonations.store import _SCHEMA, Store


def _gift(
    key: str,
    author: str,
    created: str = "2024-06-01T00:00:00Z",
    amount: float = 5,
    raw: dict | None = None,
) -> Donation:
    return Donation(
        dedupe_key=key,
        platform="youtube",
        kind="super_chat",
        external_id=key,
        channel="",
        author_name=author,
        author_id="",
        message="hello",
        amount_value=amount,
        amount_micros=int(round(amount * 1_000_000)),
        currency="USD",
        created_at=created,
        raw={"kept": True} if raw is None else raw,
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
        if os.name != "nt":  # Windows has no Unix permission bits.
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

    def test_entries_count_every_stored_row_and_archive_by_id_leaves_the_open_ledger(self):
        self.assertEqual(self.store.stream_tabs()["tabs"][0]["entries"], 0)
        self.store.add_many([_gift("youtube:super_chat:open", "Ada"), _gift("youtube:super_chat:hidden", "Bea")])
        hidden_id = self.store._conn.execute(
            "SELECT id FROM donations WHERE dedupe_key = ?",
            ("youtube:super_chat:hidden",),
        ).fetchone()["id"]
        self.store.set_entry_hidden(hidden_id, True)
        open_id = self.store.stream_tabs()["active_id"]
        self.assertEqual(self.store.stream_tabs()["tabs"][0]["entries"], 2)
        other = self.store.add_stream()
        self.store.add_many([_gift("youtube:super_chat:other", "Cara")])
        self.store.switch_stream(open_id)
        tabs = {tab["id"]: tab["entries"] for tab in self.store.stream_tabs()["tabs"]}
        self.assertEqual(tabs, {open_id: 2, other: 1})

        saved = self.store.archive_open_ledger(other)

        self.assertEqual(saved, {"count": 1, "name": ARCHIVE_NAME})
        live = self.store.stream_tabs()
        self.assertEqual(live["active_id"], open_id)
        self.assertEqual([tab["id"] for tab in live["tabs"]], [open_id])
        self.assertEqual(live["tabs"][0]["entries"], 2)
        with self.assertRaisesRegex(ValueError, "Unknown stream tab."):
            self.store.archive_open_ledger("missing")

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

    def test_groups_list_stream_names_and_dates(self):
        from datetime import timezone

        archive = Path(self.tmp.name) / ARCHIVE_NAME
        self.assertEqual(
            read_archive_groups(archive, membership_price=None, rates=None),
            {"groups": []},
        )
        self.store.archive_open_ledger()
        self.store.set_setting("stream_title", "Friday")
        self.store.add_many([
            _gift(
                "youtube:super_chat:day1",
                "@Ada",
                created="2024-06-01T12:00:00Z",
                amount=5,
                raw={"token": "SECRET-ARCHIVE-TOKEN"},
            ),
            _gift("youtube:super_chat:hidden-day", "Bea", created="2024-06-02T12:00:00Z", amount=3),
            _gift("youtube:super_chat:day2", "Cara", created="2024-06-02T18:00:00Z", amount=2),
        ])
        hidden_id = self.store._conn.execute(
            "SELECT id FROM donations WHERE dedupe_key = ?",
            ("youtube:super_chat:hidden-day",),
        ).fetchone()["id"]
        self.store.set_entry_hidden(hidden_id, True)
        self.store.archive_open_ledger()
        self.store.set_setting("stream_title", "Saturday")
        self.store.add_many([
            _gift("youtube:super_chat:saturday", "Dan", created="2024-06-03T08:00:00Z", amount=7),
        ])
        self.store.archive_open_ledger()

        connection = sqlite3.connect(archive)
        try:
            connection.row_factory = sqlite3.Row
            copied = dict(connection.execute("SELECT * FROM donations LIMIT 1").fetchone())
            columns = list(copied)
            copied["id"] = 9001
            copied["dedupe_key"] = "youtube:super_chat:orphan"
            copied["external_id"] = "orphan"
            copied["author_name"] = "Ora"
            copied["stream_id"] = "orphan-stream"
            copied["created_at"] = "2024-06-04T12:00:00Z"
            copied["amount_value"] = 4
            copied["amount_micros"] = 4_000_000
            copied["hidden"] = 0
            copied["raw_json"] = "{}"
            placeholders = ", ".join("?" for _ in columns)
            connection.execute(
                f"INSERT INTO donations ({', '.join(columns)}) VALUES ({placeholders})",
                [copied[name] for name in columns],
            )
            connection.commit()
        finally:
            connection.close()

        view = read_archive_groups(
            archive,
            membership_price=4.99,
            rates={"USD": 1.0},
            zone=timezone.utc,
        )
        groups = view["groups"]
        self.assertEqual(
            [(group["title"], group["date"], group["count"], group["total_usd"]) for group in groups],
            [
                ("Saturday", "2024-06-03", 1, 7),
                ("Friday", "2024-06-01", 1, 5),
                ("Friday", "2024-06-02", 2, 2),
                ("Stream", "", 0, 0),
                ("Stream", "2024-06-04", 1, 4),
            ],
        )
        self.assertEqual(groups[0]["entries"][0]["author_name"], "Dan")
        self.assertEqual(groups[1]["entries"][0]["author_name"], "Ada")
        self.assertEqual([entry["author_name"] for entry in groups[2]["entries"]], ["Bea", "Cara"])
        self.assertTrue(groups[2]["entries"][0]["hidden"])
        self.assertEqual(groups[2]["entries"][0]["amount_usd"], 3)
        self.assertFalse(groups[2]["entries"][1]["hidden"])
        self.assertEqual(groups[3]["entries"], [])
        self.assertEqual(groups[4]["title"], "Stream")
        rendered = json.dumps(view)
        self.assertNotIn("SECRET-ARCHIVE-TOKEN", rendered)
        for entry in groups[1]["entries"]:
            self.assertNotIn("raw", entry)
            self.assertNotIn("author_id", entry)
            self.assertNotIn("dedupe_key", entry)
            self.assertNotIn("note", entry)
        _rows, total = self.store.list_donations()
        self.assertEqual(total, 0)

    def test_an_unreadable_archive_does_not_name_the_file(self):
        bad = Path(self.tmp.name) / "broken.db"
        bad.write_bytes(b"not a database")
        with self.assertRaises(ValueError) as caught:
            read_archive_groups(bad, membership_price=None, rates=None)
        self.assertEqual(str(caught.exception), "The archive could not be read.")
        self.assertNotIn(self.tmp.name, str(caught.exception))


class ArchivePathTests(unittest.TestCase):
    """A folder name with URI characters still opens read-only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _round_trip(self, folder_name: str) -> None:
        folder = Path(self.tmp.name) / folder_name
        folder.mkdir()
        path = folder / ARCHIVE_NAME
        tab = {"id": "tab-1", "stream_title": "Friday"}
        row = {
            "id": 1, "dedupe_key": "k", "platform": "youtube", "kind": "super_chat",
            "external_id": "k", "channel": "", "author_name": "Ada", "author_id": "",
            "message": "", "amount_value": 5, "amount_micros": 5_000_000, "currency": "USD",
            "created_at": "2024-06-01T00:00:00Z", "received_at": "2024-06-01T00:00:00Z",
            "processed": 0, "processed_at": None, "note": "", "raw_json": "{}", "hidden": 0,
            "stream_id": "tab-1",
        }
        self.assertEqual(write_archived_ledger(path, _SCHEMA, tab, [row]), 1)
        document = read_archived_document(path)
        self.assertEqual(document["active_id"], "tab-1")
        self.assertEqual([item["id"] for item in document["tabs"]], ["tab-1"])
        uri = _read_only_uri(path)
        self.assertTrue(uri.startswith("file:"))
        self.assertTrue(uri.endswith("?mode=ro"))
        connection = sqlite3.connect(uri, uri=True)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM donations").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM donations")
        finally:
            connection.close()

    def test_percent_hash_and_space_in_the_folder(self):
        self._round_trip("Super 100% #1 fan")

    @unittest.skipIf(os.name == "nt", "Windows file names cannot hold ?")
    def test_question_mark_in_the_folder(self):
        self._round_trip("what?mode=rw")
