"""Signed-in YouTube polling when the account cannot go live."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from streamdonations.service import Service
from streamdonations.store import Store
from streamdonations.transport import HttpResult
from streamdonations.youtube import YouTubeClient
from streamdonations.youtube_quota import (
    DAILY_LIMIT,
    INSERT_COST,
    LIVE_CHAT_MIN_SECONDS,
    list_cost,
    pacific_day,
)


class ScriptedTransport:
    def __init__(self, broadcasts: dict):
        self.broadcasts = broadcasts
        self.urls: list[str] = []

    def request(self, method, url, **_kwargs):
        self.urls.append(url)
        if "/superChatEvents" in url:
            body = json.dumps({"items": []}).encode()
            return HttpResult(200, {}, body, url)
        if "/liveBroadcasts" in url:
            body = json.dumps(self.broadcasts).encode()
            return HttpResult(403, {}, body, url)
        raise AssertionError(url)


def _connect(store: Store) -> None:
    store.set_setting("youtube_client_id", "client")
    store.set_setting("youtube_client_secret", "secret")
    store.set_setting("youtube_refresh_token", "refresh")
    store.set_setting("youtube_access_token", "access")
    store.set_setting("youtube_access_expiry", str(time.time() + 3600))


class YouTubeLiveDisabledTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        _connect(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_live_streaming_disabled_is_not_a_collector_failure(self):
        transport = ScriptedTransport(
            {
                "error": {
                    "code": 403,
                    "message": "The user is not enabled for live streaming.",
                    "errors": [{"reason": "liveStreamingNotEnabled"}],
                }
            }
        )
        client = YouTubeClient(self.store, transport)
        client.tick(force=True)
        client.tick()
        client.tick(force=True)

        status = self.store.status()["youtube"]
        self.assertEqual(status["state"], "ok")
        self.assertEqual(status["error"], "")
        self.assertIn("not enabled for live streaming", status["detail"])
        notes = [
            row["message"]
            for row in self.store.recent_log(limit=20)
            if "not enabled for live streaming" in row["message"]
        ]
        self.assertEqual(len(notes), 1)
        self.assertEqual(sum("/liveBroadcasts" in url for url in transport.urls), 2)

    def test_other_broadcast_errors_still_fail(self):
        transport = ScriptedTransport({"error": {"code": 403, "message": "Insufficient Permission"}})
        client = YouTubeClient(self.store, transport)
        client.tick(force=True)
        status = self.store.status()["youtube"]
        self.assertEqual(status["state"], "error")
        self.assertIn("Insufficient Permission", status["error"])


class YouTubeQuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        _connect(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_quota_exceeded_pauses_until_the_next_call_would_be_wasted(self):
        transport = QuotaTransport()
        client = YouTubeClient(self.store, transport)
        client.tick(force=True)
        client.tick(force=True)

        self.assertEqual(len(transport.urls), 1)
        self.assertIn("/superChatEvents", transport.urls[0])
        status = self.store.status()["youtube"]
        self.assertEqual(status["state"], "ok")
        self.assertIn("paused until", status["detail"])
        notes = [row["message"] for row in self.store.recent_log(limit=20) if "quota" in row["message"].lower()]
        self.assertEqual(len(notes), 1)
        self.assertNotIn("<a href", notes[0])

    def test_super_chat_backfill_is_one_page_per_poll(self):
        transport = PagedSuperChatTransport()
        client = YouTubeClient(self.store, transport)
        client.tick(force=True)
        client.tick(force=True)
        client.tick(force=True)

        super_urls = [url for url in transport.urls if "/superChatEvents" in url]
        self.assertEqual(len(super_urls), 3)
        self.assertNotIn("pageToken", super_urls[0])
        self.assertIn("pageToken=page-2", super_urls[1])
        self.assertNotIn("pageToken", super_urls[2])
        self.assertEqual(self.store.get_setting("youtube_superchat_backfill_done"), "1")
        self.assertEqual(self.store.get_setting("youtube_superchat_page"), "")

    def test_short_poll_interval_is_raised_to_five_minutes(self):
        service = Service(self.store, ScriptedTransport({}))
        service.apply_settings({"youtube_poll_seconds": 90})
        self.assertEqual(self.store.get_setting("youtube_poll_seconds"), "300")
        client = YouTubeClient(self.store, ScriptedTransport({}))
        self.assertEqual(client.interval(), 300)


class QuotaTransport:
    def __init__(self):
        self.urls: list[str] = []
        self.body = json.dumps(
            {
                "error": {
                    "code": 403,
                    "message": "The request cannot be completed because you have exceeded your quota.",
                    "errors": [{"reason": "quotaExceeded"}],
                }
            }
        ).encode()

    def request(self, method, url, **_kwargs):
        self.urls.append(url)
        return HttpResult(403, {}, self.body, url)


class PagedSuperChatTransport:
    def __init__(self):
        self.urls: list[str] = []

    def request(self, method, url, **_kwargs):
        self.urls.append(url)
        if "/superChatEvents" in url:
            if "pageToken=" not in url:
                body = {"items": [], "nextPageToken": "page-2"}
            else:
                body = {"items": []}
            return HttpResult(200, {}, json.dumps(body).encode(), url)
        if "/liveBroadcasts" in url:
            return HttpResult(200, {}, json.dumps({"items": []}).encode(), url)
        raise AssertionError(url)


class LiveChatTransport:
    """One live broadcast on the signed-in channel. Chat reads return a set interval."""

    def __init__(self, interval_ms: int = 1000):
        self.interval_ms = interval_ms
        self.urls: list[str] = []

    def request(self, method, url, **_kwargs):
        self.urls.append(url)
        if "/superChatEvents" in url:
            body = {"items": []}
        elif "/liveBroadcasts" in url:
            body = {"items": [{"snippet": {"liveChatId": "chat-1", "channelId": "UC1", "title": "Live"}}]}
        elif "/liveChat/messages" in url:
            body = {"items": [], "nextPageToken": "next", "pollingIntervalMillis": self.interval_ms}
        else:
            raise AssertionError(url)
        return HttpResult(200, {}, json.dumps(body).encode(), url)


class YouTubeQuotaCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        _connect(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_published_costs(self):
        # https://developers.google.com/youtube/v3/determine_quota_cost
        self.assertEqual(list_cost("/channels"), 1)
        self.assertEqual(list_cost("/videos"), 1)
        self.assertEqual(list_cost("/superChatEvents"), 1)
        self.assertEqual(list_cost("/liveBroadcasts"), 1)
        self.assertEqual(list_cost("/liveChat/messages"), 5)
        self.assertEqual(INSERT_COST, 20)
        with self.assertRaises(ValueError):
            list_cost("/search")

    def test_each_call_is_charged_its_own_cost(self):
        transport = LiveChatTransport()
        client = YouTubeClient(self.store, transport)
        client.tick(force=True)
        # superChatEvents 1 + liveBroadcasts 1 + liveChatMessages 5.
        self.assertEqual(client._quota.units_used(), 7)
        chat_urls = [url for url in transport.urls if "/liveChat/messages" in url]
        self.assertEqual(len(chat_urls), 1)
        self.assertIn("maxResults=2000", chat_urls[0])

    def test_live_chat_read_waits_at_least_thirty_seconds(self):
        client = YouTubeClient(self.store, LiveChatTransport(interval_ms=1000))
        client.tick(force=True)
        wait = client._next_chat - time.monotonic()
        self.assertGreater(wait, LIVE_CHAT_MIN_SECONDS - 2)
        self.assertLessEqual(wait, LIVE_CHAT_MIN_SECONDS)

        transport = LiveChatTransport(interval_ms=1000)
        client = YouTubeClient(self.store, transport)
        client.tick(force=True)
        client.tick()
        client.tick()
        self.assertEqual(sum("/liveChat/messages" in url for url in transport.urls), 1)

    def test_a_longer_youtube_interval_is_kept(self):
        client = YouTubeClient(self.store, LiveChatTransport(interval_ms=45000))
        client.tick(force=True)
        wait = client._next_chat - time.monotonic()
        self.assertGreater(wait, 43)
        self.assertLessEqual(wait, 45)

    def test_a_read_that_would_pass_the_daily_quota_pauses(self):
        self.store.set_setting("youtube_quota_day", pacific_day())
        self.store.set_setting("youtube_quota_units", str(DAILY_LIMIT - 4))
        transport = LiveChatTransport()
        client = YouTubeClient(self.store, transport)
        client._chats = [{"id": "chat-1", "channel": "UC1", "title": "Live"}]
        client._next_super = time.monotonic() + 600
        client._next_broadcast = time.monotonic() + 600
        client.tick()
        self.assertEqual(transport.urls, [])
        self.assertTrue(client._quota.paused())
        self.assertIn("paused until", self.store.status()["youtube"]["detail"])
        self.assertEqual(client._quota.units_used(), DAILY_LIMIT - 4)

    def test_one_hour_of_one_live_chat(self):
        # Reads at the 30 second floor, broadcasts every 2 minutes, Super Chats every 15.
        per_hour = (3600 // LIVE_CHAT_MIN_SECONDS) * list_cost("/liveChat/messages")
        per_hour += (3600 // 120) * list_cost("/liveBroadcasts")
        per_hour += (3600 // 900) * list_cost("/superChatEvents")
        self.assertEqual(per_hour, 634)


if __name__ == "__main__":
    unittest.main()
