"""The settings file stays small, ignores what it does not understand, and carries no secrets."""

import json
import unittest

from streamdonations.settings_file import (
    KIND,
    MAX_BYTES,
    SECRET_KEYS,
    TAB_KEYS,
    VERSION,
    export_settings,
    parse_settings_file,
)


class SettingsFileTests(unittest.TestCase):
    def test_export_is_compact_and_drops_secrets(self):
        self.assertTrue(set(TAB_KEYS).isdisjoint(SECRET_KEYS))
        self.assertNotIn("verbose_logs", TAB_KEYS)
        self.assertNotIn("youtube_channel", TAB_KEYS)
        self.assertNotIn("youtube_follow_live", TAB_KEYS)
        self.assertNotIn("rumble_channel", TAB_KEYS)
        self.assertNotIn("kick_follow_live", TAB_KEYS)
        self.assertNotIn("streamer_presets", TAB_KEYS)
        self.assertNotIn("blue_scripts", TAB_KEYS)
        self.assertNotIn("blue_script_id", TAB_KEYS)
        self.assertNotIn("blue_goal_tier", TAB_KEYS)
        self.assertNotIn("collection_paused", TAB_KEYS)
        body = export_settings({
            "membership_price": "4.99",
            "tiers_enabled": True,
            "show_top_donors": True,
            "chat_extra": "café",
            "ledger_page_size": 40,
            "goal_tiers": [{"enabled": True, "amount": 25.0, "name": "Bronze", "bonus": ""}],
            "youtube_client_secret": "super-secret-value",
            "youtube_client_id": "client-id-value",
            "rumble_api_url": "https://rumble.com/-livestream-api/get-data?key=SECRETKEY",
            "stream_title": "Friday",
            "youtube_channel": "https://www.youtube.com/@samplechannel",
            "rumble_channel": "https://rumble.com/c/SampleNewsTV",
        })
        self.assertNotIn(b" ", body)
        self.assertNotIn(b"super-secret-value", body)
        self.assertNotIn(b"client-id-value", body)
        self.assertNotIn(b"SECRETKEY", body)
        self.assertNotIn(b"Friday", body)
        self.assertNotIn(b"youtube.com/@samplechannel", body)
        self.assertNotIn(b"rumble.com/c/SampleNewsTV", body)
        self.assertNotIn(b"\\u00e9", body)
        self.assertNotIn(b"tiers_enabled", body)
        self.assertNotIn(b"goal_tiers", body)
        self.assertNotIn(b"Bronze", body)
        self.assertTrue(body.startswith(b'{"kind":"stream-donations-settings","version":1,"settings":{'))
        parsed = parse_settings_file(body)
        self.assertEqual(parsed.version, VERSION)
        self.assertEqual(parsed.ignored, ())
        self.assertEqual(parsed.settings["chat_extra"], "café")
        self.assertEqual(list(parsed.settings), list(TAB_KEYS))

    def test_newer_file_keeps_known_keys_and_reports_the_rest(self):
        body = json.dumps({
            "kind": KIND,
            "version": 9,
            "packaging": "later",
            "settings": {
                "theme": "dark",
                "membership_price": "3.00",
                "youtube_client_secret": "nope",
                "tiers_enabled": True,
                "goal_tiers": [{"name": "Bronze"}],
            },
        }).encode("utf-8")
        parsed = parse_settings_file(b"\xef\xbb\xbf" + body)
        self.assertEqual(parsed.version, 9)
        self.assertEqual(parsed.settings, {"membership_price": "3.00"})
        self.assertEqual(
            parsed.ignored,
            ("goal_tiers", "packaging", "theme", "tiers_enabled", "youtube_client_secret"),
        )

    def test_missing_keys_are_not_filled_in(self):
        parsed = parse_settings_file(json.dumps({
            "kind": KIND,
            "version": 1,
            "settings": {"chat_extra": "only this"},
        }).encode("utf-8"))
        self.assertEqual(parsed.settings, {"chat_extra": "only this"})
        self.assertNotIn("membership_price", parsed.settings)
        self.assertNotIn("goal_tiers", parsed.settings)

    def test_present_empty_price_is_kept_so_it_can_clear(self):
        parsed = parse_settings_file(json.dumps({
            "kind": KIND,
            "version": 1,
            "settings": {"membership_price": None},
        }).encode("utf-8"))
        self.assertIn("membership_price", parsed.settings)
        self.assertIsNone(parsed.settings["membership_price"])

    def test_rejected_files(self):
        cases = [
            (b"", "Settings file was empty."),
            (b"[]", "Settings file must be an object."),
            (b"{", "Settings file was not JSON."),
            (b'{"kind":"ledger","version":1}', "This file is not a SuperBerry settings file."),
            (b'{"kind":"stream-donations-settings","version":0}', "Settings file version must be a whole number of 1 or higher."),
            (b'{"kind":"stream-donations-settings","version":true}', "Settings file version must be a whole number of 1 or higher."),
            (b'{"kind":"stream-donations-settings","version":1.5}', "Settings file version must be a whole number of 1 or higher."),
            (b'{"kind":"stream-donations-settings","version":"1"}', "Settings file version must be a whole number of 1 or higher."),
            (b'{"kind":"stream-donations-settings","version":1,"settings":[]}', "Settings in the file must be an object."),
            (b"x" * (MAX_BYTES + 1), "Settings file is too large."),
        ]
        for body, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(ValueError) as caught:
                    parse_settings_file(body)
                self.assertEqual(str(caught.exception), message)


if __name__ == "__main__":
    unittest.main()
