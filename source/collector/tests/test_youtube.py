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


if __name__ == "__main__":
    unittest.main()
