"""Player URLs for the stream panel. Resolvers are fakes, so nothing is fetched."""

import unittest

from streamdonations.embed import (
    allowed_player_url,
    kick_player_url,
    player_for_ledger,
    rumble_player_url,
    youtube_player_url,
)
from streamdonations.rumble import embed_to_chat_id
from streamdonations.util import ApiError


def _unused(_value):
    raise AssertionError("this lookup should not run")


def _player(platform, settings, **resolvers):
    base = {
        "youtube_video": _unused,
        "youtube_live": _unused,
        "rumble_chat": _unused,
        "rumble_live": _unused,
    }
    base.update(resolvers)
    return player_for_ledger(platform, settings, **base)


class PlayerUrlTests(unittest.TestCase):
    def test_youtube_and_rumble_ids_round_trip_locally(self):
        self.assertEqual(youtube_player_url("abcdefghijk"), "https://www.youtube.com/embed/abcdefghijk")
        self.assertEqual(rumble_player_url("9604851"), "https://rumble.com/embed/v5pv5f/")
        self.assertEqual(rumble_player_url("446413572"), "https://rumble.com/embed/v7ds6x0/")
        self.assertEqual(embed_to_chat_id("v5pv5f"), "9604851")
        self.assertEqual(embed_to_chat_id("v7ds6x0"), "446413572")
        self.assertEqual(kick_player_url("https://kick.com/smashjt"), "https://player.kick.com/smashjt")
        self.assertEqual(kick_player_url("SmashJT"), "https://player.kick.com/smashjt")
        with self.assertRaises(ValueError):
            youtube_player_url("https://www.youtube.com/watch?v=abcdefghijk")
        with self.assertRaises(ValueError):
            youtube_player_url("short")
        with self.assertRaises(ValueError):
            rumble_player_url("v5pv5f")
        with self.assertRaises(ValueError):
            kick_player_url("https://kick.com/settings")

    def test_only_official_player_urls_are_allowed(self):
        self.assertTrue(allowed_player_url("https://www.youtube.com/embed/abcdefghijk"))
        self.assertTrue(allowed_player_url("https://www.youtube-nocookie.com/embed/abcdefghijk"))
        self.assertTrue(allowed_player_url("https://rumble.com/embed/v5pv5f/"))
        self.assertTrue(allowed_player_url("https://player.kick.com/smashjt"))
        self.assertFalse(allowed_player_url("https://www.youtube.com/embed/abcdefghijk?autoplay=1"))
        self.assertFalse(allowed_player_url("https://www.youtube.com/watch?v=abcdefghijk"))
        self.assertFalse(allowed_player_url("http://www.youtube.com/embed/abcdefghijk"))
        self.assertFalse(allowed_player_url("https://player.kick.com/api"))
        self.assertFalse(allowed_player_url("https://evil.example/embed/abcdefghijk"))
        self.assertFalse(allowed_player_url("https://rumble.com/embed/v5pv5f"))

    def test_a_saved_video_id_does_not_call_the_live_lookup(self):
        result = _player("youtube", {
            "youtube_follow_live": "0",
            "youtube_channel": "https://www.youtube.com/@Example",
            "youtube_watch": "https://www.youtube.com/watch?v=abcdefghijk",
        })
        self.assertEqual(result, {
            "platform": "youtube",
            "url": "https://www.youtube.com/embed/abcdefghijk",
            "message": "",
        })

    def test_follow_live_uses_the_lookup_and_ignores_a_saved_video(self):
        seen = []

        def live(channel):
            seen.append(channel)
            return "abcdefghijk"

        result = _player(
            "youtube",
            {
                "youtube_follow_live": "1",
                "youtube_channel": "https://www.youtube.com/@Example",
                "youtube_watch": "zzzzzzzzzzz",
            },
            youtube_live=live,
        )
        self.assertEqual(seen, ["https://www.youtube.com/@Example"])
        self.assertEqual(result["url"], "https://www.youtube.com/embed/abcdefghijk")
        self.assertEqual(result["message"], "")

        # The string "0" is off. A boolean of that string would be on.
        quiet = _player("youtube", {
            "youtube_follow_live": "0",
            "youtube_channel": "https://www.youtube.com/@Example",
            "youtube_watch": "abcdefghijk",
        })
        self.assertEqual(quiet["url"], "https://www.youtube.com/embed/abcdefghijk")

    def test_offline_channel_is_a_note_and_not_an_older_video(self):
        result = _player(
            "youtube",
            {"youtube_follow_live": True, "youtube_channel": "https://www.youtube.com/@Example", "youtube_watch": "abcdefghijk"},
            youtube_live=lambda _channel: None,
        )
        self.assertEqual(result["url"], "")
        self.assertEqual(result["message"], "That YouTube channel is not live.")

        rumble = _player(
            "rumble",
            {
                "rumble_follow_live": "1",
                "rumble_channel": "https://rumble.com/user/Example",
                "rumble_watch": "9604851",
            },
            rumble_live=lambda _channel: None,
        )
        self.assertEqual(rumble["url"], "")
        self.assertEqual(rumble["message"], "That Rumble channel is not live.")

    def test_missing_addresses_do_not_call_a_lookup(self):
        youtube = _player("youtube", {"youtube_follow_live": "1", "youtube_channel": "", "youtube_watch": "abcdefghijk"})
        self.assertEqual(youtube["url"], "")
        self.assertEqual(youtube["message"], "This ledger has no YouTube channel to embed.")
        rumble = _player("rumble", {"rumble_follow_live": "0"})
        self.assertEqual(rumble["message"], "This ledger has no Rumble video to embed.")
        kick = _player("kick", {})
        self.assertEqual(kick["message"], "This ledger has no Kick channel to embed.")
        self.assertNotIn("http", youtube["message"] + rumble["message"] + kick["message"])

    def test_a_lookup_error_does_not_repeat_the_saved_address(self):
        channel = "https://www.youtube.com/@SmashJT/"

        def live(_channel):
            raise ValueError(f"Could not read {channel}")

        result = _player(
            "youtube",
            {"youtube_follow_live": "1", "youtube_channel": channel},
            youtube_live=live,
        )
        self.assertEqual(result["url"], "")
        self.assertEqual(result["message"], "That address could not be embedded.")
        self.assertNotIn("SmashJT", result["message"])

        def down(_channel):
            raise ApiError(f"HTTP 503: {channel}", status=503)

        with self.assertRaises(ApiError) as caught:
            _player(
                "youtube",
                {"youtube_follow_live": "1", "youtube_channel": channel},
                youtube_live=down,
            )
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(str(caught.exception), "That address could not be embedded.")
        self.assertNotIn("SmashJT", str(caught.exception))

    def test_rumble_chat_id_becomes_the_embed_and_an_unknown_source_is_rejected(self):
        def chat(target):
            self.assertEqual(target, "9604851")
            return target

        result = _player("rumble", {"rumble_watch": "9604851", "rumble_follow_live": "0"}, rumble_chat=chat)
        self.assertEqual(result["url"], "https://rumble.com/embed/v5pv5f/")
        with self.assertRaises(ValueError):
            _player("twitch", {})


if __name__ == "__main__":
    unittest.main()
