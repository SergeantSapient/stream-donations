"""Chat commands: !total and !top."""

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from streamdonations.chat_feed import CommandTap, offer_chat
from streamdonations.commands import (
    COOLDOWN_SECONDS,
    LIMITS,
    RUMBLE_ITEM_SECONDS,
    ChatCommands,
    parse_command,
    top_reply,
    total_reply,
    trim_reply,
)
from streamdonations.goals import dump_tiers, normalize_tiers
from streamdonations.normalize import Donation
from streamdonations.service import Service
from streamdonations.store import Store
from streamdonations.transport import HttpResult

START = datetime(2026, 10, 18, 5, 0, 0, tzinfo=timezone.utc)


def _iso(seconds: float) -> str:
    return (START + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _line(text: str, key: str, author: str = "viewer", at: float | None = 5) -> dict:
    return {"key": key, "author": author, "text": text, "time": "" if at is None else _iso(at)}


def _gift(key: str, donor: str, usd: float, platform: str = "youtube") -> Donation:
    return Donation(
        dedupe_key=key, platform=platform, kind="super_chat" if platform == "youtube" else "donation",
        external_id=key, channel="", author_name=donor, author_id="", message="",
        amount_value=usd, amount_micros=int(usd * 1_000_000), currency="USD",
        created_at="2026-10-18T04:59:00Z", raw={},
    )


class Clock:
    def __init__(self):
        self.mono = 1000.0
        self.wall = START.timestamp()

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += seconds


class FakeTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("json_body")))
        if method == "GET" and "/videos?" in url:
            body = {"items": [{"liveStreamingDetails": {"activeLiveChatId": "LIVE"}}]}
        elif method == "POST" and "/liveChat/messages?" in url:
            body = {}
        else:
            raise AssertionError(method + " " + url)
        return HttpResult(200, {}, json.dumps(body).encode(), url)


def _connect(store: Store) -> None:
    store.set_setting("youtube_client_id", "client")
    store.set_setting("youtube_client_secret", "secret")
    store.set_setting("youtube_refresh_token", "refresh")
    store.set_setting("youtube_access_token", "access")
    store.set_setting("youtube_access_expiry", str(time.time() + 3600))
    store.set_setting("youtube_granted_scope", "https://www.googleapis.com/auth/youtube.force-ssl")
    store.set_setting("kick_client_id", "client")
    store.set_setting("kick_client_secret", "secret")
    store.set_setting("kick_refresh_token", "refresh")
    store.set_setting("kick_granted_scope", "chat:write")


class ParseTests(unittest.TestCase):
    def test_first_word_any_case(self):
        self.assertEqual(parse_command("!total"), "total")
        self.assertEqual(parse_command("!TOTAL"), "total")
        self.assertEqual(parse_command("  !Total   please "), "total")
        self.assertEqual(parse_command("!top"), "top")
        self.assertEqual(parse_command("!top!"), "top")
        self.assertEqual(parse_command("!total, how much?"), "total")

    def test_other_words_are_not_commands(self):
        for text in ("", "total", "what is the !total", "!totals", "!topdonors", "! total", "!", "!!total"):
            self.assertEqual(parse_command(text), "", text)


class ReplyTextTests(unittest.TestCase):
    def test_trim_ends_on_a_word(self):
        text = "word " * 100
        cut = trim_reply(text, 200)
        self.assertLessEqual(len(cut), 200)
        self.assertTrue(cut.endswith("word…"))
        self.assertEqual(trim_reply("short", 200), "short")

    def test_total_drops_the_extra_note_before_cutting(self):
        tab = {"tiers_enabled": "0", "goal_tiers": "", "chat_extra": "x" * 300}
        rows = [{"amount_usd": 12.5}]
        line = total_reply(tab, rows, LIMITS["youtube"])
        self.assertEqual(line, "$12.50 total for stream.")
        line = total_reply(dict(tab, chat_extra="Thanks all!"), rows, LIMITS["youtube"])
        self.assertEqual(line, "$12.50 total for stream. Thanks all!")

    def test_total_includes_the_next_goal(self):
        tiers = normalize_tiers([{"enabled": True, "name": "Goal one", "amount": 50, "bonus": ""}])
        tab = {"tiers_enabled": "1", "goal_tiers": dump_tiers(tiers), "chat_extra": ""}
        line = total_reply(tab, [{"amount_usd": 20}], 200)
        self.assertEqual(line, "$20.00 total for stream. $30.00 from Goal one")

    def test_top_skips_anonymous_and_unnamed_and_has_no_at(self):
        rows = [
            {"author_name": "Anonymous", "amount_usd": 100, "platform": "youtube"},
            {"author_name": "", "amount_usd": 90, "platform": "kick"},
            {"author_name": "@ada", "amount_usd": 30, "platform": "youtube"},
            {"author_name": "bob", "amount_usd": 20, "platform": "kick"},
            {"author_name": "cy", "amount_usd": 10, "platform": "rumble"},
            {"author_name": "dee", "amount_usd": 5, "platform": "rumble"},
        ]
        line = top_reply(rows, 200)
        self.assertEqual(line, "Top Donors: ada: $30.00, bob: $20.00, cy: $10.00. Thank you for the support!")
        self.assertNotIn("@", line)
        self.assertNotIn("Anonymous", line)

    def test_long_names_are_shortened_to_fit_youtube(self):
        rows = [{"author_name": c * 80, "amount_usd": 10 - i} for i, c in enumerate("abc")]
        line = top_reply(rows, LIMITS["youtube"])
        self.assertLessEqual(len(line), 200)
        self.assertIn("$10.00", line)
        self.assertIn("$8.00", line)

    def test_limits(self):
        self.assertEqual(LIMITS["youtube"], 200)
        self.assertEqual(LIMITS["kick"], 500)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.transport = FakeTransport()
        self.service = Service(self.store, self.transport)
        self.clock = Clock()
        self.commands = ChatCommands(
            self.service, clock=lambda: self.clock.mono, wall=lambda: self.clock.wall, run=lambda job: job()
        )
        self.service.commands = self.commands
        self.sent = []

        def send(platform, text, tab):
            self.sent.append((platform, text, tab["id"]))
            return {"platform": platform, "ok": True}

        self.service._send_chat_one = send
        _connect(self.store)
        self.store.stream_tabs()
        self.open_id = self.store._active_stream_id()
        self.store.set_stream_paused(None, False)
        self.store.set_setting("collection_paused", "0")
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("kick_watch", "https://kick.com/streamer")
        self.store.set_setting("youtube_chat_commands", "1")
        self.store.set_setting("kick_chat_commands", "1")
        self.store.add_many([_gift("g1", "Ada", 25), _gift("g2", "Bob", 10, "kick")])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def say(self, platform, rows, chat="video-1", fresh=False, stream_id=None):
        self.commands.listen(platform, rows, stream_id=stream_id, chat=chat, fresh=fresh)

    def test_off_by_default(self):
        self.store.set_setting("youtube_chat_commands", "0")
        tab = self.store.add_stream()
        snapshot = next(item for item in self.store.ledger_snapshots() if item["id"] == tab)
        self.assertIn(snapshot["youtube_chat_commands"], ("", "0"))
        self.store.set_stream_paused(None, False)
        self.store.set_setting("collection_paused", "0")
        self.say("youtube", [_line("!total", "m1")])
        self.assertEqual(self.sent, [])

    def test_total_and_top_reply_in_the_same_chat(self):
        self.say("youtube", [_line("!total", "m1")])
        self.assertEqual(self.sent, [("youtube", "$35.00 total for stream.", self.open_id)])
        self.say("kick", [_line("!TOP please", "k1")], chat="streamer")
        self.assertEqual(self.sent[1][0], "kick")
        self.assertEqual(self.sent[1][1], "Top Donors: Ada: $25.00, Bob: $10.00. Thank you for the support!")

    def test_one_shared_cooldown_per_chat(self):
        self.say("youtube", [_line("!total", "m1"), _line("!top", "m2"), _line("!total", "m3")])
        self.assertEqual(len(self.sent), 1)
        self.clock.advance(COOLDOWN_SECONDS - 1)
        self.say("youtube", [_line("!top", "m4", at=COOLDOWN_SECONDS - 2)])
        self.assertEqual(len(self.sent), 1)
        # A different chat on the same platform has its own cooldown.
        self.say("youtube", [_line("!top", "m5", at=COOLDOWN_SECONDS - 2)], chat="video-2")
        self.assertEqual(len(self.sent), 2)
        self.clock.advance(2)
        self.say("youtube", [_line("!top", "m6", at=COOLDOWN_SECONDS + 1)])
        self.assertEqual(len(self.sent), 3)
        self.assertTrue(self.sent[2][1].startswith("Top Donors:"))

    def test_a_failed_send_still_starts_the_cooldown(self):
        self.service._send_chat_one = lambda platform, text, tab: (
            self.sent.append((platform, text, tab["id"])) or {"platform": platform, "ok": False, "error": "no"}
        )
        self.say("youtube", [_line("!total", "m1")])
        self.say("youtube", [_line("!total", "m2")])
        self.assertEqual(len(self.sent), 1)

    def test_a_platform_that_cannot_send_does_not_start_the_cooldown(self):
        self.store.set_setting("youtube_granted_scope", "https://www.googleapis.com/auth/youtube.readonly")
        self.say("youtube", [_line("!total", "m1")])
        self.assertEqual(self.sent, [])
        self.store.set_setting("youtube_granted_scope", "https://www.googleapis.com/auth/youtube.force-ssl")
        self.say("youtube", [_line("!total", "m2")])
        self.assertEqual(len(self.sent), 1)

    def test_quota_pause_skips_youtube(self):
        self.store.set_setting("youtube_quota_resume_at", str(time.time() + 3600))
        self.say("youtube", [_line("!total", "m1")])
        self.assertEqual(self.sent, [])

    def test_backlog_is_ignored(self):
        # Before this process started listening.
        self.say("youtube", [_line("!total", "old1", at=-5)])
        # Older than a minute.
        self.clock.advance(300)
        self.say("youtube", [_line("!total", "old2", at=200)])
        # No platform time in the first batch after a connect.
        self.say("youtube", [_line("!total", "old3", at=None)], fresh=True)
        self.assertEqual(self.sent, [])
        # The same untimed line after that first batch is live.
        self.say("youtube", [_line("!total", "new1", at=None)])
        self.assertEqual(len(self.sent), 1)

    def test_own_account_and_own_replies_are_ignored(self):
        self.store.set_setting("youtube_sender_name", "My Channel")
        self.say("youtube", [_line("!total", "m1", author="@My Channel")])
        self.assertEqual(self.sent, [])
        self.say("youtube", [_line("!total", "m2")])
        self.assertEqual(len(self.sent), 1)
        reply = self.sent[0][1]
        self.clock.advance(COOLDOWN_SECONDS + 1)
        # The reply read back from chat never answers, even if it looked like a command.
        self.commands._replies[("youtube", "video-1")].append(("!total echo", self.clock.mono + 60))
        self.say("youtube", [_line(reply, "echo1", at=COOLDOWN_SECONDS), _line("!total echo", "echo2", at=COOLDOWN_SECONDS)])
        self.assertEqual(len(self.sent), 1)

    def test_a_message_seen_twice_answers_once(self):
        self.say("youtube", [_line("!total", "m1")])
        self.clock.advance(COOLDOWN_SECONDS + 1)
        self.say("youtube", [_line("!total", "m1")])
        self.assertEqual(len(self.sent), 1)

    def test_top_is_ignored_when_top_donors_are_hidden(self):
        self.store.set_setting("show_top_donors", "0")
        self.say("youtube", [_line("!top", "m1")])
        self.assertEqual(self.sent, [])
        self.say("youtube", [_line("!total", "m2")])
        self.assertEqual(len(self.sent), 1)

    def test_a_disconnected_ledger_does_not_answer(self):
        self.store.set_stream_paused(None, True)
        self.store.set_setting("collection_paused", "1")
        self.say("youtube", [_line("!total", "m1")])
        self.assertEqual(self.sent, [])

    def test_two_ledgers_on_one_chat_answer_once_and_background_ledgers_answer(self):
        other = self.store.add_stream()
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("youtube_chat_commands", "1")
        self.store.set_stream_paused(None, False)
        self.store.set_setting("collection_paused", "0")
        self.store.add_many([_gift("g3", "Cy", 7)])
        self.store.switch_stream(self.open_id)
        tap = CommandTap(self.commands.listen, other)
        # Both ledgers read the same video. The background one sees it first.
        offer_chat(tap, "youtube", [_line("!total", "m1")], chat="video-1")
        offer_chat(self.service.chat, "youtube", [_line("!total", "m1")], chat="video-1")
        self.assertEqual(self.sent, [("youtube", "$7.00 total for stream.", other)])
        # The background tap never feeds the open ledger's panel.
        self.assertEqual(self.service.chat.since(0, 0, False)["lines"][0]["text"], "!total")
        self.assertEqual(len(self.service.chat.since(0, 0, False)["lines"]), 1)

    def test_open_ledger_feed_reaches_commands(self):
        offer_chat(self.service.chat, "youtube", [_line("!total", "m1")], chat="video-1")
        self.assertEqual(len(self.sent), 1)

    def test_youtube_reply_goes_through_the_api_once(self):
        service = Service(self.store, self.transport)
        commands = ChatCommands(service, clock=lambda: self.clock.mono, wall=lambda: self.clock.wall, run=lambda job: job())
        commands.listen("youtube", [_line("!total", "m1")], chat="abcdefghijk")
        posts = [call for call in self.transport.calls if call[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["snippet"]["textMessageDetails"]["messageText"], "$35.00 total for stream.")
        self.assertEqual(service.youtube._quota.units_used(), 21)
        logs = [row["message"] for row in self.store.recent_log(limit=10)]
        self.assertIn("Answered !total in YouTube chat.", logs)


class RumbleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.service = Service(self.store, FakeTransport())
        self.clock = Clock()
        self.commands = ChatCommands(
            self.service, clock=lambda: self.clock.mono, wall=lambda: self.clock.wall, run=lambda job: job()
        )
        self.store.stream_tabs()
        self.store.set_stream_paused(None, False)
        self.store.set_setting("collection_paused", "0")
        self.store.set_setting("rumble_watch", "123")
        self.store.set_setting("rumble_chat_commands", "1")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_without_a_desktop_window_rumble_is_skipped(self):
        self.commands.listen("rumble", [_line("!total", "r1")], chat="123")
        self.assertEqual(self.commands.claim_rumble(True), [])
        # The window's ask came after the line, so that line was skipped, not queued.
        self.commands.listen("rumble", [_line("!total", "r2")], chat="123")
        handed = self.commands.claim_rumble(True)
        self.assertEqual([item["text"] for item in handed], ["$0.00 total for stream."])

    def test_a_browser_window_does_not_take_rumble_replies(self):
        self.commands.claim_rumble(True)
        self.commands.listen("rumble", [_line("!total", "r1")], chat="123")
        self.assertEqual(self.commands.claim_rumble(False), [])
        self.clock.advance(RUMBLE_ITEM_SECONDS + 1)
        self.assertEqual(self.commands.claim_rumble(False), [])
        logs = [row["message"] for row in self.store.recent_log(limit=10)]
        self.assertTrue(any("was not answered" in message for message in logs))

    def test_window_readiness_expires(self):
        self.commands.claim_rumble(True)
        self.assertTrue(self.commands.rumble_ready())
        self.clock.advance(60)
        self.assertFalse(self.commands.rumble_ready())


class Recorder:
    def __init__(self):
        self.taps = []

    def add(self, platform, rows):
        return None

    def clear(self, platform=None):
        return None

    def command_tap(self, platform, rows, *, chat="", fresh=False):
        self.taps.append((platform, [row["text"] for row in rows], chat, fresh))


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.store.set_setting("collection_paused", "0")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_rumble_marks_the_first_event_after_connect(self):
        import threading

        from streamdonations.rumble import RumbleClient

        feed = Recorder()
        client = RumbleClient(self.store, FakeTransport(), chat_feed=feed)
        client._chat_live_id = "446413572"
        client._chat_fresh = True
        event = {"type": "messages", "data": {
            "messages": [{"id": "1", "user_id": "u", "text": "!total", "time": _iso(5)}],
            "users": [{"id": "u", "username": "bob"}],
        }}
        line = ("data: " + json.dumps(event)).encode()
        stop = threading.Event()
        client._consume_sse_line(line, stop)
        client._consume_sse_line(line, stop)
        self.assertEqual(feed.taps, [
            ("rumble", ["!total"], "446413572", True),
            ("rumble", ["!total"], "446413572", False),
        ])

    def test_youtube_passes_the_video_and_marks_the_bootstrap_batch(self):
        import threading

        from streamdonations.youtube_public import PublicChatFollower

        feed = Recorder()
        follower = PublicChatFollower(self.store, FakeTransport(), feed)
        actions = [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "id": "m1", "message": {"runs": [{"text": "!top"}]},
            "authorName": {"simpleText": "ada"}, "timestampUsec": "1792040405000000",
        }}}}]
        stop = threading.Event()
        follower._live = lambda _stop: True
        follower._store(actions, "", stop, chat="abcdefghijk", fresh=True)
        follower._store(actions, "", stop, chat="abcdefghijk")
        self.assertEqual(feed.taps, [
            ("youtube", ["!top"], "abcdefghijk", True),
            ("youtube", ["!top"], "abcdefghijk", False),
        ])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.service = Service(self.store, FakeTransport())
        self.store.stream_tabs()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_toggles_are_per_ledger_and_per_platform(self):
        first = self.store._active_stream_id()
        self.service.apply_settings({"youtube_chat_commands": True, "kick_chat_commands": "0"})
        self.assertEqual(self.store.get_setting("youtube_chat_commands"), "1")
        self.assertEqual(self.store.get_setting("kick_chat_commands"), "0")
        second = self.store.add_stream()
        # A new ledger copies the open ledger's switches.
        self.assertEqual(self.store.get_setting("youtube_chat_commands"), "1")
        self.assertEqual(self.store.get_setting("kick_chat_commands"), "0")
        self.service.apply_settings({"rumble_chat_commands": True, "youtube_chat_commands": False})
        tabs = {tab["id"]: tab for tab in self.store.ledger_snapshots()}
        self.assertEqual(tabs[first]["youtube_chat_commands"], "1")
        self.assertNotEqual(tabs[first]["rumble_chat_commands"], "1")
        self.assertEqual(tabs[second]["rumble_chat_commands"], "1")
        self.assertNotEqual(tabs[second]["youtube_chat_commands"], "1")
        self.store.switch_stream(first)
        self.assertEqual(self.store.get_setting("youtube_chat_commands"), "1")
        self.assertNotEqual(self.store.get_setting("rumble_chat_commands"), "1")

    def test_a_fresh_store_starts_with_every_switch_off(self):
        tab = self.store.ledger_snapshots()[0]
        for key in ("youtube_chat_commands", "rumble_chat_commands", "kick_chat_commands"):
            self.assertIn(tab[key], ("", "0"))
            self.assertIs(self.service_settings()[key], False)

    def service_settings(self):
        from streamdonations.server import App
        return App(self.store, "t" * 32, 47321, service=self.service)._public_settings()

    def test_new_and_replacement_ledgers_copy_the_switches(self):
        self.service.apply_settings({"youtube_chat_commands": True, "rumble_chat_commands": True})
        second = self.store.add_stream()
        tab = next(item for item in self.store.ledger_snapshots() if item["id"] == second)
        self.assertEqual(tab["youtube_chat_commands"], "1")
        self.assertEqual(tab["rumble_chat_commands"], "1")
        self.assertEqual(tab["kick_chat_commands"], "0")
        # Closing every ledger leaves a blank one, which copies from the ledger being closed.
        for item in list(self.store.stream_tabs()["tabs"]):
            if item["id"] != second:
                self.store.close_stream(item["id"])
        self.store.close_stream(second)
        tabs = self.store.ledger_snapshots()
        self.assertEqual(len(tabs), 1)
        self.assertNotEqual(tabs[0]["id"], second)
        self.assertEqual(tabs[0]["youtube_chat_commands"], "1")
        self.assertEqual(tabs[0]["rumble_chat_commands"], "1")
        self.assertEqual(self.store.get_setting("youtube_chat_commands"), "1")

    def test_page_has_a_toggle_and_note_for_each_platform(self):
        page = (Path(__file__).resolve().parents[1] / "streamdonations/web/index.html").read_text(encoding="utf-8")
        for platform in ("youtube", "rumble", "kick"):
            self.assertIn(f'id="{platform}-chat-commands"', page)
            self.assertIn(f'id="{platform}-commands-note"', page)


if __name__ == "__main__":
    unittest.main()
