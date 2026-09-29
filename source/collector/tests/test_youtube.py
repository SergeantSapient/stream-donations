"""Signed-in YouTube polling when the account cannot go live."""

import json
import tempfile
import time
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
