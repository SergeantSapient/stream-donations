"""Streamer presets stay shared and are not copied onto a ledger."""

import json
import tempfile
import unittest
from pathlib import Path

from streamdonations.presets import (
    MAX_PRESETS,
    find_preset,
    ledger_settings,
    parse_presets,
    remove_preset,
    upsert_preset,
)
from streamdonations.store import Store


YOUTUBE = "https://www.youtube.com/@presetstreamer"
RUMBLE = "https://rumble.com/c/PresetStreamer"
KICK = "https://kick.com/presetstreamer"


class PresetTests(unittest.TestCase):
    def test_save_replaces_the_same_name_and_rejects_a_video(self):
        presets, saved = upsert_preset([], "  Ada   Bee ", YOUTUBE, "", KICK)
        self.assertEqual(saved["name"], "Ada Bee")
        self.assertEqual(saved["youtube_channel"], YOUTUBE)
        self.assertEqual(saved["rumble_channel"], "")
        self.assertEqual(saved["kick_watch"], KICK)
        self.assertEqual(len(saved["id"]), 32)
        self.assertIs(saved["tiers_enabled"], False)
        self.assertEqual(len(saved["goal_tiers"]), 5)
        self.assertFalse(any(tier["name"] for tier in saved["goal_tiers"]))

        presets, again = upsert_preset(presets, "ada bee", "", RUMBLE, "")
        self.assertEqual(len(presets), 1)
        self.assertEqual(again["id"], saved["id"])
        self.assertEqual(again["name"], "ada bee")
        self.assertEqual(again["youtube_channel"], "")
        self.assertEqual(again["rumble_channel"], RUMBLE)
        self.assertEqual(again["kick_watch"], "")
        self.assertIs(again["tiers_enabled"], False)

        with self.assertRaisesRegex(ValueError, "video"):
            upsert_preset(presets, "Ada", "https://www.youtube.com/watch?v=abcdefghijk", "", "")
        with self.assertRaisesRegex(ValueError, "video"):
            upsert_preset(presets, "Ada", "", "446413572", "")
        with self.assertRaisesRegex(ValueError, "owner API"):
            upsert_preset(
                presets,
                "Ada",
                "",
                "https://rumble.com/-livestream-api/get-data?key=SECRET",
                "",
            )
        with self.assertRaisesRegex(ValueError, "Name the streamer"):
            upsert_preset(presets, "   ", YOUTUBE, "", "")
        with self.assertRaisesRegex(ValueError, "at least one"):
            upsert_preset([], "Ada", "", "", "")
        self.assertEqual(presets[0]["youtube_channel"], "")
        self.assertEqual(presets[0]["rumble_channel"], RUMBLE)

    def test_ledger_settings_match_the_address_dialog(self):
        bronze = [{"enabled": True, "amount": 25, "name": "NightGoal", "bonus": "map"}]
        presets, saved = upsert_preset([], "Ada", YOUTUBE, "", KICK, True, bronze, "script-1")
        body = ledger_settings(find_preset(presets, saved["id"]))
        self.assertEqual(body["youtube_channel"], YOUTUBE)
        self.assertIs(body["youtube_follow_live"], True)
        self.assertNotIn("youtube_watch", body)
        self.assertEqual(body["rumble_watch"], "")
        self.assertIs(body["rumble_follow_live"], False)
        self.assertNotIn("rumble_channel", body)
        self.assertEqual(body["kick_watch"], KICK)
        self.assertIs(body["tiers_enabled"], True)
        self.assertEqual(body["goal_tiers"][0]["name"], "NightGoal")
        self.assertEqual(body["blue_script_id"], "script-1")
        self.assertNotIn("kick_follow_live", body)
        with self.assertRaises(ValueError):
            find_preset(presets, "missing")

    def test_limit_and_broken_document(self):
        presets = []
        for index in range(MAX_PRESETS):
            presets, _saved = upsert_preset(presets, f"Streamer {index}", "", "", f"channel{index}")
        with self.assertRaisesRegex(ValueError, "40"):
            upsert_preset(presets, "One more", YOUTUBE, "", "")
        presets, replaced = upsert_preset(presets, "streamer 0", YOUTUBE, "", "")
        self.assertEqual(len(presets), MAX_PRESETS)
        self.assertEqual(replaced["youtube_channel"], YOUTUBE)

        self.assertEqual(parse_presets(""), [])
        self.assertEqual(parse_presets("{"), [])
        self.assertEqual(parse_presets(json.dumps({"presets": []})), [])
        kept = parse_presets(json.dumps([
            {"id": "ok-1", "name": "Ada", "youtube_channel": YOUTUBE},
            {"id": "bad id", "name": "No", "kick_watch": KICK},
            "nope",
        ]))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["kick_watch"], "")
        self.assertIs(kept[0]["tiers_enabled"], False)
        self.assertEqual(len(kept[0]["goal_tiers"]), 5)
        remaining, removed = remove_preset(kept, "ok-1")
        self.assertEqual(removed["name"], "Ada")
        self.assertEqual(remaining, [])
        with self.assertRaisesRegex(ValueError, "not saved"):
            remove_preset(remaining, "ok-1")

    def test_goals_travel_with_the_preset_and_a_missing_list_stays_empty(self):
        bronze = [{"enabled": True, "amount": 25, "name": "Bronze", "bonus": "map"}]
        presets, saved = upsert_preset([], "Ada", YOUTUBE, "", "", "0", None)
        self.assertIs(saved["tiers_enabled"], False)

        presets, replaced = upsert_preset(presets, "ada", YOUTUBE, "", "", "1", bronze)
        self.assertEqual(replaced["id"], saved["id"])
        self.assertIs(replaced["tiers_enabled"], True)
        self.assertEqual(replaced["goal_tiers"][0]["name"], "Bronze")
        self.assertEqual(replaced["goal_tiers"][0]["amount"], 25.0)
        self.assertEqual(len(replaced["goal_tiers"]), 5)

        presets, cleared = upsert_preset(presets, "Ada", "", "", KICK)
        self.assertEqual(cleared["id"], saved["id"])
        self.assertIs(cleared["tiers_enabled"], False)
        self.assertTrue(all(not tier["name"] and not tier["enabled"] for tier in cleared["goal_tiers"]))

        with self.assertRaisesRegex(ValueError, "at least one"):
            upsert_preset([], "Goals only", "", "", "", True, bronze)
        with self.assertRaisesRegex(ValueError, "list"):
            upsert_preset([], "Ada", YOUTUBE, "", "", True, "Bronze")

        older = parse_presets(json.dumps([{
            "id": "ok-1",
            "name": "Ada",
            "youtube_channel": YOUTUBE,
            "tiers_enabled": "0",
            "goal_tiers": "not-a-list",
        }]))
        self.assertEqual(len(older), 1)
        self.assertIs(older[0]["tiers_enabled"], False)
        self.assertEqual(len(older[0]["goal_tiers"]), 5)
        self.assertFalse(any(tier["enabled"] for tier in older[0]["goal_tiers"]))


class PresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_presets_stay_shared_and_off_the_new_ledger(self):
        self.store.set_setting("youtube_watch", "abcdefghijk")
        saved = self.store.save_streamer_preset("Zed", YOUTUBE, RUMBLE, KICK)
        first = self.store.save_streamer_preset("Ada", "", "", "https://kick.com/xqc")
        listed = self.store.streamer_presets()
        self.assertEqual([item["name"] for item in listed], ["Ada", "Zed"])
        self.assertEqual(self.store.get_setting("youtube_channel"), "")
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")
        self.assertNotIn(YOUTUBE, self.store.get_setting("streams"))

        second = self.store.add_stream()
        self.assertEqual(self.store.get_setting("youtube_channel"), "")
        self.assertEqual(self.store.get_setting("youtube_watch"), "")
        self.assertEqual(self.store.get_setting("kick_watch"), "")
        self.assertEqual(len(self.store.streamer_presets()), 2)
        self.store.switch_stream(self.store.stream_tabs()["tabs"][0]["id"])
        self.assertNotEqual(self.store.stream_tabs()["active_id"], second)
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")
        self.assertEqual(self.store.streamer_presets()[1]["id"], saved["id"])

        self.assertEqual(self.store.delete_streamer_preset(first["id"]), "Ada")
        self.assertEqual(len(self.store.streamer_presets()), 1)
        self.store.delete_streamer_preset(saved["id"])
        self.assertEqual(self.store.get_setting("streamer_presets"), "")
        self.assertEqual(self.store.streamer_presets(), [])

    def test_saving_a_preset_does_not_change_the_open_ledgers_goals(self):
        self.store.set_setting("tiers_enabled", "1")
        self.store.set_setting("goal_tiers", '[{"enabled":true,"amount":10,"name":"Keep","bonus":""}]')
        bronze = [{"enabled": True, "amount": 25, "name": "NightGoal", "bonus": "map"}]
        saved = self.store.save_streamer_preset("Ada", YOUTUBE, "", "", True, bronze)
        self.assertIs(saved["tiers_enabled"], True)
        self.assertEqual(saved["goal_tiers"][0]["name"], "NightGoal")
        self.assertEqual(self.store.get_setting("tiers_enabled"), "1")
        self.assertIn("Keep", self.store.get_setting("goal_tiers"))
        self.assertNotIn("NightGoal", self.store.get_setting("goal_tiers"))
        self.assertNotIn("NightGoal", self.store.get_setting("streams"))

        cleared = self.store.save_streamer_preset("ada", YOUTUBE, "", "")
        self.assertEqual(cleared["id"], saved["id"])
        self.assertIs(cleared["tiers_enabled"], False)
        self.assertTrue(all(not tier["name"] for tier in cleared["goal_tiers"]))
        self.assertIn("Keep", self.store.get_setting("goal_tiers"))
        self.assertEqual(self.store.get_setting("tiers_enabled"), "1")
