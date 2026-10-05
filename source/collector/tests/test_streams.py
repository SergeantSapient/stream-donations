"""Each ledger keeps its own donations, title, and platform addresses."""

import json
import tempfile
import unittest
from pathlib import Path

from streamdonations.money import parse_membership_price
from streamdonations.normalize import Donation
from streamdonations.store import Store

TIERS = '[{"enabled":true,"amount":25,"name":"Bronze","bonus":"map"}]'


class StreamTabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _price(self):
        return parse_membership_price(self.store.get_setting("membership_price"))

    def _report(self):
        return self.store.stream_report({"USD": 1.0}, self._price())

    def _quoted(self):
        rows, total = self.store.list_donations(membership_price=self._price(), rates={"USD": 1.0})
        self.assertEqual(total, 1)
        return rows[0]["amount_usd"]

    def test_tabs_keep_distinct_counts_and_settings(self):
        self.store.add_many([
            Donation(
                dedupe_key="youtube:membership:1",
                platform="youtube",
                kind="membership",
                external_id="1",
                channel="",
                author_name="Ada",
                author_id="",
                message="",
                amount_value=None,
                amount_micros=None,
                currency=None,
                created_at="2024-06-01T00:00:00Z",
                raw={},
            )
        ])
        self.store.set_setting("membership_price", "4.99")
        self.store.set_setting("stream_title", "Friday")
        self.store.set_setting("chat_extra", "thanks")
        self.store.set_setting("tiers_enabled", "1")
        self.store.set_setting("show_top_donors", "0")
        self.store.set_setting("goal_tiers", TIERS)
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("youtube_client_id", "client-1")
        self.store.set_stream_started("2024-01-01T00:00:00Z")

        report = self._report()
        self.assertEqual(report["total_usd"], 4.99)
        self.assertEqual(report["scope"], "ledger")
        self.assertEqual(report["title"], "Friday")
        self.assertEqual(report["show_top_donors"], False)
        self.assertEqual(self._quoted(), 4.99)

        second = self.store.add_stream()
        fresh = self._report()
        self.assertEqual(fresh["total_usd"], 0)
        self.assertEqual(fresh["count"], 0)
        self.assertEqual(fresh["scope"], "ledger")
        self.assertEqual(fresh["title"], "")
        self.assertNotEqual(fresh["started_at"], "2024-01-01T00:00:00Z")
        self.assertEqual(self.store.get_setting("youtube_watch"), "")
        self.assertEqual(self.store.get_setting("youtube_client_id"), "client-1")
        self.assertEqual(self.store.get_setting("membership_price"), "4.99")
        self.assertEqual(self.store.get_setting("chat_extra"), "thanks")
        self.assertEqual(self.store.get_setting("goal_tiers"), "")
        self.assertEqual(self.store.get_setting("tiers_enabled"), "0")
        self.assertEqual(self.store.get_setting("show_top_donors"), "0")
        empty, empty_total = self.store.list_donations(membership_price=self._price(), rates={"USD": 1.0})
        self.assertEqual(empty_total, 0)
        self.assertEqual(empty, [])

        self.store.set_setting("membership_price", "10.00")
        self.store.set_setting("chat_extra", "only here")
        self.store.set_setting("stream_title", "Saturday")
        self.store.set_setting("show_top_donors", "1")
        self.store.set_setting("youtube_watch", "zzzzzzzzzzz")
        self.assertEqual(self.store.list_donations()[1], 0)
        self.assertEqual(self._report()["total_usd"], 0)

        doc = json.loads(self.store.get_setting("streams"))
        first = next(tab["id"] for tab in doc["tabs"] if tab["id"] != second)
        saved = next(tab for tab in doc["tabs"] if tab["id"] == first)
        self.assertEqual(saved["stream_started_at"], "2024-01-01T00:00:00Z")
        self.assertEqual(saved["membership_price"], "4.99")
        self.assertEqual(saved["chat_extra"], "thanks")
        self.assertEqual(saved["goal_tiers"], TIERS)
        self.assertEqual(saved["tiers_enabled"], "1")
        self.assertEqual(saved["stream_title"], "Friday")
        self.assertEqual(saved["show_top_donors"], "0")
        self.assertEqual(saved["youtube_watch"], "abcdefghijk")

        self.store.switch_stream(first)
        restored = self._report()
        self.assertEqual(restored["total_usd"], 4.99)
        self.assertEqual(restored["started_at"], "2024-01-01T00:00:00Z")
        self.assertEqual(restored["title"], "Friday")
        self.assertEqual(restored["chat_extra"], "thanks")
        self.assertEqual(restored["show_top_donors"], False)
        self.assertEqual(self.store.get_setting("membership_price"), "4.99")
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")
        self.assertEqual(self.store.get_setting("youtube_client_id"), "client-1")
        self.assertEqual(self.store.get_setting("goal_tiers"), TIERS)
        self.assertEqual(self.store.get_setting("tiers_enabled"), "1")
        self.assertEqual(self._quoted(), 4.99)

        self.store.switch_stream(second)
        self.assertEqual(self.store.get_setting("membership_price"), "10.00")
        self.assertEqual(self.store.get_setting("chat_extra"), "only here")
        self.assertEqual(self.store.get_setting("youtube_watch"), "zzzzzzzzzzz")
        self.assertEqual(self.store.get_setting("goal_tiers"), "")
        self.assertEqual(self.store.get_setting("tiers_enabled"), "0")
        self.assertEqual(self.store.get_setting("youtube_client_id"), "client-1")
        self.assertEqual(self._report()["title"], "Saturday")
        self.assertEqual(self.store.list_donations()[1], 0)
        self.assertEqual(self._report()["total_usd"], 0)

    def test_stream_total_includes_donations_from_before_the_saved_start(self):
        self.store.add_many([
            Donation(
                dedupe_key="youtube:super_chat:old",
                platform="youtube",
                kind="super_chat",
                external_id="old",
                channel="",
                author_name="Ada",
                author_id="",
                message="",
                amount_value=5,
                amount_micros=5_000_000,
                currency="USD",
                created_at="2020-01-01T00:00:00Z",
                raw={},
            )
        ])
        self.store.set_stream_started("2024-06-01T00:00:00Z")
        report = self._report()
        self.assertEqual(report["scope"], "ledger")
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["total_usd"], 5)

    def test_reset_and_full_ledger_touch_only_the_open_tab(self):
        self.store.set_stream_started("2020-01-01T00:00:00Z")
        second = self.store.add_stream()
        self.store.set_stream_started(None)
        self.assertEqual(self.store.get_setting("stream_started_at"), "")
        doc = json.loads(self.store.get_setting("streams"))
        first = next(tab["id"] for tab in doc["tabs"] if tab["id"] != second)
        self.assertEqual(
            next(tab for tab in doc["tabs"] if tab["id"] == first)["stream_started_at"],
            "2020-01-01T00:00:00Z",
        )
        self.store.switch_stream(first)
        self.assertEqual(self.store.get_setting("stream_started_at"), "2020-01-01T00:00:00Z")

    def test_close_and_rename_limits(self):
        tabs = self.store.stream_tabs()
        with self.assertRaisesRegex(ValueError, "The last stream tab stays."):
            self.store.close_stream(tabs["active_id"])
        with self.assertRaisesRegex(ValueError, "Stream name is limited to 80 characters."):
            self.store.rename_stream("n" * 81)
        self.store.rename_stream("  Night  ")
        self.assertEqual(self.store.stream_tabs()["tabs"][0]["title"], "Night")
        self.assertEqual(self.store.get_setting("stream_title"), "Night")

        self.store.set_setting("membership_price", "3.00")
        second = self.store.add_stream()
        self.store.set_setting("membership_price", "4.00")
        first = next(tab["id"] for tab in self.store.stream_tabs()["tabs"] if tab["id"] != second)
        self.store.switch_stream(first)
        self.store.close_stream(second)
        self.assertEqual(self.store.stream_tabs()["active_id"], first)
        self.assertEqual(len(self.store.stream_tabs()["tabs"]), 1)
        self.assertEqual(self.store.get_setting("membership_price"), "3.00")
        with self.assertRaisesRegex(ValueError, "Unknown stream tab."):
            self.store.switch_stream("nope")

    def test_bad_stream_document_rebuilds_from_the_open_settings(self):
        self.store.set_setting("membership_price", "4.99")
        self.store.set_setting("stream_started_at", "2024-01-01T00:00:00Z")
        self.store.set_setting("streams", "not-json")
        tabs = self.store.stream_tabs()
        self.assertEqual(len(tabs["tabs"]), 1)
        self.assertEqual(tabs["tabs"][0]["title"], "Stream")
        self.assertEqual(self.store.get_setting("membership_price"), "4.99")
        self.assertEqual(self.store.get_setting("stream_started_at"), "2024-01-01T00:00:00Z")

    def test_close_and_reset_touch_only_that_ledger(self):
        self.store.add_many([
            Donation(
                dedupe_key="youtube:super_chat:ada",
                platform="youtube",
                kind="super_chat",
                external_id="ada",
                channel="",
                author_name="Ada",
                author_id="",
                message="",
                amount_value=5,
                amount_micros=5_000_000,
                currency="USD",
                created_at="2024-06-01T00:00:00Z",
                raw={},
            )
        ])
        first_id = self.store.stream_tabs()["active_id"]
        ada_id = self.store.list_donations()[0][0]["id"]
        second = self.store.add_stream()
        self.store.add_manual_entry(
            author_name="Bea",
            platform="other",
            kind="donation",
            amount=2,
            currency="USD",
        )
        bea_id = self.store.list_donations()[0][0]["id"]
        self.assertIsNone(self.store.get_donation(ada_id))
        self.assertFalse(self.store.delete_manual_entry(ada_id))
        self.store.switch_stream(first_id)
        self.assertIsNone(self.store.get_donation(bea_id))
        self.assertEqual(self.store.list_donations()[1], 1)
        self.assertEqual(self.store.clear_donations(), 1)
        self.assertEqual(self.store.list_donations()[1], 0)
        self.store.switch_stream(second)
        self.assertEqual(self.store.list_donations()[1], 1)
        self.assertEqual(self.store.get_donation(bea_id)["author_name"], "Bea")
        self.store.switch_stream(first_id)
        self.store.close_stream(second)
        self.assertIsNone(self.store.get_donation(bea_id))
        self.assertEqual(self.store.summary()["total"], 0)

    def test_older_tab_document_keeps_the_open_watch_url(self):
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("rumble_api_url", "https://rumble.com/-livestream-api/get-data?key=SECRET")
        self.store.set_setting("youtube_client_id", "client-1")
        second = self.store.add_stream()
        self.store.set_setting("youtube_watch", "zzzzzzzzzzz")
        doc = json.loads(self.store.get_setting("streams"))
        for tab in doc["tabs"]:
            for key in ("youtube_watch", "rumble_watch", "kick_watch"):
                tab.pop(key, None)
        self.store.set_setting("streams", json.dumps(doc))
        self.store.stream_tabs()
        saved = json.loads(self.store.get_setting("streams"))
        self.assertEqual(saved["active_id"], second)
        self.assertTrue(all(tab["youtube_watch"] == "zzzzzzzzzzz" for tab in saved["tabs"]))
        self.assertEqual(self.store.get_setting("youtube_watch"), "zzzzzzzzzzz")
        self.assertEqual(self.store.get_setting("youtube_client_id"), "client-1")
        self.assertEqual(
            self.store.get_setting("rumble_api_url"),
            "https://rumble.com/-livestream-api/get-data?key=SECRET",
        )
        self.store.set_setting("youtube_watch", "yyyyyyyyyyy")
        first = next(tab["id"] for tab in saved["tabs"] if tab["id"] != second)
        self.store.switch_stream(first)
        self.assertEqual(self.store.get_setting("youtube_watch"), "zzzzzzzzzzz")
        self.assertEqual(
            self.store.get_setting("rumble_api_url"),
            "https://rumble.com/-livestream-api/get-data?key=SECRET",
        )
        self.assertEqual(self.store.get_setting("youtube_client_id"), "client-1")
        self.store.switch_stream(second)
        self.assertEqual(self.store.get_setting("youtube_watch"), "yyyyyyyyyyy")

    def test_follow_live_stays_on_its_ledger(self):
        first = self.store.stream_tabs()["active_id"]
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("youtube_channel", "https://www.youtube.com/@one")
        self.store.set_setting("youtube_follow_live", "1")
        self.store.set_setting("rumble_channel", "https://rumble.com/c/One")
        self.store.set_setting("kick_follow_live", "1")
        second = self.store.add_stream()
        self.assertEqual(self.store.get_setting("youtube_watch"), "")
        self.assertEqual(self.store.get_setting("youtube_channel"), "")
        self.assertNotEqual(self.store.get_setting("youtube_follow_live"), "1")
        self.assertEqual(self.store.get_setting("rumble_channel"), "")
        self.assertNotEqual(self.store.get_setting("kick_follow_live"), "1")
        self.store.set_setting("youtube_channel", "https://www.youtube.com/@two")
        self.store.set_setting("youtube_follow_live", "0")
        self.store.switch_stream(first)
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")
        self.assertEqual(self.store.get_setting("youtube_channel"), "https://www.youtube.com/@one")
        self.assertEqual(self.store.get_setting("youtube_follow_live"), "1")
        self.assertEqual(self.store.get_setting("rumble_channel"), "https://rumble.com/c/One")
        self.assertEqual(self.store.get_setting("kick_follow_live"), "1")
        self.store.switch_stream(second)
        self.assertEqual(self.store.get_setting("youtube_channel"), "https://www.youtube.com/@two")
        self.assertEqual(self.store.get_setting("youtube_follow_live"), "0")
        self.assertEqual(self.store.get_setting("youtube_watch"), "")

    def test_pause_defaults_on_and_stays_on_its_ledger(self):
        tabs = self.store.stream_tabs()
        self.assertTrue(tabs["tabs"][0]["paused"])
        self.assertEqual(self.store.get_setting("collection_paused"), "1")

        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_stream_paused(None, False)
        self.assertEqual(self.store.get_setting("collection_paused"), "0")
        self.assertFalse(self.store.stream_tabs()["tabs"][0]["paused"])

        second = self.store.add_stream()
        fresh = self.store.stream_tabs()
        self.assertEqual(fresh["active_id"], second)
        self.assertTrue(next(tab["paused"] for tab in fresh["tabs"] if tab["id"] == second))
        self.assertEqual(self.store.get_setting("collection_paused"), "1")
        self.assertEqual(self.store.get_setting("youtube_watch"), "")
        saved = json.loads(self.store.get_setting("streams"))
        first = next(tab for tab in saved["tabs"] if tab["id"] != second)
        self.assertEqual(first["collection_paused"], "0")
        self.assertEqual(first["youtube_watch"], "abcdefghijk")
        self.assertEqual(
            next(tab for tab in saved["tabs"] if tab["id"] == second)["collection_paused"],
            "1",
        )

        self.store.set_stream_paused(second, False)
        self.assertEqual(self.store.get_setting("collection_paused"), "0")
        self.store.set_stream_paused(first["id"], True)
        self.assertEqual(self.store.get_setting("collection_paused"), "0")
        self.assertFalse(self.store.collection_paused())
        paused_first = json.loads(self.store.get_setting("streams"))
        self.assertEqual(
            next(tab for tab in paused_first["tabs"] if tab["id"] == first["id"])["collection_paused"],
            "1",
        )

        self.store.switch_stream(first["id"])
        self.assertEqual(self.store.get_setting("collection_paused"), "1")
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")
        self.store.switch_stream(second)
        self.assertEqual(self.store.get_setting("collection_paused"), "0")
        self.assertEqual(self.store.get_setting("youtube_watch"), "")

    def test_older_ledger_without_a_pause_starts_paused(self):
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("collection_paused", "0")
        doc = json.loads(self.store.get_setting("streams"))
        for tab in doc["tabs"]:
            tab.pop("collection_paused", None)
        self.store.delete_setting("collection_paused")
        self.store.set_setting("streams", json.dumps(doc))
        tabs = self.store.stream_tabs()
        self.assertTrue(tabs["tabs"][0]["paused"])
        self.assertEqual(self.store.get_setting("collection_paused"), "1")
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")
