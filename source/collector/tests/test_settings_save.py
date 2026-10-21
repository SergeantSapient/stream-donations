"""Saving settings writes them, and the open ledger's copy, in one transaction."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamdonations.service import Service
from streamdonations.store import Store
from streamdonations.transport import HttpResult


class _Transport:
    def request(self, _method, url, **_kwargs):
        return HttpResult(200, {}, b"{}", url)


class _CountingConnection:
    """Wraps the store's connection to count commits."""

    def __init__(self, conn):
        self._conn = conn
        self.commits = 0

    def commit(self):
        self.commits += 1
        return self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


_PAYLOAD = {
    "kick_watch": "https://kick.com/presetstreamer",
    "youtube_channel": "https://www.youtube.com/@presetstreamer",
    "youtube_follow_live": True,
    "rumble_watch": "",
    "rumble_follow_live": False,
    "tiers_enabled": True,
    "goal_tiers": [{"enabled": True, "amount": 25, "name": "NightGoal", "bonus": "map"}],
    "membership_price": "4.99",
    "ledger_page_size": 25,
}

_KEYS = (
    "kick_watch", "youtube_channel", "youtube_follow_live", "rumble_watch",
    "rumble_follow_live", "tiers_enabled", "goal_tiers", "membership_price", "ledger_page_size",
)


class SettingsSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.store.stream_tabs()
        self.store.set_setting("rumble_watch", "https://rumble.com/v4abcde-some-show.html")
        self.service = Service(self.store, _Transport())

    def tearDown(self):
        self.service.stop()
        self.store.close()
        self.tmp.cleanup()

    def _snapshot(self, store):
        values = {key: store.get_setting(key) for key in _KEYS}
        return values, self._ledger_copy(store)

    def _ledger_copy(self, store):
        doc = json.loads(store.get_setting("streams"))
        return next(item for item in doc["tabs"] if item["id"] == doc["active_id"])

    def test_one_commit_and_one_permission_pass_for_a_whole_save(self):
        counting = _CountingConnection(self.store._conn)
        self.store._conn = counting
        real_chmod = os.chmod
        chmods = []
        with mock.patch("os.chmod", side_effect=lambda *a, **k: (chmods.append(a[0]), real_chmod(*a, **k))):
            self.service.apply_settings(_PAYLOAD)
        self.assertEqual(counting.commits, 1)
        # The database and its WAL files, once each.
        self.assertLessEqual(len(chmods), 3)
        self.assertEqual(len(chmods), len(set(map(str, chmods))))

    def test_the_result_matches_saving_one_setting_at_a_time(self):
        self.service.apply_settings(_PAYLOAD)
        batched = self._snapshot(self.store)

        other = Store(Path(self.tmp.name) / "one-by-one.db")
        self.addCleanup(other.close)
        other.stream_tabs()
        other.set_setting("rumble_watch", "https://rumble.com/v4abcde-some-show.html")
        service = Service(other, _Transport())
        self.addCleanup(service.stop)
        with mock.patch.object(
            Store,
            "set_settings",
            lambda store, updates, clears=(): (
                [store.set_setting(key, value) for key, value in updates.items()],
                [store.delete_setting(key) for key in clears],
            ),
        ):
            service.apply_settings(_PAYLOAD)
        self.assertEqual(batched, self._snapshot(other))
        values, tab = batched
        self.assertEqual(values["rumble_watch"], "")
        self.assertEqual(tab["rumble_watch"], "")
        self.assertEqual(tab["kick_watch"], "https://kick.com/presetstreamer")
        self.assertIn("NightGoal", tab["goal_tiers"])

    def test_a_failure_part_way_saves_nothing(self):
        before = self._snapshot(self.store)
        self.store._conn.execute(
            "CREATE TRIGGER refuse_goals BEFORE INSERT ON settings WHEN NEW.key = 'goal_tiers' "
            "BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
        self.store._conn.commit()
        with self.assertRaises(sqlite3.DatabaseError):
            self.service.apply_settings(_PAYLOAD)
        self.assertEqual(self._snapshot(self.store), before)
        # The connection is usable afterwards.
        self.store.set_setting("chat_extra", "still works")
        self.assertEqual(self.store.get_setting("chat_extra"), "still works")

    def test_a_bad_value_is_refused_before_anything_is_written(self):
        before = self._snapshot(self.store)
        with self.assertRaises(ValueError):
            self.service.apply_settings({**_PAYLOAD, "ledger_page_size": 9999})
        self.assertEqual(self._snapshot(self.store), before)

    def test_stream_title_is_cut_on_the_ledger_copy_only(self):
        title = "  " + "x" * 100
        self.store.set_settings({"stream_title": title})
        self.assertEqual(self.store.get_setting("stream_title"), title)
        self.assertEqual(self._ledger_copy(self.store)["stream_title"], "x" * 80)

    def test_an_empty_save_writes_nothing(self):
        counting = _CountingConnection(self.store._conn)
        self.store._conn = counting
        self.store.set_settings({}, [])
        self.assertEqual(counting.commits, 0)
        self.assertIn('"active_id"', self.store.get_setting("streams"))
        json.loads(self.store.get_setting("streams"))


if __name__ == "__main__":
    unittest.main()
