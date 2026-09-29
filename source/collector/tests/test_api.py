"""Local API authentication, settings redaction, and the Kick webhook route."""

import base64
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from streamdonations.kick_sig import signed_payload
from streamdonations.server import App, Handler, ThreadingServer, WebhookHandler
from streamdonations.store import Store
from streamdonations.transport import Transport


def _openssl(*args, input_bytes=None):
    return subprocess.run(["openssl", *args], input=input_bytes, capture_output=True, check=True)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.token = "a" * 32
        self.app = App(self.store, self.token, 1)
        self.server = ThreadingServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.app.ui_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.app.ui_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.stop_webhook()
        self.store.close()
        self.tmp.cleanup()

    def _request(self, path, method="GET", body=None, cookie=True, host=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = f"sd_session={self.token}"
        if host:
            headers["Host"] = host
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read()
                return response.status, response.headers, raw
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.headers, exc.read()
            finally:
                exc.close()

    def test_auth_boot_and_secret_redaction(self):
        status, _headers, body = self._request("/api/health", cookie=False)
        self.assertEqual(status, 401)

        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.app.ui_port, timeout=5)
        connection.request("GET", f"/boot?token={self.token}")
        boot = connection.getresponse()
        self.assertEqual(boot.status, 302)
        self.assertIn(f"sd_session={self.token}", boot.getheader("Set-Cookie") or "")
        boot.read()
        connection.close()

        status, headers, body = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Stream Donations", body)
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))

        status, _headers, body = self._request(
            "/api/settings",
            method="PUT",
            body={"youtube_client_id": "client-id", "youtube_client_secret": "super-secret-value"},
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertNotIn("super-secret-value", body.decode())
        self.assertTrue(payload["youtube_client_secret_set"])
        self.assertEqual(payload["youtube_client_id"], "client-id")
        self.assertIn("/oauth/google/callback", payload["google_redirect_uri"])

    def test_foreign_host_is_rejected(self):
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.app.ui_port, timeout=5)
        connection.request("GET", "/api/health", headers={"Host": "evil.example"})
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        connection.close()

    def test_process_a_stored_donation_over_http(self):
        from streamdonations.normalize import normalize_super_chat_event

        donation = normalize_super_chat_event(
            {
                "id": "evt",
                "snippet": {
                    "channelId": "UCb",
                    "supporterDetails": {"displayName": "Fan"},
                    "commentText": "ship it",
                    "createdAt": "2024-06-01T12:00:00Z",
                    "amountMicros": "5000000",
                    "currency": "USD",
                },
            }
        )
        self.store.add_many([donation])
        status, _headers, body = self._request("/api/donations?processed=0")
        self.assertEqual(status, 200)
        listed = json.loads(body)["donations"]
        self.assertEqual(listed[0]["message"], "ship it")
        status, _headers, body = self._request(
            f"/api/donations/{listed[0]['id']}",
            method="PATCH",
            body={"processed": True, "note": "queued"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["processed"])
        status, _headers, csv_body = self._request("/api/export.csv")
        self.assertEqual(status, 200)
        self.assertIn(b"ship it", csv_body)

    def test_reset_clears_donations_and_keeps_settings(self):
        from streamdonations.normalize import normalize_super_chat_event

        donation = normalize_super_chat_event(
            {
                "id": "reset-me",
                "snippet": {
                    "channelId": "UCb",
                    "supporterDetails": {"displayName": "Fan"},
                    "commentText": "gone",
                    "createdAt": "2024-06-01T12:00:00Z",
                    "amountMicros": "1000000",
                    "currency": "USD",
                },
            }
        )
        self.store.add_many([donation])
        self.store.set_setting("youtube_watch", "https://www.youtube.com/watch?v=abcdefghijk")
        self.store.log("youtube", "info", "still here")
        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'data-tab="ledger"', page)
        self.assertIn(b'id="reset-ledger"', page)
        self.assertIn(b'id="tab-log"', page)
        status, _headers, body = self._request("/api/ledger/reset", method="POST", body={})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["removed"], 1)
        _rows, total = self.store.list_donations()
        self.assertEqual(total, 0)
        self.assertEqual(
            self.store.get_setting("youtube_watch"),
            "https://www.youtube.com/watch?v=abcdefghijk",
        )
        messages = [entry["message"] for entry in self.store.recent_log()]
        self.assertIn("still here", messages)
        self.assertTrue(any(message.startswith("Ledger reset.") for message in messages))

    def test_webhook_requires_a_valid_signature(self):
        root = Path(self.tmp.name)
        key = root / "key.pem"
        pub = root / "pub.pem"
        _openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key))
        _openssl("rsa", "-in", str(key), "-pubout", "-out", str(pub))
        self.app.service.kick.set_public_key(pub.read_text(encoding="utf-8"))
        webhook = ThreadingHTTPServer(("127.0.0.1", 0), WebhookHandler)
        webhook.app = self.app
        thread = threading.Thread(target=webhook.serve_forever, daemon=True)
        thread.start()
        try:
            created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            event = {
                "broadcaster": {"channel_slug": "creator", "username": "creator"},
                "sender": {"user_id": 2, "username": "fan"},
                "gift": {"amount": 500, "name": "Rage Quit", "message": "w"},
                "created_at": created,
            }
            body = json.dumps(event, separators=(",", ":")).encode("utf-8")
            message_id = "01WEBHOOK"
            payload = signed_payload(message_id, created, body)
            signature = base64.b64encode(
                _openssl("dgst", "-sha256", "-sign", str(key), input_bytes=payload).stdout
            ).decode("ascii")
            status = self._post_webhook(webhook.server_port, body, message_id, created, signature)
            self.assertEqual(status, 204)
            rows, total = self.store.list_donations()
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]["kind"], "kicks")
            self.assertEqual(rows[0]["message"], "Rage Quit: w")
            again = self._post_webhook(webhook.server_port, body, message_id, created, signature)
            self.assertEqual(again, 204)
            _rows, total = self.store.list_donations()
            self.assertEqual(total, 1)
            bad = self._post_webhook(webhook.server_port, body + b" ", message_id, created, signature)
            self.assertEqual(bad, 401)
        finally:
            webhook.shutdown()
            webhook.server_close()

    def _post_webhook(self, port, body, message_id, timestamp, signature, event_type="kicks.gifted"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/kick/webhook",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Kick-Event-Message-Id": message_id,
                "Kick-Event-Message-Timestamp": timestamp,
                "Kick-Event-Signature": signature,
                "Kick-Event-Type": event_type,
                "Kick-Event-Version": "1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            try:
                return exc.code
            finally:
                exc.close()


class TransportTests(unittest.TestCase):
    def test_refuses_hosts_outside_the_allow_list(self):
        transport = Transport()
        with self.assertRaises(ValueError):
            transport.request("GET", "https://example.com/path", allow_hosts={"rumble.com"})
        with self.assertRaises(ValueError):
            transport.request(
                "GET",
                "http://rumble.com/-livestream-api/get-data?key=abc",
                allow_hosts={"rumble.com"},
            )


if __name__ == "__main__":
    unittest.main()
