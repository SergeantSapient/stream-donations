"""Soft disconnect pauses platform APIs and live chat without signing out."""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from streamdonations.chat_feed import CommandTap
from streamdonations.normalize import Donation
from streamdonations.service import Service
from streamdonations.store import Store
from streamdonations.transport import HttpResult


class RecordingTransport:
    def __init__(self):
        self.urls: list[str] = []

    def request(self, method, url, **_kwargs):
        self.urls.append(url)
        return HttpResult(200, {}, b"{}", url)


def _hold(started: threading.Event):
    def loop(_target, stop):
        started.set()
        stop.wait(5)

    return loop


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.transport = RecordingTransport()
        self.service = Service(self.store, self.transport)

    def tearDown(self):
        self.service.stop()
        self.service.youtube.rest()
        self.service.rumble.rest()
        self.service.kick.rest()
        self.store.close()
        self.tmp.cleanup()

    def test_public_chats_stop_and_restart_without_requests(self):
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("rumble_watch", "123456")
        self.store.set_setting("kick_watch", "some-channel")
        youtube_started = threading.Event()
        rumble_started = threading.Event()
        kick_started = threading.Event()
        self.service.youtube._public._loop = _hold(youtube_started)
        self.service.rumble._chat_loop = _hold(rumble_started)
        self.service.kick._public._loop = _hold(kick_started)

        self.service.youtube.tick()
        self.service.rumble.tick()
        self.service.kick.tick()
        self.assertTrue(youtube_started.wait(1))
        self.assertTrue(rumble_started.wait(1))
        self.assertTrue(kick_started.wait(1))
        self.assertEqual(self.transport.urls, [])

        self.service.set_paused(True)
        self.assertFalse(self.service.youtube._public._thread.is_alive())
        self.assertFalse(self.service.rumble._chat_thread.is_alive())
        self.assertFalse(self.service.kick._public._thread.is_alive())
        self.assertEqual(self.transport.urls, [])
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")
        self.assertEqual(self.store.get_setting("rumble_watch"), "123456")
        self.assertEqual(self.store.get_setting("kick_watch"), "some-channel")
        for name in ("youtube", "rumble", "kick"):
            status = self.store.status()[name]
            self.assertEqual(status["state"], "idle")
            self.assertIn("paused", status["detail"])
        self.assertEqual(self.service.poll_now(), ["Collection is paused. Reconnect to send requests again."])
        self.assertEqual(self.transport.urls, [])

        youtube_started.clear()
        rumble_started.clear()
        kick_started.clear()
        self.service.set_paused(False)
        self.assertEqual(self.store.get_setting("collection_paused"), "0")
        self.assertTrue(youtube_started.wait(1))
        self.assertTrue(rumble_started.wait(1))
        self.assertTrue(kick_started.wait(1))
        self.assertEqual(self.transport.urls, [])
        self.assertFalse(self.service.paused())

    def test_signed_in_requests_stop_and_credentials_stay(self):
        future = str(time.time() + 3600)
        self.store.set_setting("youtube_client_id", "client")
        self.store.set_setting("youtube_client_secret", "secret")
        self.store.set_setting("youtube_refresh_token", "refresh")
        self.store.set_setting("youtube_access_token", "access")
        self.store.set_setting("youtube_access_expiry", future)
        self.store.set_setting("rumble_api_url", "https://rumble.com/-livestream-api/example")
        self.store.set_setting("kick_client_id", "client")
        self.store.set_setting("kick_client_secret", "secret")
        self.store.set_setting("kick_refresh_token", "refresh")
        self.store.set_setting("kick_access_token", "access")
        self.store.set_setting("kick_access_expiry", future)

        self.service.set_paused(True)
        self.service.youtube.tick(force=True)
        self.service.rumble.tick(force=True)
        self.service.kick.tick(force=True)
        self.assertEqual(self.service.poll_now()[0], "Collection is paused. Reconnect to send requests again.")
        self.assertEqual(self.transport.urls, [])
        self.assertEqual(self.store.get_setting("youtube_refresh_token"), "refresh")
        self.assertEqual(self.store.get_setting("kick_refresh_token"), "refresh")
        self.assertEqual(self.store.get_setting("rumble_api_url"), "https://rumble.com/-livestream-api/example")

        self.service.set_paused(False)
        self.assertGreaterEqual(len(self.transport.urls), 3)
        paused_at = len(self.transport.urls)
        self.service.set_paused(True)
        self.service.poll_now()
        self.assertEqual(len(self.transport.urls), paused_at)
        self.assertEqual(self.store.get_setting("youtube_refresh_token"), "refresh")

    def test_pausing_a_background_ledger_does_not_stop_the_open_chat(self):
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("collection_paused", "0")
        second = self.store.add_stream()
        first = next(tab["id"] for tab in self.store.stream_tabs()["tabs"] if tab["id"] != second)
        self.store.switch_stream(first)
        started = threading.Event()
        self.service.youtube._public._loop = _hold(started)
        self.service.youtube.tick()
        self.assertTrue(started.wait(1))

        self.service.set_paused(True, second)
        self.assertFalse(self.service.paused())
        self.assertTrue(self.service.youtube._public._thread.is_alive())
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")
        self.assertEqual(self.store.get_setting("collection_paused"), "0")
        notes = " ".join(row["message"] for row in self.store.recent_log())
        self.assertIn("Paused that ledger", notes)
        self.assertNotIn("This ledger's", notes)

    def test_another_connected_ledger_keeps_collecting(self):
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("collection_paused", "0")
        second = self.store.add_stream()
        self.store.set_setting("youtube_watch", "zzzzzzzzzzz")
        self.store.set_setting("rumble_watch", "654321")
        self.store.set_setting("kick_watch", "other-channel")
        self.store.set_setting("youtube_access_token", "access")
        self.store.set_setting("youtube_access_expiry", str(time.time() + 3600))
        self.store.set_setting("rumble_api_url", "https://rumble.com/-livestream-api/example")
        self.store.set_setting("collection_paused", "0")
        first = next(tab["id"] for tab in self.store.stream_tabs()["tabs"] if tab["id"] != second)
        self.store.switch_stream(first)
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")
        self.assertEqual(self.store.get_setting("collection_paused"), "0")

        started = {name: threading.Event() for name in ("youtube", "rumble", "kick")}
        real_make = self.service._make_other

        def make(tab_id):
            bundle = real_make(tab_id)
            bundle.youtube._public._loop = _hold(started["youtube"])
            bundle.rumble._chat_loop = _hold(started["rumble"])
            bundle.kick._public._loop = _hold(started["kick"])
            self.background = bundle
            return bundle

        self.service._make_other = make
        self.service._sync_other_ledgers()
        self.assertTrue(started["youtube"].wait(1))
        self.assertTrue(started["rumble"].wait(1))
        self.assertTrue(started["kick"].wait(1))
        self.assertEqual(self.transport.urls, [])
        # Background chat reaches chat commands only, never the open ledger's panel.
        for feed in (
            self.background.youtube._public._chat_feed,
            self.background.rumble._chat_feed,
            self.background.kick._public._chat_feed,
        ):
            self.assertIsInstance(feed, CommandTap)
            self.assertIsNot(feed, self.service.chat)
            feed.add("kick", [{"key": "x", "author": "a", "text": "hello"}])
        self.assertEqual(self.service.chat.since(0, 0, False)["lines"], [])
        self.background.tick()
        self.assertEqual(self.transport.urls, [])

        stored = self.background.view.add_many([
            Donation(
                dedupe_key="kick:public:sub:other-ledger",
                platform="kick",
                kind="subscription",
                external_id="other-ledger",
                channel="other-channel",
                author_name="SampleViewer",
                author_id="10466179",
                message="Subscribed",
                amount_value=None,
                amount_micros=None,
                currency=None,
                created_at="2026-10-06T05:52:15Z",
                raw={},
            )
        ])
        self.assertEqual(stored, 1)
        with self.store._lock:
            row = self.store._conn.execute(
                "SELECT stream_id, author_name FROM donations WHERE dedupe_key = ?",
                ("kick:public:sub:other-ledger",),
            ).fetchone()
        self.assertEqual(row["stream_id"], second)
        self.assertEqual(row["author_name"], "SampleViewer")
        _rows, total = self.store.list_donations(include_hidden=True)
        self.assertEqual(total, 0)

        self.service.set_paused(True, second)
        self.assertFalse(self.service.paused())
        self.assertFalse(self.background.youtube._public._thread.is_alive())
        self.assertFalse(self.background.rumble._chat_thread.is_alive())
        self.assertFalse(self.background.kick._public._thread.is_alive())
        self.assertEqual(self.transport.urls, [])

    def test_startup_skips_kick_key_refresh_while_paused(self):
        self.store.set_setting("collection_paused", "1")
        self.store.set_setting("kick_client_id", "client")
        self.store.set_setting("kick_client_secret", "secret")
        self.store.set_setting("kick_refresh_token", "refresh")
        self.store.set_setting("kick_access_token", "access")
        self.store.set_setting("kick_access_expiry", str(time.time() + 3600))
        self.service.start()
        time.sleep(0.3)
        self.assertEqual(self.transport.urls, [])
        self.assertIn("paused", self.store.status()["kick"]["detail"])

    def test_settings_save_does_not_clear_the_pause(self):
        self.service.set_paused(True)
        self.service.apply_settings({"membership_price": "4.99"})
        self.assertTrue(self.service.paused())
        self.assertEqual(self.store.get_setting("membership_price"), "4.99")
        with self.assertRaises(ValueError):
            self.service.apply_settings({"collection_paused": "0"})
