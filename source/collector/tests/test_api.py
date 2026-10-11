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
from streamdonations.normalize import Donation
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

    def _request(self, path, method="GET", body=None, cookie=True, host=None, raw=None):
        headers = {}
        if raw is not None:
            data = raw
            headers["Content-Type"] = "application/json"
        else:
            data = None if body is None else json.dumps(body).encode("utf-8")
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
        self.assertIn(b"<title>SuperBerry</title>", body)
        self.assertIn(b'class="brand-mark" src="/icons/superberry.png"', body)
        self.assertIn(b'class="brand-super">Super</span><span class="brand-berry">Berry</span>', body)
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))
        status, _headers, css = self._request("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(b"font-family: Fredoka", css)
        self.assertIn(b"/fonts/fredoka.woff2", css)
        self.assertIn(b"--brand-blue: #002ed7", css)
        self.assertIn(b"--brand-gold: #feb517", css)
        self.assertRegex(css, rb"\.brand-mark \{[^}]*background: var\(--raise-2\)")

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
        self.assertNotIn(b'data-tab="ledger"', page)
        self.assertNotIn(b'data-tab="log"', page)
        self.assertIn(b'id="tab-ledger"', page)
        self.assertIn(b'id="reset-ledger"', page)
        self.assertIn(b'id="export-button"', page)
        self.assertIn(b'id="export-menu"', page)
        self.assertIn(b'id="export-csv"', page)
        self.assertIn(b'id="export-jsonl"', page)
        self.assertIn(b'title="Export the open ledger"', page)
        self.assertIn(b'title="Download the open ledger as CSV"', page)
        self.assertIn(b'title="Download the open ledger as JSONL"', page)
        self.assertIn(b'title="Menu"', page)
        self.assertIn(b'title="Delete every donation on this ledger"', page)
        self.assertLess(page.index(b'id="export-button"'), page.index(b'id="log-button"'))
        self.assertLess(page.index(b'id="log-button"'), page.index(b'id="app-menu-button"'))
        self.assertLess(page.index(b'id="app-menu"'), page.index(b'id="reset-ledger"'))
        self.assertLess(page.index(b'id="reset-ledger"'), page.index(b'id="filters"'))
        filters = page.split(b'id="filters"', 1)[1].split(b"</form>", 1)[0]
        self.assertNotIn(b"Apply", filters)
        help_button = page.split(b'id="ledger-help-button"', 1)[1].split(b"</button>", 1)[0]
        self.assertIn(b'aria-label="Help"', help_button)
        self.assertIn(b'title="Help"', help_button)
        self.assertNotIn(b">Help<", help_button)
        help_panel = page.split(b'id="ledger-help"', 1)[1].split(b"</div>", 1)[0]
        self.assertIn(b'class="help-section"', help_panel)
        self.assertIn(b"Hover an automatic entry", help_panel)
        self.assertEqual(page.count(b"Hover an automatic entry"), 1)
        self.assertNotIn(b"poll-now", page)
        self.assertNotIn(b"Export CSV", page)
        self.assertNotIn(b"Export JSONL", page)
        self.assertIn(b'id="log-button"', page)
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertNotIn(b"poll-now", script)
        self.assertIn(b'$("export-csv").href = "/api/export.csv?" + exportQuery', script)
        self.assertIn(b'$("export-jsonl").href = "/api/export.jsonl?" + exportQuery', script)
        self.assertIn(b'toggleMenu("ledger-help", "ledger-help-button")', script)
        self.assertIn(b'id="tab-log"', page)
        self.assertIn(b'id="tab-youtube"', page)
        self.assertIn(b'id="tab-settings"', page)
        self.assertIn(b'class="sheet"', page)
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
        self.assertTrue(any(message.startswith("This ledger was reset.") for message in messages))

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

            self.store.set_setting("collection_paused", "1")
            paused_id = "01WEBHOOK-PAUSED"
            paused_payload = signed_payload(paused_id, created, body)
            paused_signature = base64.b64encode(
                _openssl("dgst", "-sha256", "-sign", str(key), input_bytes=paused_payload).stdout
            ).decode("ascii")
            paused = self._post_webhook(webhook.server_port, body, paused_id, created, paused_signature)
            self.assertEqual(paused, 204)
            _rows, total = self.store.list_donations()
            self.assertEqual(total, 1)
            rejected = self._post_webhook(webhook.server_port, body + b" ", paused_id, created, paused_signature)
            self.assertEqual(rejected, 401)
        finally:
            webhook.shutdown()
            webhook.server_close()

    def test_soft_disconnect_keeps_credentials_and_refuses_poll(self):
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("youtube_refresh_token", "refresh-token")
        self.store.set_setting("kick_client_secret", "kick-secret")
        status, _headers, body = self._request("/api/collection", method="POST", body={"paused": True})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["paused"])
        self.assertIn("paused", payload["platforms"]["youtube"]["detail"])
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")
        self.assertEqual(self.store.get_setting("youtube_refresh_token"), "refresh-token")
        self.assertEqual(self.store.get_setting("kick_client_secret"), "kick-secret")

        status, _headers, body = self._request("/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["paused"])

        status, _headers, body = self._request("/api/poll", method="POST", body={})
        polled = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(polled["paused"])
        self.assertFalse(polled["ok"])
        self.assertIn("paused", polled["errors"][0])

        status, _headers, body = self._request("/api/kick/subscribe", method="POST", body={})
        self.assertEqual(status, 400)
        self.assertIn(b"paused", body)

        status, _headers, body = self._request("/api/settings", method="PUT", body={"membership_price": "4.99"})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.get_setting("collection_paused"), "1")
        self.assertEqual(self.store.get_setting("membership_price"), "4.99")
        self.assertNotIn(b"kick-secret", body)
        self.assertNotIn(b"refresh-token", body)

        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertNotIn(b'id="soft-disconnect"', page)
        self.assertNotIn(b'id="new-stream"', page)
        self.assertNotIn(b'id="full-ledger"', page)
        self.assertIn(b'id="stream-tab-add"', page)
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"stream-tab-pause", script)
        self.assertIn(b"Soft disconnect", script)
        self.assertIn(b"unplug", script)

        status, _headers, body = self._request("/api/collection", method="POST", body={"paused": "yes"})
        self.assertEqual(status, 400)

        self.store.delete_setting("youtube_watch")
        status, _headers, body = self._request("/api/collection", method="POST", body={"paused": False})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["paused"])
        self.assertEqual(self.store.get_setting("collection_paused"), "0")
        self.assertEqual(self.store.get_setting("youtube_refresh_token"), "refresh-token")

    def test_collection_pause_follows_the_open_ledger_only(self):
        self.store.set_setting("youtube_watch", "abcdefghijk")
        status, _headers, body = self._request(
            "/api/collection", method="POST", body={"paused": False, "id": None}
        )
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["paused"])
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")

        status, _headers, body = self._request("/api/streams", method="POST", body={"action": "add"})
        report = json.loads(body)
        self.assertEqual(status, 200)
        new_id = report["streams"]["active_id"]
        first_id = next(tab["id"] for tab in report["streams"]["tabs"] if tab["id"] != new_id)
        new_tab = next(tab for tab in report["streams"]["tabs"] if tab["id"] == new_id)
        first_tab = next(tab for tab in report["streams"]["tabs"] if tab["id"] == first_id)
        self.assertTrue(new_tab["paused"])
        self.assertFalse(first_tab["paused"])
        self.assertEqual(self.store.get_setting("collection_paused"), "1")
        self.assertEqual(self.store.get_setting("youtube_watch"), "")

        status, _headers, body = self._request(
            "/api/collection", method="POST", body={"paused": False, "id": new_id}
        )
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["paused"])

        status, _headers, body = self._request(
            "/api/collection", method="POST", body={"paused": True, "id": first_id}
        )
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(payload["paused"])
        self.assertEqual(self.store.get_setting("collection_paused"), "0")
        saved = {tab["id"]: tab["paused"] for tab in payload["streams"]["tabs"]}
        self.assertTrue(saved[first_id])
        self.assertFalse(saved[new_id])
        self.assertEqual(self.store.get_setting("youtube_refresh_token", "kept"), "kept")

        status, _headers, body = self._request(
            "/api/collection", method="POST", body={"paused": True, "id": "missing"}
        )
        self.assertEqual(status, 400)
        self.assertIn(b"Unknown stream tab.", body)
        self.assertEqual(self.store.get_setting("collection_paused"), "0")

        status, _headers, body = self._request("/api/status")
        messages = " ".join(row["message"] for row in json.loads(body)["log"])
        self.assertIn("Paused that ledger", messages)
        self.assertNotIn("abcdefghijk", messages)

    def test_manual_entry_stream_total_and_new_stream(self):
        status, _headers, body = self._request(
            "/api/donations",
            method="POST",
            body={
                "author_name": "Ada",
                "platform": "other",
                "kind": "donation",
                "amount": "12.50",
                "message": "Venmo",
                "created_at": "2020-01-01T00:00:00Z",
            },
        )
        self.assertEqual(status, 201)
        saved = json.loads(body)
        self.assertEqual(saved["kind"], "donation")
        self.assertEqual(saved["quoted_amount"], 12.5)
        self.assertEqual(saved["platform"], "other")

        status, _headers, body = self._request("/api/stream")
        report = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(report["total_usd"], 12.5)
        self.assertEqual(report["scope"], "ledger")
        self.assertEqual(report["donors"]["rows"][0]["donor"], "Ada")
        self.assertIn("Ada: $12.50", report["donor_bar"])
        self.assertNotIn("@", report["donor_bar"])
        self.assertIn("@Ada: $12.50", report["donor_bar_copy"])

        status, _headers, body = self._request("/api/settings", method="PUT", body={
            "tiers_enabled": True,
            "goal_tiers": [{"enabled": True, "amount": 25, "name": "Bronze", "bonus": ""}],
            "chat_extra": "",
            "show_top_donors": True,
        })
        self.assertEqual(status, 200)
        status, _headers, body = self._request("/api/stream")
        report = json.loads(body)
        self.assertEqual(report["chat_line"], "$12.50 total for stream. $12.50 from Bronze")

        status, _headers, body = self._request("/api/stream", method="POST", body={"action": "start"})
        report = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(report["scope"], "ledger")
        self.assertEqual(report["total_usd"], 12.5)
        self.assertEqual(report["count"], 1)

        status, _headers, _body = self._request(
            "/api/donations",
            method="POST",
            body={"author_name": "Ben", "platform": "youtube", "kind": "super_chat", "amount": 5},
        )
        self.assertEqual(status, 201)
        status, _headers, body = self._request("/api/stream")
        report = json.loads(body)
        self.assertEqual(report["total_usd"], 17.5)
        self.assertEqual(report["donors"]["rows"][0]["donor"], "Ada")
        status, _headers, body = self._request("/api/donations?platform=other")
        page = json.loads(body)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["donations"][0]["author_name"], "Ada")

        missing, _headers, body = self._request(
            "/api/donations",
            method="POST",
            body={"author_name": "", "platform": "other", "kind": "donation", "amount": 1},
        )
        self.assertEqual(missing, 400)

    def test_manual_duplicate_asks_before_a_second_copy(self):
        gift = {
            "author_name": "@Ada",
            "platform": "youtube",
            "kind": "super_chat",
            "amount": "5",
            "currency": "USD",
            "message": "Hello",
        }
        status, _headers, body = self._request("/api/donations", method="POST", body=gift)
        self.assertEqual(status, 201)
        status, _headers, body = self._request("/api/donations", method="POST", body=gift)
        self.assertEqual(status, 409)
        warning = json.loads(body)
        self.assertTrue(warning["duplicate"])
        self.assertEqual(warning["author_name"], "Ada")
        self.assertEqual(warning["amount"], 5)
        self.assertEqual(warning["currency"], "USD")
        status, _headers, _body = self._request(
            "/api/donations",
            method="POST",
            body={**gift, "message": "Thanks"},
        )
        self.assertEqual(status, 201)
        status, _headers, _body = self._request(
            "/api/donations",
            method="POST",
            body={**gift, "confirm_duplicate": "yes"},
        )
        self.assertEqual(status, 409)
        status, _headers, body = self._request(
            "/api/donations",
            method="POST",
            body={**gift, "confirm_duplicate": True},
        )
        self.assertEqual(status, 201)
        listed = json.loads(self._request("/api/donations")[2])
        self.assertEqual(listed["total"], 3)

        first = json.loads(self._request("/api/stream")[2])["streams"]["active_id"]
        status, _headers, _body = self._request("/api/streams", method="POST", body={"action": "add"})
        self.assertEqual(status, 200)
        status, _headers, _body = self._request("/api/donations", method="POST", body=gift)
        self.assertEqual(status, 201)
        status, _headers, _body = self._request(
            "/api/streams", method="POST", body={"action": "switch", "id": first}
        )
        self.assertEqual(status, 200)
        collected = Donation(
            dedupe_key="yt:chat:1",
            platform="youtube",
            kind="super_chat",
            external_id="1",
            channel="",
            author_name="@Fan",
            author_id="",
            message="Hello",
            amount_value=1,
            amount_micros=1_000_000,
            currency="USD",
            created_at="2024-06-01T12:00:00Z",
            raw={},
        )
        again = Donation(
            dedupe_key="yt:chat:2",
            platform="youtube",
            kind="super_chat",
            external_id="2",
            channel="",
            author_name="Fan",
            author_id="",
            message="Hello",
            amount_value=1,
            amount_micros=1_000_000,
            currency="USD",
            created_at="2024-06-01T12:05:00Z",
            raw={},
        )
        self.assertEqual(self.store.add_many([collected, again]), 2)
        status, _headers, body = self._request(
            "/api/donations",
            method="POST",
            body={
                "author_name": "Fan",
                "platform": "youtube",
                "kind": "super_chat",
                "amount": "1.00",
                "message": "Hello",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["author_name"], "Fan")
        _status, _headers, script = self._request("/app.js")
        self.assertIn(b"confirm_duplicate: true", script)
        self.assertIn(b"Save this one too?", script)

    def test_manual_row_can_be_edited_from_the_ledger(self):
        status, _headers, body = self._request("/api/donations", method="POST", body={
            "author_name": "@Ada",
            "platform": "youtube",
            "kind": "super_chat",
            "amount": "5.00",
            "currency": "USD",
            "message": "Hello",
            "created_at": "2024-06-01T12:00:00Z",
        })
        self.assertEqual(status, 201)
        created = json.loads(body)
        donation_id = created["id"]
        self.assertTrue(str(created["dedupe_key"]).startswith("manual:"))

        status, _headers, body = self._request(
            f"/api/donations/{donation_id}",
            method="PATCH",
            body={"note": "keep me", "processed": True},
        )
        self.assertEqual(status, 200)
        marked = json.loads(body)
        self.assertEqual(marked["note"], "keep me")
        self.assertTrue(marked["processed"])
        self.assertTrue(marked["processed_at"])

        edited_fields = {
            "author_name": "@Bea",
            "platform": "kick",
            "kind": "donation",
            "amount": "7.50",
            "currency": "PHP",
            "message": "Edited",
        }
        status, _headers, body = self._request(
            f"/api/donations/{donation_id}",
            method="PATCH",
            body=edited_fields,
        )
        self.assertEqual(status, 200)
        edited = json.loads(body)
        self.assertEqual(edited["id"], donation_id)
        self.assertEqual(edited["dedupe_key"], created["dedupe_key"])
        self.assertEqual(edited["external_id"], created["external_id"])
        self.assertEqual(edited["author_name"], "Bea")
        self.assertEqual(edited["platform"], "kick")
        self.assertEqual(edited["kind"], "donation")
        self.assertEqual(edited["amount_value"], 7.5)
        self.assertEqual(edited["amount_micros"], 7_500_000)
        self.assertEqual(edited["currency"], "PHP")
        self.assertEqual(edited["message"], "Edited")
        self.assertEqual(edited["created_at"], created["created_at"])
        self.assertEqual(edited["received_at"], created["received_at"])
        self.assertEqual(edited["note"], "keep me")
        self.assertTrue(edited["processed"])
        self.assertEqual(edited["processed_at"], marked["processed_at"])
        self.assertFalse(edited["hidden"])
        listed = json.loads(self._request("/api/donations")[2])
        self.assertEqual(listed["total"], 1)
        log = json.loads(self._request("/api/status")[2])["log"]
        self.assertTrue(any(
            row["message"] == "Edited a manual donation from Bea for 7.50 PHP."
            for row in log
        ))

        status, _headers, body = self._request(
            f"/api/donations/{donation_id}",
            method="PATCH",
            body=edited_fields,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["id"], donation_id)
        self.assertEqual(json.loads(self._request("/api/donations")[2])["total"], 1)

        status, _headers, body = self._request(
            f"/api/donations/{donation_id}",
            method="PATCH",
            body={**edited_fields, "created_at": "2024-07-04T15:30:00Z"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["created_at"], "2024-07-04T15:30:00Z")
        status, _headers, body = self._request(
            f"/api/donations/{donation_id}",
            method="PATCH",
            body={**edited_fields, "created_at": "  "},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["created_at"], "2024-07-04T15:30:00Z")

        status, _headers, _body = self._request("/api/donations", method="POST", body={
            "author_name": "Cara",
            "platform": "youtube",
            "kind": "super_chat",
            "amount": "3.00",
            "currency": "USD",
            "message": "Other",
        })
        self.assertEqual(status, 201)
        match = {
            "author_name": "Cara",
            "platform": "youtube",
            "kind": "super_chat",
            "amount": "3.00",
            "currency": "USD",
            "message": "Other",
        }
        status, _headers, body = self._request(
            f"/api/donations/{donation_id}",
            method="PATCH",
            body={**match, "confirm_duplicate": "yes"},
        )
        self.assertEqual(status, 409)
        status, _headers, body = self._request(
            f"/api/donations/{donation_id}",
            method="PATCH",
            body=match,
        )
        self.assertEqual(status, 409)
        self.assertTrue(json.loads(body)["duplicate"])
        self.assertEqual(json.loads(body)["author_name"], "Cara")
        status, _headers, body = self._request(
            f"/api/donations/{donation_id}",
            method="PATCH",
            body={**match, "confirm_duplicate": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["id"], donation_id)
        self.assertEqual(json.loads(body)["author_name"], "Cara")
        self.assertEqual(json.loads(self._request("/api/donations")[2])["total"], 2)

        self.store.add_many([Donation(
            dedupe_key="yt:chat:edit-1",
            platform="youtube",
            kind="super_chat",
            external_id="edit-1",
            channel="",
            author_name="HiddenFan",
            author_id="",
            message="automatic",
            amount_value=10,
            amount_micros=10_000_000,
            currency="USD",
            created_at="2024-06-01T12:00:00Z",
            raw={},
        )])
        rows = json.loads(self._request("/api/donations")[2])["donations"]
        automatic = next(row for row in rows if row["author_name"] == "HiddenFan")
        self.assertFalse(automatic["manual"])
        status, _headers, body = self._request(
            f"/api/donations/{automatic['id']}",
            method="PATCH",
            body={"author_name": "Nope", "platform": "youtube", "kind": "super_chat", "amount": 1},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "Automatic entries cannot be edited.")
        again = json.loads(self._request(f"/api/donations/{automatic['id']}")[2])
        self.assertEqual(again["author_name"], "HiddenFan")
        self.assertEqual(again["message"], "automatic")
        self.assertEqual(again["amount_value"], 10)
        status, _headers, body = self._request(
            f"/api/donations/{automatic['id']}",
            method="PATCH",
            body={"note": "still a note"},
        )
        self.assertEqual(status, 200)
        noted = json.loads(body)
        self.assertEqual(noted["note"], "still a note")
        self.assertEqual(noted["author_name"], "HiddenFan")

        status, _headers, body = self._request(
            "/api/donations/999999",
            method="PATCH",
            body={"author_name": "X", "platform": "other", "kind": "donation", "amount": 1},
        )
        self.assertEqual(status, 404)

        original_stream = json.loads(self._request("/api/stream")[2])["streams"]["active_id"]
        status, _headers, _body = self._request("/api/streams", method="POST", body={"action": "add"})
        self.assertEqual(status, 200)
        status, _headers, body = self._request(
            f"/api/donations/{donation_id}",
            method="PATCH",
            body={"author_name": "Gone", "platform": "other", "kind": "donation", "amount": 1},
        )
        self.assertEqual(status, 404)
        status, _headers, _body = self._request(
            "/api/streams",
            method="POST",
            body={"action": "switch", "id": original_stream},
        )
        self.assertEqual(status, 200)
        kept = json.loads(self._request(f"/api/donations/{donation_id}")[2])
        self.assertEqual(kept["author_name"], "Cara")
        self.assertEqual(kept["dedupe_key"], created["dedupe_key"])

        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Hover a manual entry to edit or delete it.", page)
        self.assertEqual(page.count(b"Hover an automatic entry"), 1)
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        action = script.split(b"function rowAction(", 1)[1].split(b"\nfunction ", 1)[0]
        manual, automatic_action = action.split(b"} else {", 1)
        self.assertIn(b'aria-label", "Edit"', manual)
        self.assertIn(b'aria-label", "Delete"', manual)
        self.assertLess(manual.index(b'aria-label", "Edit"'), manual.index(b'aria-label", "Delete"'))
        self.assertIn(b'icon("pencil")', manual)
        self.assertIn(b'icon("trash")', manual)
        self.assertNotIn(b"Edit", automatic_action)
        self.assertNotIn(b"pencil", automatic_action)
        self.assertIn(b"eye-off", automatic_action)
        self.assertIn(b"M12 20h9", script)
        self.assertIn(b"M5 12.5 9.5 17 19 7", script)
        self.assertIn(b"function beginEdit(", script)
        self.assertIn(b"function setManualEditing(", script)
        self.assertIn(b"function saveManualEdit(", script)
        self.assertIn(b"Saving updates this manual entry.", script)
        self.assertIn(b': "Saved."', script)
        self.assertIn(b"That entry is no longer in the ledger.", script)
        refresh = script.split(b"async function refresh(", 1)[1].split(b"\nfunction ", 1)[0]
        self.assertIn(b"cancelManualEdit()", refresh)
        self.assertNotIn(b'manual-entry").reset()', refresh)
        rows_fn = script.split(b"function renderRows(", 1)[1].split(b"\nfunction ", 1)[0]
        self.assertIn(b"is-editing", rows_fn)
        status, _headers, styles = self._request("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(b"tbody tr:hover .row-actions .icon-button", styles)
        self.assertIn(b"tbody tr.is-editing .row-actions .icon-button", styles)

    def test_stream_tabs_round_trip(self):
        status, _headers, body = self._request("/api/settings", method="PUT", body={
            "membership_price": "4.99",
            "chat_extra": "hi",
        })
        self.assertEqual(status, 200)
        status, _headers, body = self._request("/api/streams", method="POST", body={
            "action": "rename",
            "title": "Friday",
        })
        report = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(report["title"], "Friday")
        self.assertEqual(len(report["streams"]["tabs"]), 1)

        status, _headers, body = self._request("/api/streams", method="POST", body={"action": "add"})
        report = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(report["streams"]["tabs"]), 2)
        self.assertEqual(report["title"], "")
        self.assertEqual(report["scope"], "ledger")
        new_id = report["streams"]["active_id"]
        first_id = next(tab["id"] for tab in report["streams"]["tabs"] if tab["id"] != new_id)

        status, _headers, _body = self._request("/api/settings", method="PUT", body={"membership_price": "8.00"})
        self.assertEqual(status, 200)
        status, _headers, body = self._request("/api/streams", method="POST", body={
            "action": "switch",
            "id": first_id,
        })
        report = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(report["scope"], "ledger")
        self.assertEqual(report["title"], "Friday")
        status, _headers, body = self._request("/api/settings")
        settings = json.loads(body)
        self.assertEqual(settings["membership_price"], "4.99")
        self.assertEqual(settings["chat_extra"], "hi")
        self.assertEqual(settings["stream_title"], "Friday")

        status, _headers, body = self._request("/api/streams", method="POST", body={"action": "nope"})
        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(body)["error"],
            "Stream tab action must be add, switch, close, or rename.",
        )
        status, _headers, body = self._request("/api/streams", method="POST", body={
            "action": "rename",
            "title": "x" * 81,
        })
        self.assertEqual(status, 400)
        status, _headers, body = self._request("/api/streams", method="POST", body={
            "action": "close",
            "id": first_id,
        })
        self.assertEqual(status, 200)
        status, _headers, body = self._request("/api/streams", method="POST", body={
            "action": "close",
            "id": new_id,
        })
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "The last stream tab stays.")
        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="ledger-urls"', page)
        self.assertIn(b"Addresses for this ledger", page)
        self.assertIn(b'aria-label="New ledger"', page)
        self.assertIn(b'id="ledger-youtube-follow"', page)
        self.assertIn(b'id="ledger-rumble-follow"', page)
        self.assertNotIn(b'id="ledger-kick-follow"', page)
        self.assertIn(b"Follow the ", page)
        self.assertIn(b'class="platform-name" data-platform="youtube"', page)
        self.assertIn(b'class="platform-name" data-platform="rumble"', page)
        self.assertIn(b'class="platform-name" data-platform="kick"', page)
        self.assertIn(b'src="/icons/youtube.svg"', page)
        self.assertIn(b'src="/icons/rumble.svg"', page)
        self.assertIn(b'src="/icons/kick.svg"', page)
        self.assertIn(b"YouTube public video URL or id", page)
        self.assertIn(b"Rumble public video URL or chat id", page)
        self.assertIn(b"public channel", page)
        self.assertIn(b'data-channel-label="YouTube channel URL"', page)
        self.assertIn(b'data-channel-label="Rumble channel URL"', page)
        self.assertIn(b'data-channel-placeholder="https://www.youtube.com/@channel"', page)
        self.assertIn(b'data-channel-placeholder="https://rumble.com/c/channel"', page)
        self.assertIn(b'placeholder="https://kick.com/channel"', page)
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b'id="ledger-name"', page)
        self.assertIn(b'id="stream-title"', page)
        self.assertIn(b'id="stream-banner-body"', page)
        self.assertIn(b'id="top-donors-body"', page)
        self.assertIn(b'id="manual-entry-body"', page)
        form = page.split(b'<form id="manual-entry"', 1)[1].split(b"</form>", 1)[0]
        shown, rest = form.split(b'id="manual-entry-body"', 1)
        self.assertIn(b'id="manual-donor"', shown)
        self.assertIn(b'id="manual-platform"', shown)
        self.assertIn(b'id="manual-kind"', shown)
        self.assertIn(b'id="manual-amount"', shown)
        self.assertIn(b'class="manual-native"', shown)
        self.assertIn(b'class="manual-currency"', shown)
        self.assertNotIn(b'id="manual-message"', shown)
        self.assertIn(b'id="manual-add"', rest)
        self.assertIn(b'id="manual-paste"', rest)
        self.assertIn(b'id="manual-clear"', rest)
        add = rest.split(b'id="manual-add"', 1)[1].split(b"</button>", 1)[0]
        self.assertIn(b'aria-label="Add"', add)
        self.assertIn(b"M12 5v14M5 12h14", add)
        self.assertNotIn(b">Add<", add)
        status, _headers, styles = self._request("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(b"#manual-entry.is-collapsed .manual-native", styles)
        self.assertIn(b"#manual-entry.is-collapsed .manual-currency { display: none; }", styles)
        self.assertIn(b'class="icon-button section-toggle"', page)
        self.assertIn(b'aria-label="Expand stream total"', page)
        self.assertIn(b'aria-label="Expand top donors"', page)
        self.assertIn(b'aria-label="Expand manual entry"', page)
        self.assertIn(b'aria-label="Copy stream total"', page)
        self.assertIn(b'aria-label="Copy top 3 donors"', page)
        banner_head = page.split(b'id="stream-banner"', 1)[1].split(b'id="stream-banner-body"', 1)[0]
        donor_head = page.split(b'id="top-donors"', 1)[1].split(b'id="top-donors-body"', 1)[0]
        self.assertIn(b'id="copy-chat-line"', banner_head)
        self.assertIn(b"const line = streamChatLine(Number(report.total_usd || 0), report)", script)
        self.assertIn(
            b'$("stream-summary").textContent = `${report.count} ${countLabel}. ${line}`',
            script,
        )
        self.assertNotIn(
            b"${formatStreamUsd(report.total_usd)} total for stream.",
            script,
        )
        self.assertIn(b'id="copy-donor-bar"', donor_head)
        self.assertIn(b"function topDonorCopyText()", script)
        self.assertIn(b"item.copy || item.line", script)
        self.assertIn(b'class="app-body"', page)
        self.assertIn(b'id="app-status"', page)
        self.assertIn(b'class="status-bar"', page)
        self.assertIn(b'id="status-total-line"', page)
        self.assertIn(b'id="status-copy-total"', page)
        self.assertIn(b'id="status-donor-line"', page)
        self.assertIn(b'id="status-copy-donors"', page)
        self.assertIn(b'id="status-ephemeral"', page)
        self.assertLess(page.index(b'class="app-body"'), page.index(b'id="watch-panel"'))
        self.assertLess(page.index(b'id="tab-ledger"'), page.index(b'id="app-status"'))
        self.assertLess(page.index(b'id="status-total"'), page.index(b'id="status-donors"'))
        self.assertLess(page.index(b'id="status-donors"'), page.index(b'id="status-ephemeral"'))
        stream = script.split(b"function renderStream(", 1)[1].split(b"\nfunction ", 1)[0]
        self.assertIn(b"status-donor-line", stream)
        preview = script.split(b"function updateStreamPreview(", 1)[1].split(b"\nfunction ", 1)[0]
        self.assertIn(b"status-total-line", preview)
        ledger_refresh = script.split(b"async function refresh(", 1)[1].split(b"\nasync function ", 1)[0]
        self.assertNotIn(b"status-ephemeral", ledger_refresh)
        self.assertNotIn(b"/api/giveaway", ledger_refresh)
        self.assertIn(b"M9 6l6 6-6 6", page)
        self.assertIn(b"is-collapsed", page)
        self.assertNotIn(b"stream-name", page)
        self.assertIn(b"stream-tab-addresses", script)
        self.assertIn(b"openLedgerAddresses", script)
        self.assertIn(b"openLedgerName", script)
        self.assertIn(b"promptLedgerName", script)
        self.assertIn(b"bindSectionToggles", script)
        self.assertNotIn(b"pinCompactUsd", script)
        self.assertIn(b'message: $("manual-message").value.trim()', script)
        self.assertIn(b"The ledger keeps that amount, currency, and message.", page)
        self.assertIn(b'dblclick', script)
        self.assertNotIn(b'stream-title").addEventListener("blur"', script)
        self.assertIn(b"fresh: false", script)
        self.assertIn(b"fillLedgerAddressFields", script)
        self.assertIn(b"function ledgerAddressPayload()", script)
        self.assertIn(b'followKey: "youtube_follow_live"', script)
        self.assertIn(b'channelKey: "youtube_channel"', script)
        self.assertIn(b'followKey: "rumble_follow_live"', script)
        self.assertIn(b'channelKey: "rumble_channel"', script)
        self.assertIn(b"kick_watch:", script)
        self.assertNotIn(b'followKey: "kick_follow_live"', script)

    def test_settings_file_export_leaves_out_secrets(self):
        self.store.set_setting("youtube_client_id", "client-id-value")
        self.store.set_setting("youtube_client_secret", "super-secret-value")
        self.store.set_setting("youtube_refresh_token", "refresh-token-value")
        self.store.set_setting("kick_client_secret", "kick-secret-value")
        self.store.set_setting("rumble_api_url", "https://rumble.com/-livestream-api/get-data?key=SECRETKEY")
        status, _headers, body = self._request("/api/settings", method="PUT", body={
            "membership_price": "4.99",
            "tiers_enabled": True,
            "show_top_donors": False,
            "chat_extra": "be nice",
            "goal_tiers": [{"enabled": True, "amount": 25, "name": "Bronze", "bonus": ""}],
        })
        self.assertEqual(status, 200)

        status, headers, body = self._request("/api/settings/export", cookie=False)
        self.assertEqual(status, 401)

        status, headers, body = self._request("/api/settings/export")
        self.assertEqual(status, 200)
        self.assertIn("stream-donations-settings.json", headers.get("Content-Disposition", ""))
        self.assertNotIn(b"super-secret-value", body)
        self.assertNotIn(b"refresh-token-value", body)
        self.assertNotIn(b"kick-secret-value", body)
        self.assertNotIn(b"SECRETKEY", body)
        self.assertNotIn(b"client-id-value", body)
        self.assertNotIn(b"youtube_", body)
        self.assertNotIn(b"rumble_", body)
        self.assertNotIn(b"kick_", body)
        self.assertNotIn(b": ", body)
        document = json.loads(body)
        self.assertEqual(document["kind"], "stream-donations-settings")
        self.assertEqual(document["version"], 1)
        self.assertEqual(set(document), {"kind", "version", "settings"})
        self.assertEqual(document["settings"]["membership_price"], "4.99")
        self.assertIs(document["settings"]["show_top_donors"], False)
        self.assertEqual(document["settings"]["chat_extra"], "be nice")
        self.assertNotIn("tiers_enabled", document["settings"])
        self.assertNotIn("goal_tiers", document["settings"])
        self.assertNotIn(b"Bronze", body)
        self.assertEqual(document["settings"]["ledger_page_size"], 100)
        self.assertEqual(
            list(document["settings"]),
            ["membership_price", "show_top_donors", "chat_extra", "ledger_page_size"],
        )
        kept = json.loads(self._request("/api/settings")[2])
        self.assertIs(kept["tiers_enabled"], True)
        self.assertEqual(kept["goal_tiers"][0]["name"], "Bronze")

        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Export settings", page)
        self.assertIn(b"Import settings", page)
        self.assertIn(b"/api/settings/export", page)
        self.assertIn(b'id="ledger-page-size"', page)
        self.assertIn(b'value="custom"', page)

    def test_ledger_page_size_is_shared_and_bounded(self):
        status, _headers, body = self._request("/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ledger_page_size"], 100)
        self.assertEqual(self.store.get_setting("ledger_page_size"), "")

        for size in (10, 25, 50, 100, 40):
            status, _headers, body = self._request("/api/settings", method="PUT", body={"ledger_page_size": size})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["ledger_page_size"], size)
            self.assertEqual(self.store.get_setting("ledger_page_size"), str(size))

        status, _headers, body = self._request("/api/streams", method="POST", body={"action": "add"})
        self.assertEqual(status, 200)
        report = json.loads(body)
        second = report["streams"]["active_id"]
        first = next(tab["id"] for tab in report["streams"]["tabs"] if tab["id"] != second)
        self.assertEqual(json.loads(self._request("/api/settings")[2])["ledger_page_size"], 40)
        status, _headers, body = self._request("/api/streams", method="POST", body={"action": "switch", "id": first})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(self._request("/api/settings")[2])["ledger_page_size"], 40)

        status, _headers, body = self._request("/api/settings", method="PUT", body={
            "membership_price": "4.99",
            "ledger_page_size": 0,
        })
        self.assertEqual(status, 400)
        self.assertIn(b"Ledger page size", body)
        self.assertEqual(self.store.get_setting("ledger_page_size"), "40")
        self.assertEqual(self.store.get_setting("membership_price"), "")
        for bad in (501, "nope", 10.5, True):
            status, _headers, body = self._request("/api/settings", method="PUT", body={"ledger_page_size": bad})
            self.assertEqual(status, 400, bad)
            self.assertEqual(self.store.get_setting("ledger_page_size"), "40")

        status, _headers, page = self._request("/")
        self.assertIn(b"syncLedgerPageSize", self._request("/app.js")[2])
        self.assertIn(b"How many donations each page shows", page)

    def test_settings_file_import_applies_only_the_open_tab(self):
        self.store.set_setting("youtube_client_id", "kept-client")
        self.store.set_setting("youtube_client_secret", "original-secret")
        status, _headers, body = self._request("/api/settings", method="PUT", body={
            "membership_price": "4.99",
            "chat_extra": "keep-me",
        })
        self.assertEqual(status, 200)
        status, _headers, body = self._request("/api/streams", method="POST", body={"action": "add"})
        report = json.loads(body)
        self.assertEqual(status, 200)
        new_id = report["streams"]["active_id"]
        first_id = next(tab["id"] for tab in report["streams"]["tabs"] if tab["id"] != new_id)
        status, _headers, body = self._request("/api/settings", method="PUT", body={"membership_price": "8.00"})
        self.assertEqual(status, 200)

        raw = json.dumps({
            "kind": "stream-donations-settings",
            "version": 4,
            "later": {"note": "from a newer app"},
            "settings": {
                "membership_price": "1.25",
                "youtube_client_id": "other-client",
                "youtube_client_secret": "imported-secret",
                "theme": "dark",
            },
        }, separators=(",", ":")).encode("utf-8")
        status, _headers, body = self._request("/api/settings/import", method="POST", cookie=False, raw=raw)
        self.assertEqual(status, 401)
        self.assertEqual(self.store.get_setting("membership_price"), "8.00")

        status, _headers, body = self._request("/api/settings/import", method="POST", raw=raw)
        self.assertEqual(status, 200)
        self.assertNotIn(b"imported-secret", body)
        self.assertNotIn(b"original-secret", body)
        payload = json.loads(body)
        self.assertEqual(payload["membership_price"], "1.25")
        self.assertEqual(payload["chat_extra"], "keep-me")
        self.assertEqual(payload["youtube_client_id"], "kept-client")
        self.assertTrue(payload["youtube_client_secret_set"])
        self.assertEqual(payload["settings_import"]["version"], 4)
        self.assertEqual(
            payload["settings_import"]["ignored"],
            ["later", "theme", "youtube_client_id", "youtube_client_secret"],
        )
        self.assertEqual(self.store.get_setting("youtube_client_secret"), "original-secret")
        self.assertEqual(self.store.get_setting("youtube_client_id"), "kept-client")
        self.assertEqual(self.store.get_setting("theme"), "")
        self.assertEqual(self.store.recent_log()[0]["message"], "Imported settings for the open stream tab.")

        status, _headers, body = self._request("/api/streams", method="POST", body={
            "action": "switch",
            "id": first_id,
        })
        self.assertEqual(status, 200)
        status, _headers, body = self._request("/api/settings")
        settings = json.loads(body)
        self.assertEqual(settings["membership_price"], "4.99")
        self.assertEqual(settings["chat_extra"], "keep-me")

        status, _headers, body = self._request("/api/streams", method="POST", body={
            "action": "switch",
            "id": new_id,
        })
        self.assertEqual(status, 200)
        omitted = json.dumps({
            "kind": "stream-donations-settings",
            "version": 1,
            "settings": {"chat_extra": "updated"},
        }).encode("utf-8")
        status, _headers, body = self._request("/api/settings/import", method="POST", raw=omitted)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["membership_price"], "1.25")
        self.assertEqual(json.loads(body)["chat_extra"], "updated")

        cleared = json.dumps({
            "kind": "stream-donations-settings",
            "version": 1,
            "settings": {"membership_price": ""},
        }).encode("utf-8")
        status, _headers, body = self._request("/api/settings/import", method="POST", raw=cleared)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["membership_price"], "")
        self.assertEqual(self.store.get_setting("membership_price"), "")
        self.assertEqual(json.loads(body)["chat_extra"], "updated")

        status, _headers, _body = self._request("/api/settings", method="PUT", body={"membership_price": "4.99", "chat_extra": "stay"})
        self.assertEqual(status, 200)
        rejected = json.dumps({
            "kind": "stream-donations-settings",
            "version": 1,
            "settings": {"membership_price": "nope", "chat_extra": "changed"},
        }).encode("utf-8")
        status, _headers, body = self._request("/api/settings/import", method="POST", raw=rejected)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "Membership price must be a dollar amount.")
        self.assertEqual(self.store.get_setting("membership_price"), "4.99")
        self.assertEqual(self.store.get_setting("chat_extra"), "stay")

        status, _headers, body = self._request("/api/settings/import", method="POST", raw=b'{"kind":"ledger","version":1}')
        self.assertEqual(status, 400)
        self.assertIn(b"not a SuperBerry settings file", body)
        self.assertEqual(self.store.get_setting("membership_price"), "4.99")

    def test_manual_entry_keeps_php(self):
        status, _headers, body = self._request("/api/donations", method="POST", body={
            "author_name": "kayeend",
            "platform": "youtube",
            "kind": "super_chat",
            "amount": "50.00",
            "currency": "PHP",
            "message": "juuuuuuuuuuuuicy tip",
        })
        self.assertEqual(status, 201)
        payload = json.loads(body)
        self.assertEqual(payload["currency"], "PHP")
        self.assertEqual(payload["amount_value"], 50.0)
        self.assertNotEqual(payload["currency"], "USD")

    def test_manual_currency_is_a_dropdown_of_supported_codes(self):
        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'<select id="manual-currency">', page)
        self.assertIn(b'<option value="USD" selected>', page)
        self.assertIn("United States Dollar".encode(), page)
        self.assertNotIn(b'id="manual-currency" maxlength', page)

        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"renderCurrencies", script)
        self.assertIn(b"DEFAULT_CURRENCY = \"USD\"", script)

        status, _headers, body = self._request("/api/meta")
        self.assertEqual(status, 200)
        currencies = json.loads(body)["currencies"]
        codes = [item["code"] for item in currencies]
        self.assertEqual(codes, sorted(codes))
        self.assertEqual(len(codes), 162)
        self.assertEqual(currencies[codes.index("USD")]["name"], "United States Dollar")
        self.assertIn("PHP", codes)

        status, _headers, body = self._request("/api/donations", method="POST", body={
            "author_name": "Ada",
            "platform": "other",
            "kind": "donation",
            "amount": "5.00",
            "currency": "XYZ",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"supported code", body)

    def test_hide_automatic_entries_and_delete_manual_ones(self):
        from streamdonations.normalize import normalize_super_chat_event

        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertNotIn(b'id="show-hidden"', page)
        self.assertIn(b"stays in the ledger", page)
        self.assertIn(b'class="row-actions"', page)

        status, _headers, body = self._request("/api/donations", method="POST", body={
            "author_name": "Ada",
            "platform": "other",
            "kind": "donation",
            "amount": "2.00",
            "currency": "USD",
        })
        self.assertEqual(status, 201)
        manual = json.loads(body)
        self.assertTrue(manual["manual"])
        manual_id = manual["id"]

        donation = normalize_super_chat_event({
            "id": "auto-1",
            "snippet": {
                "channelId": "UCb",
                "supporterDetails": {"displayName": "HiddenFan", "channelId": "UCf"},
                "commentText": "automatic",
                "createdAt": "2024-06-01T12:00:00Z",
                "amountMicros": "10000000",
                "currency": "USD",
            },
        })
        self.assertEqual(self.store.add_many([donation]), 1)
        rows, _total = self.store.list_donations(rates={"USD": 1.0})
        automatic_id = next(row["id"] for row in rows if row["author_name"] == "HiddenFan")

        status, _headers, body = self._request(f"/api/donations/{automatic_id}", method="DELETE")
        self.assertEqual(status, 400)
        self.assertIn(b"stays in the ledger", body)
        status, _headers, body = self._request(
            f"/api/donations/{manual_id}",
            method="PATCH",
            body={"hidden": True},
        )
        self.assertEqual(status, 400)
        self.assertIn(b"deleted, not hidden", body)

        status, _headers, body = self._request(
            f"/api/donations/{automatic_id}",
            method="PATCH",
            body={"hidden": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["hidden"])
        status, _headers, body = self._request("/api/donations")
        listed = json.loads(body)
        self.assertEqual([row["author_name"] for row in listed["donations"]], ["Ada", "HiddenFan"])
        parked = next(row for row in listed["donations"] if row["id"] == automatic_id)
        self.assertTrue(parked["hidden"])
        self.assertFalse(next(row["hidden"] for row in listed["donations"] if row["author_name"] == "Ada"))
        status, _headers, body = self._request("/api/summary")
        self.assertEqual(json.loads(body)["total_usd"], 2.0)
        status, _headers, body = self._request("/api/export.csv?include_hidden=1")
        self.assertNotIn(b"HiddenFan", body)
        self.assertIn(b"Ada", body)

        status, _headers, body = self._request(
            f"/api/donations/{automatic_id}",
            method="PATCH",
            body={"hidden": False},
        )
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["hidden"])

        status, _headers, body = self._request(f"/api/donations/{manual_id}", method="DELETE")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["removed"], manual_id)
        status, _headers, body = self._request("/api/donations")
        remaining = json.loads(body)
        self.assertEqual([row["author_name"] for row in remaining["donations"]], ["HiddenFan"])
        status, _headers, body = self._request(f"/api/donations/{manual_id}", method="DELETE")
        self.assertEqual(status, 404)

    def test_manual_delete_can_be_undone(self):
        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="toast"', page)
        self.assertIn(b"can undo that delete", page)
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertNotIn(b"Delete the manual entry from", script)
        self.assertNotIn(b"are you sure", script.lower())
        self.assertIn(b"/api/donations/restore", script)
        self.assertIn(b"Undo", script)
        status, _headers, css = self._request("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(b".toast-action", css)

        status, _headers, body = self._request("/api/donations", method="POST", body={
            "author_name": "Ada",
            "platform": "youtube",
            "kind": "super_chat",
            "amount": "50.00",
            "currency": "PHP",
            "message": "cash",
            "created_at": "2024-01-01T12:00:00Z",
        })
        self.assertEqual(status, 201)
        manual_id = json.loads(body)["id"]
        status, _headers, body = self._request(
            f"/api/donations/{manual_id}",
            method="PATCH",
            body={"note": "envelope", "processed": True},
        )
        self.assertEqual(status, 200)
        current = json.loads(body)
        status, _headers, body = self._request(f"/api/donations/{manual_id}", method="DELETE")
        self.assertEqual(status, 200)
        status, _headers, body = self._request("/api/donations/restore", method="POST", body={
            "id": current["id"],
            "dedupe_key": current["dedupe_key"],
            "author_name": current["author_name"],
            "platform": current["platform"],
            "kind": current["kind"],
            "amount": current["amount_value"],
            "currency": current["currency"],
            "message": current["message"],
            "created_at": current["created_at"],
            "received_at": current["received_at"],
            "processed": current["processed"],
            "processed_at": current["processed_at"],
            "note": current["note"],
            "raw": {"access_token": "do-not-keep"},
        })
        self.assertEqual(status, 201)
        restored = json.loads(body)
        self.assertEqual(restored["id"], manual_id)
        self.assertEqual(restored["author_name"], "Ada")
        self.assertEqual(restored["currency"], "PHP")
        self.assertEqual(restored["amount_value"], 50.0)
        self.assertEqual(restored["message"], "cash")
        self.assertEqual(restored["note"], "envelope")
        self.assertTrue(restored["processed"])
        self.assertEqual(restored["created_at"], "2024-01-01T12:00:00Z")
        self.assertEqual(restored["raw"], {"manual": True})
        status, _headers, body = self._request("/api/donations/restore", method="POST", body={
            "dedupe_key": current["dedupe_key"],
            "author_name": "Ada",
            "platform": "youtube",
            "kind": "super_chat",
            "amount": 50,
            "currency": "PHP",
            "created_at": "2024-01-01T12:00:00Z",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"already in the ledger", body)
        status, _headers, body = self._request("/api/donations/restore", method="POST", body={
            "dedupe_key": "youtube:automatic",
            "author_name": "Ada",
            "platform": "youtube",
            "kind": "super_chat",
            "amount": 50,
            "currency": "USD",
            "created_at": "2024-01-01T12:00:00Z",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"Only a deleted manual entry", body)

    def test_verbose_log_is_a_logs_page_control(self):
        from streamdonations.normalize import Donation

        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="verbose-logs"', page)
        self.assertIn(b'id="log-file"', page)

        status, _headers, body = self._request("/api/meta")
        meta = json.loads(body)
        self.assertTrue(meta["log_file"].endswith("stream-donations.log"))

        status, _headers, body = self._request("/api/status")
        self.assertEqual(status, 200)
        current = json.loads(body)
        self.assertFalse(current["verbose"])
        self.assertEqual(current["log_file"], meta["log_file"])
        self.assertIsInstance(current["log"], list)

        status, _headers, body = self._request("/api/log", method="PUT", cookie=False, body={"verbose": True})
        self.assertEqual(status, 401)
        status, _headers, body = self._request("/api/log", method="PUT", body={"verbose": "yes"})
        self.assertEqual(status, 400)
        self.assertIn(b"true or false", body)
        self.assertEqual(self.store.get_setting("verbose_logs"), "")

        status, _headers, body = self._request("/api/log", method="PUT", body={"verbose": True})
        self.assertEqual(status, 200)
        turned_on = json.loads(body)
        self.assertTrue(turned_on["verbose"])
        self.assertEqual(self.store.get_setting("verbose_logs"), "1")
        self.assertTrue(any("Verbose logging is on." in row["message"] for row in turned_on["log"]))

        status, _headers, body = self._request("/api/settings", method="PUT", body={"verbose_logs": "0"})
        self.assertEqual(status, 400)
        self.assertEqual(self.store.get_setting("verbose_logs"), "1")
        status, _headers, body = self._request("/api/settings/export")
        self.assertEqual(status, 200)
        self.assertNotIn(b"verbose_logs", body)
        self.assertNotIn(b"payload-secret-value", body)

        secret = "payload-secret-value"
        donation = Donation(
            dedupe_key="youtube:test:verbose",
            platform="youtube",
            kind="super_chat",
            external_id="verbose",
            channel="",
            author_name="kayeend",
            author_id="",
            message="tip",
            amount_value=50.0,
            amount_micros=50_000_000,
            currency="PHP",
            created_at="2026-10-01T04:40:52Z",
            raw={
                "authorName": {"simpleText": "kayeend"},
                "purchaseAmountText": {"simpleText": "₱50.00"},
                "access_token": secret,
                "authorPhoto": {"thumbnails": [{"url": "https://example.test/thumb.jpg"}]},
            },
        )
        self.assertEqual(self.store.add_many([donation]), 1)
        status, _headers, body = self._request("/api/status")
        listed = json.loads(body)
        self.assertLessEqual(len(listed["log"]), 100)
        joined = "\n".join(row["message"] for row in listed["log"])
        self.assertIn("kayeend", joined)
        self.assertIn("₱50.00", joined)
        self.assertNotIn(secret, joined)
        self.assertNotIn("thumb.jpg", joined)
        saved = Path(meta["log_file"]).read_text(encoding="utf-8")
        self.assertIn("₱50.00", saved)
        self.assertNotIn(secret, saved)

        Path(meta["log_file"]).unlink()
        status, _headers, body = self._request("/api/log", method="PUT", body={"verbose": False})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["verbose"])
        restarted = Path(meta["log_file"]).read_text(encoding="utf-8")
        self.assertIn("Verbose logging is off.", restarted)
        self.assertNotIn("₱50.00", restarted)
        self.assertNotIn(secret, restarted)

    def test_follow_live_settings_stay_out_of_the_export(self):
        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="youtube-follow-live"', page)
        self.assertIn(b'id="youtube-channel"', page)
        self.assertIn(b'id="rumble-follow-live"', page)
        self.assertIn(b'id="rumble-channel"', page)
        self.assertIn(b'id="kick-follow-live"', page)
        self.assertIn(b"Follow the live stream", page)
        self.assertIn(b"Show whether this channel is live", page)

        status, _headers, body = self._request(
            "/api/settings",
            method="PUT",
            body={
                "youtube_watch": "abcdefghijk",
                "youtube_follow_live": True,
                "youtube_channel": "https://www.youtube.com/@nasa/videos",
                "rumble_follow_live": True,
                "rumble_channel": "https://rumble.com/c/NewsmaxTV/livestreams",
                "kick_follow_live": True,
            },
        )
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual(saved["youtube_watch"], "abcdefghijk")
        self.assertTrue(saved["youtube_follow_live"])
        self.assertEqual(saved["youtube_channel"], "https://www.youtube.com/@nasa/videos")
        self.assertEqual(saved["rumble_channel"], "https://rumble.com/c/NewsmaxTV/livestreams")
        self.assertTrue(saved["kick_follow_live"])
        status, _headers, exported = self._request("/api/settings/export")
        self.assertEqual(status, 200)
        self.assertNotIn(b"@nasa", exported)
        self.assertNotIn(b"NewsmaxTV", exported)
        self.assertNotIn(b"abcdefghijk", exported)

        status, _headers, body = self._request(
            "/api/settings",
            method="PUT",
            body={"youtube_channel": "https://www.youtube.com/watch?v=abcdefghijk"},
        )
        self.assertEqual(status, 400)
        self.assertIn(b"video", body.lower())
        self.assertEqual(self.store.get_setting("youtube_channel"), "https://www.youtube.com/@nasa/videos")
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")

        status, _headers, body = self._request(
            "/api/settings",
            method="PUT",
            body={"youtube_channel": "", "youtube_follow_live": False},
        )
        self.assertEqual(status, 200)
        cleared = json.loads(body)
        self.assertEqual(cleared["youtube_channel"], "")
        self.assertFalse(cleared["youtube_follow_live"])
        self.assertEqual(self.store.get_setting("youtube_watch"), "abcdefghijk")

    def test_new_ledger_follow_save_stores_channel_not_video(self):
        status, _headers, body = self._request(
            "/api/settings",
            method="PUT",
            body={
                "youtube_watch": "abcdefghijk",
                "youtube_follow_live": False,
                "rumble_watch": "446413572",
                "rumble_follow_live": False,
                "kick_watch": "https://kick.com/xqc",
            },
        )
        self.assertEqual(status, 200)
        first = json.loads(body)
        self.assertEqual(first["youtube_watch"], "abcdefghijk")
        self.assertEqual(first["youtube_channel"], "")
        self.assertFalse(first["youtube_follow_live"])
        self.assertEqual(first["rumble_watch"], "446413572")
        self.assertEqual(first["rumble_channel"], "")
        self.assertFalse(first["rumble_follow_live"])
        self.assertEqual(first["kick_watch"], "https://kick.com/xqc")
        self.assertFalse(first["kick_follow_live"])

        status, _headers, body = self._request("/api/streams", method="POST", body={"action": "add"})
        self.assertEqual(status, 200)
        report = json.loads(body)
        first_id = next(tab["id"] for tab in report["streams"]["tabs"] if tab["id"] != report["streams"]["active_id"])
        opened = json.loads(self._request("/api/settings")[2])
        self.assertEqual(opened["youtube_watch"], "")
        self.assertEqual(opened["youtube_channel"], "")
        self.assertFalse(opened["youtube_follow_live"])
        self.assertEqual(opened["rumble_watch"], "")
        self.assertEqual(opened["kick_watch"], "")
        self.assertFalse(opened["kick_follow_live"])

        status, _headers, body = self._request(
            "/api/settings",
            method="PUT",
            body={
                "youtube_channel": "https://www.youtube.com/@nasa",
                "youtube_follow_live": True,
                "rumble_channel": "https://rumble.com/c/NewsmaxTV",
                "rumble_follow_live": True,
                "kick_watch": "https://kick.com/channel",
            },
        )
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual(saved["youtube_channel"], "https://www.youtube.com/@nasa")
        self.assertTrue(saved["youtube_follow_live"])
        self.assertEqual(saved["youtube_watch"], "")
        self.assertEqual(saved["rumble_channel"], "https://rumble.com/c/NewsmaxTV")
        self.assertTrue(saved["rumble_follow_live"])
        self.assertEqual(saved["rumble_watch"], "")
        self.assertEqual(saved["kick_watch"], "https://kick.com/channel")
        self.assertFalse(saved["kick_follow_live"])

        status, _headers, body = self._request(
            "/api/streams",
            method="POST",
            body={"action": "switch", "id": first_id},
        )
        self.assertEqual(status, 200)
        back = json.loads(self._request("/api/settings")[2])
        self.assertEqual(back["youtube_watch"], "abcdefghijk")
        self.assertEqual(back["youtube_channel"], "")
        self.assertFalse(back["youtube_follow_live"])
        self.assertEqual(back["kick_watch"], "https://kick.com/xqc")
        self.assertFalse(back["kick_follow_live"])

    def test_streamer_presets_are_shared_and_left_out_of_the_export(self):
        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="tab-streamers"', page)
        self.assertIn(b'id="tab-button-streamers"', page)
        self.assertIn(b'data-tab="streamers"', page)
        self.assertIn(b"Streamer presets", page)
        self.assertIn(b'id="ledger-preset"', page)
        self.assertIn(b'id="preset-youtube"', page)
        self.assertIn(b'id="preset-rumble"', page)
        self.assertIn(b'id="preset-kick"', page)
        streamers, settings_page = page.split(b'id="tab-settings"', 1)
        self.assertIn(b'id="tiers-enabled"', streamers)
        self.assertIn(b'id="goal-tiers"', streamers)
        self.assertNotIn(b'id="tiers-enabled"', settings_page)
        self.assertNotIn(b'id="goal-tiers"', settings_page)
        self.assertIn(b"Saving the preset itself does not change the ledger that is open.", page)
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"function applyLedgerPreset(", script)
        self.assertIn(b"function editPreset(", script)
        self.assertIn(b'action: "save"', script)
        self.assertIn(b'action: "delete"', script)
        self.assertIn(b"/api/presets", script)
        self.assertEqual(script.count(b'tiers_enabled: $("tiers-enabled").checked'), 1)
        self.assertEqual(script.count(b"goal_tiers: collectTiers()"), 1)
        self.assertNotIn(b"renderTierFields(settings.goal_tiers)", script)
        self.assertNotIn(b"settings.tiers_enabled", script)
        self.assertNotIn(b"settings.goal_tiers", script)
        self.assertIn(b"renderTierFields([])", script)
        self.assertIn(b"body.tiers_enabled", script)
        self.assertIn(b"body.goal_tiers", script)
        self.assertIn(b"addresses and goals", script)
        self.assertIn(" · goals".encode(), script)

        status, _headers, body = self._request("/api/presets", cookie=False)
        self.assertEqual(status, 401)
        status, _headers, body = self._request("/api/presets")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["presets"], [])

        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "  Zed  ",
            "youtube_channel": "https://www.youtube.com/@presetstreamer",
            "rumble_channel": "https://rumble.com/c/PresetStreamer",
            "kick_watch": "https://kick.com/presetstreamer",
        })
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual(saved["preset"]["name"], "Zed")
        self.assertEqual(saved["presets"][0]["name"], "Zed")
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "ada",
            "kick_watch": "https://kick.com/xqc",
        })
        self.assertEqual(status, 200)
        listed = json.loads(body)["presets"]
        self.assertEqual([item["name"] for item in listed], ["ada", "Zed"])
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "Zed",
            "youtube_channel": "https://www.youtube.com/watch?v=abcdefghijk",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"video", body.lower())
        self.assertEqual(self.store.get_setting("youtube_channel"), "")
        self.assertEqual(self.store.get_setting("youtube_watch"), "")
        kept = self.store.streamer_presets()
        self.assertEqual(kept[1]["youtube_channel"], "https://www.youtube.com/@presetstreamer")
        self.assertIs(kept[1]["tiers_enabled"], False)

        self.store.set_setting("tiers_enabled", "1")
        self.store.set_setting("goal_tiers", '[{"enabled":true,"amount":10,"name":"Keep","bonus":""}]')
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "Zed",
            "youtube_channel": "https://www.youtube.com/@presetstreamer",
            "tiers_enabled": True,
            "goal_tiers": [{"enabled": True, "amount": 25, "name": "NightGoal", "bonus": "map"}],
        })
        self.assertEqual(status, 200)
        replaced = json.loads(body)["preset"]
        self.assertEqual(replaced["id"], kept[1]["id"])
        self.assertIs(replaced["tiers_enabled"], True)
        self.assertEqual(replaced["goal_tiers"][0]["name"], "NightGoal")
        self.assertEqual(replaced["goal_tiers"][0]["amount"], 25.0)
        self.assertEqual(self.store.get_setting("tiers_enabled"), "1")
        self.assertIn("Keep", self.store.get_setting("goal_tiers"))
        self.assertNotIn("NightGoal", self.store.get_setting("goal_tiers"))
        self.assertNotIn("NightGoal", self.store.get_setting("youtube_channel"))
        status, _headers, exported = self._request("/api/settings/export")
        self.assertEqual(status, 200)
        self.assertNotIn(b"NightGoal", exported)
        self.assertNotIn(b"Keep", exported)
        self.assertNotIn(b"presetstreamer", exported)

        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "Zed",
            "youtube_channel": "https://www.youtube.com/@presetstreamer",
        })
        self.assertEqual(status, 200)
        cleared = json.loads(body)["preset"]
        self.assertEqual(cleared["id"], kept[1]["id"])
        self.assertIs(cleared["tiers_enabled"], False)
        self.assertTrue(all(not tier["name"] for tier in cleared["goal_tiers"]))
        self.assertIn("Keep", self.store.get_setting("goal_tiers"))

        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "Goals only",
            "tiers_enabled": True,
            "goal_tiers": [{"enabled": True, "amount": 25, "name": "NightGoal", "bonus": ""}],
        })
        self.assertEqual(status, 400)
        self.assertIn(b"at least one", body)
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "Zed",
            "youtube_channel": "https://www.youtube.com/@presetstreamer",
            "goal_tiers": "NightGoal",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"list", body)
        self.assertEqual(self.store.streamer_presets()[1]["youtube_channel"], "https://www.youtube.com/@presetstreamer")

        status, _headers, exported = self._request("/api/settings/export")
        self.assertEqual(status, 200)
        self.assertNotIn(b"presetstreamer", exported)
        self.assertNotIn(b"streamer_presets", exported)
        status, _headers, settings = self._request("/api/settings")
        self.assertNotIn(b"presetstreamer", settings)

        status, _headers, body = self._request("/api/streams", method="POST", body={"action": "add"})
        self.assertEqual(status, 200)
        opened = json.loads(self._request("/api/settings")[2])
        self.assertEqual(opened["youtube_channel"], "")
        self.assertFalse(opened["youtube_follow_live"])
        self.assertEqual(opened["kick_watch"], "")
        self.assertIs(opened["tiers_enabled"], False)
        self.assertFalse(any(tier["name"] for tier in opened["goal_tiers"]))
        first = next(
            tab for tab in json.loads(self.store.get_setting("streams"))["tabs"]
            if tab["id"] != json.loads(body)["streams"]["active_id"]
        )
        self.assertEqual(first["tiers_enabled"], "1")
        self.assertIn("Keep", first["goal_tiers"])
        self.assertEqual(len(self.store.streamer_presets()), 2)

        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "delete",
            "id": kept[0]["id"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["presets"]), 1)
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "delete",
            "id": "missing",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"not saved", body)
        status, _headers, body = self._request("/api/presets", method="POST", body={"action": "nope"})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "Streamer preset action must be save, delete, or apply.")
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "Nobody",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"at least one", body)

        status, _headers, body = self._request("/api/status")
        joined = "\n".join(row["message"] for row in json.loads(body)["log"])
        self.assertIn("Saved streamer preset Zed.", joined)
        self.assertIn("Saved streamer preset ada.", joined)
        self.assertIn("Removed streamer preset ada.", joined)
        self.assertNotIn("presetstreamer", joined)
        self.assertNotIn("@presetstreamer", joined)
        self.assertNotIn("NightGoal", joined)
        self.assertNotIn("Keep", joined)

    def test_apply_to_open_ledger_writes_the_preset_like_the_address_dialog(self):
        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        streamers = page.split(b'id="tab-streamers"', 1)[1].split(b"</dialog>", 1)[0]
        self.assertIn(b'id="preset-apply"', streamers)
        self.assertIn(b"Apply to open ledger", streamers)
        self.assertLess(streamers.index(b"Save preset"), streamers.index(b'id="preset-apply"'))
        self.assertIn(b"Saving the preset itself does not change the ledger that is open.", streamers)
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"function applyPresetToOpenLedger(", script)
        self.assertIn(b'action: "apply"', script)
        self.assertIn(b"function syncPresetApply(", script)
        apply_fn = script.split(b"function applyPresetToOpenLedger(", 1)[1].split(b"\nfunction ", 1)[0]
        # Unsaved edits are saved first, then the saved preset is applied.
        self.assertLess(apply_fn.index(b"presetFormBody()"), apply_fn.index(b'action: "apply"'))
        self.assertIn(b"applySettingsToForm(payload.settings)", apply_fn)
        self.assertIn(b"refresh()", apply_fn)
        save_fn = script.split(b"function savePreset(", 1)[1].split(b"\nfunction ", 1)[0]
        self.assertNotIn(b'"apply"', save_fn)
        self.assertNotIn(b"/api/settings", save_fn)
        render_fn = script.split(b"function renderStream(", 1)[1].split(b"\nfunction ", 1)[0]
        self.assertIn(b"syncPresetApply()", render_fn)

        status, _headers, body = self._request("/api/blue", method="POST", body={
            "action": "save",
            "name": "Thanks",
            "source": 'ON DONATION THEN SEND CHAT TO YOUTUBE "Hi {DONOR}."',
        })
        self.assertEqual(status, 200)
        script_id = json.loads(body)["script"]["id"]
        goals = [{"enabled": True, "amount": 25, "name": "NightGoal", "bonus": "map"}]
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "Zed",
            "youtube_channel": "https://www.youtube.com/@presetstreamer",
            "kick_watch": "https://kick.com/presetstreamer",
            "tiers_enabled": True,
            "goal_tiers": goals,
            "script_id": script_id,
        })
        self.assertEqual(status, 200)
        preset = json.loads(body)["preset"]

        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("rumble_watch", "https://rumble.com/v4abcde-some-show.html")
        self.store.set_setting("tiers_enabled", "0")
        self.assertEqual(self.store.get_setting("blue_script_id"), "")
        noted = []
        original_note = self.app.service.blue.note_script
        self.app.service.blue.note_script = lambda stream_id, previous: noted.append((stream_id, previous))
        try:
            status, _headers, body = self._request("/api/presets", method="POST", body={
                "action": "apply",
                "id": preset["id"],
            })
        finally:
            self.app.service.blue.note_script = original_note
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["preset"]["id"], preset["id"])
        self.assertEqual(payload["settings"]["youtube_channel"], "https://www.youtube.com/@presetstreamer")
        self.assertTrue(payload["settings"]["youtube_follow_live"])
        self.assertEqual(self.store.get_setting("youtube_follow_live"), "1")
        self.assertEqual(self.store.get_setting("rumble_follow_live"), "0")
        self.assertEqual(self.store.get_setting("rumble_watch"), "")
        self.assertEqual(self.store.get_setting("kick_watch"), "https://kick.com/presetstreamer")
        self.assertEqual(self.store.get_setting("tiers_enabled"), "1")
        self.assertIn("NightGoal", self.store.get_setting("goal_tiers"))
        self.assertEqual(self.store.get_setting("blue_script_id"), script_id)
        self.assertEqual(noted, [(self.store.stream_tabs()["active_id"], "")])
        self.assertIn("NightGoal", self.store.get_setting("streams"))
        self.assertIn("Applied streamer preset Zed to the open ledger.", json.dumps(self.store.recent_log()))
        applied = {key: self.store.get_setting(key) for key in (
            "youtube_channel", "youtube_follow_live", "youtube_watch",
            "rumble_channel", "rumble_follow_live", "rumble_watch",
            "kick_watch", "tiers_enabled", "goal_tiers", "blue_script_id",
        )}
        first = self.store.stream_tabs()["active_id"]

        # The address dialog, saved with this preset chosen, writes the same values.
        status, _headers, body = self._request("/api/streams", method="POST", body={"action": "add"})
        self.assertEqual(status, 200)
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("rumble_watch", "https://rumble.com/v4abcde-some-show.html")
        status, _headers, body = self._request("/api/settings", method="PUT", body={
            "kick_watch": "https://kick.com/presetstreamer",
            "youtube_channel": "https://www.youtube.com/@presetstreamer",
            "youtube_follow_live": True,
            "rumble_watch": "",
            "rumble_follow_live": False,
            "tiers_enabled": True,
            "goal_tiers": goals,
            "blue_script_id": script_id,
        })
        self.assertEqual(status, 200)
        dialog = {key: self.store.get_setting(key) for key in applied}
        self.assertEqual(dialog, applied)

        # Plain Save of an edited preset still leaves the open ledger alone.
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "Zed",
            "youtube_channel": "https://www.youtube.com/@otherstreamer",
        })
        self.assertEqual(status, 200)
        self.assertEqual(self.store.get_setting("youtube_channel"), "https://www.youtube.com/@presetstreamer")
        self.assertIn("NightGoal", self.store.get_setting("goal_tiers"))
        self.assertEqual(self.store.get_setting("blue_script_id"), script_id)

        # Apply writes only the open ledger. The first ledger keeps what it had.
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "apply",
            "id": preset["id"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(self.store.get_setting("youtube_channel"), "https://www.youtube.com/@otherstreamer")
        self.assertEqual(self.store.get_setting("tiers_enabled"), "0")
        self.assertEqual(self.store.get_setting("blue_script_id"), "")
        self.assertEqual(self.store.get_setting("kick_watch"), "")
        self._request("/api/streams", method="POST", body={"action": "switch", "id": first})
        self.assertEqual(self.store.get_setting("youtube_channel"), "https://www.youtube.com/@presetstreamer")
        self.assertEqual(self.store.get_setting("blue_script_id"), script_id)

        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "apply",
            "id": "missing",
        })
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "That streamer preset is not saved.")
        self.assertEqual(self.store.get_setting("youtube_channel"), "https://www.youtube.com/@presetstreamer")

    def test_blue_scripts_are_saved_and_left_out_of_the_export(self):
        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertLess(page.index(b'id="tab-button-streamers"'), page.index(b'id="tab-button-blue"'))
        self.assertLess(page.index(b'id="tab-button-blue"'), page.index(b'id="tab-button-settings"'))
        self.assertIn(b'id="tab-blue"', page)
        self.assertIn(b'id="blue-source"', page)
        self.assertIn(b'id="blue-import"', page)
        self.assertIn(b'id="blue-export"', page)
        self.assertIn(b'id="preset-script"', page)
        self.assertIn(b"Berry Lexical User Events", page)
        self.assertIn(b"Saving the preset itself does not change the ledger that is open.", page)
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"BLUE_CHAT_GAP_MIN_MS = 2000", script)
        self.assertIn(b"BLUE_CHAT_GAP_MAX_MS = 6000", script)
        self.assertIn(b"function drainBlue(", script)
        self.assertIn(b"new FileReader(", script)
        refresh = script.split(b"async function refresh(", 1)[1].split(b"\nasync function ", 1)[0]
        self.assertIn(b"drainBlue()", refresh)
        self.assertNotIn(b"/api/chat", refresh)
        self.assertNotIn(b"/api/blue", refresh)

        status, _headers, body = self._request("/api/blue", cookie=False)
        self.assertEqual(status, 401)
        status, _headers, body = self._request("/api/blue")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["scripts"], [])
        source = 'NOT A LINE\nON DONATION THEN SEND CHAT TO YOUTUBE "Hi {DONOR}."\n'
        status, _headers, body = self._request("/api/blue", method="POST", body={
            "action": "save",
            "name": "  Thanks  ",
            "source": source,
        })
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual(saved["script"]["name"], "Thanks")
        self.assertEqual(saved["problems"], [{"line": 1, "message": "A BLUE line starts with ON."}])
        script_id = saved["script"]["id"]
        status, _headers, body = self._request("/api/blue", method="POST", body={
            "action": "save",
            "name": "Empty",
            "source": "   ",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"Write a BLUE script first.", body)
        status, _headers, exported = self._request("/api/settings/export")
        self.assertNotIn(b"blue_scripts", exported)
        self.assertNotIn(b"Hi {DONOR}", exported)
        self.assertNotIn(b"NOT A LINE", exported)

        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "With script",
            "kick_watch": "https://kick.com/presetstreamer",
            "script_id": "missing",
        })
        self.assertEqual(status, 400)
        self.assertIn(b"That BLUE script is not saved.", body)
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "With script",
            "kick_watch": "https://kick.com/presetstreamer",
            "script_id": script_id,
        })
        self.assertEqual(status, 200)
        preset = json.loads(body)["preset"]
        self.assertEqual(preset["script_id"], script_id)
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "With script",
            "kick_watch": "https://kick.com/presetstreamer",
        })
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["preset"]["script_id"], "")
        self.assertEqual(json.loads(body)["preset"]["id"], preset["id"])
        status, _headers, body = self._request("/api/presets", method="POST", body={
            "action": "save",
            "name": "With script",
            "kick_watch": "https://kick.com/presetstreamer",
            "script_id": script_id,
        })
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["preset"]["script_id"], script_id)

        status, _headers, body = self._request("/api/settings", method="PUT", body={
            "blue_script_id": script_id,
        })
        self.assertEqual(status, 200)
        self.assertEqual(self.store.get_setting("blue_script_id"), script_id)
        self.assertNotIn(b"blue_script_id", self._request("/api/settings/export")[2])
        status, _headers, body = self._request("/api/settings", method="PUT", body={
            "blue_script_id": "missing",
        })
        self.assertEqual(status, 400)
        self.assertEqual(self.store.get_setting("blue_script_id"), script_id)

        status, _headers, body = self._request("/api/blue/pending")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["pending"], [])
        status, _headers, body = self._request("/api/blue/result", method="POST", body={
            "ok": False,
            "script": "Thanks",
            "line": 2,
            "target": "rumble",
            "reason": "Continue on the Rumble page before chat can be posted there.",
            "text": "secret message body",
        })
        self.assertEqual(status, 200)
        status, _headers, body = self._request("/api/blue/result", method="POST", body={
            "ok": True,
            "script": "Thanks",
            "text": "secret message body",
        })
        self.assertEqual(status, 200)
        joined = "\n".join(row["message"] for row in self.store.recent_log())
        self.assertIn("BLUE Thanks line 2: Rumble chat was not sent.", joined)
        self.assertIn("Continue on the Rumble page before chat can be posted there.", joined)
        self.assertNotIn("secret message body", joined)

        status, _headers, body = self._request("/api/blue", method="POST", body={
            "action": "delete",
            "id": script_id,
        })
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["scripts"], [])
        self.assertEqual(self.store.get_setting("blue_script_id"), "")
        self.assertEqual(self.store.streamer_presets()[0]["script_id"], "")
        removed = "\n".join(row["message"] for row in self.store.recent_log())
        self.assertIn("Removed BLUE script Thanks.", removed)
        self.assertNotIn("NOT A LINE", removed)

    def test_embed_uses_the_open_ledger_without_a_live_lookup(self):
        from streamdonations.util import ApiError

        def fail_video(_target):
            raise AssertionError("video page lookup")

        def fail_live(_channel):
            raise AssertionError("live page lookup")

        self.app.service.youtube.public_video_id = fail_video
        self.app.service.youtube.public_live_video_id = fail_live
        self.app.service.rumble.resolve_live_channel = fail_live
        self.store.set_setting("collection_paused", "1")
        self.assertTrue(self.store.collection_paused())
        self.store.set_setting("youtube_follow_live", "0")
        self.store.set_setting("youtube_watch", "abcdefghijk")
        self.store.set_setting("rumble_follow_live", "0")
        self.store.set_setting("rumble_watch", "9604851")
        self.store.set_setting("kick_watch", "https://kick.com/smashjt")

        status, _headers, body = self._request("/api/embed?platform=youtube")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(set(payload), {"platform", "url", "message"})
        self.assertEqual(payload["platform"], "youtube")
        self.assertEqual(payload["url"], "https://www.youtube.com/embed/abcdefghijk")
        self.assertEqual(payload["message"], "")

        status, _headers, body = self._request("/api/embed?platform=rumble")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["url"], "https://rumble.com/embed/v5pv5f/")

        status, _headers, body = self._request("/api/embed?platform=kick")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["url"], "https://player.kick.com/smashjt")
        self.assertNotIn(b"autoplay=1", body)

        logged = "\n".join(row["message"] for row in self.store.recent_log())
        self.assertNotIn("abcdefghijk", logged)
        self.assertNotIn("smashjt", logged)
        self.assertNotIn("v5pv5f", logged)

        self.store.set_setting("kick_watch", "")
        status, _headers, body = self._request("/api/embed?platform=kick")
        self.assertEqual(status, 200)
        missing = json.loads(body)
        self.assertEqual(missing["url"], "")
        self.assertIn("Kick", missing["message"])
        self.assertNotIn("http", missing["message"])

        status, _headers, body = self._request("/api/embed?platform=twitch")
        self.assertEqual(status, 400)
        status, _headers, _body = self._request("/api/embed?platform=kick", cookie=False)
        self.assertEqual(status, 401)

        self.store.set_setting("youtube_follow_live", "1")
        self.store.set_setting("youtube_channel", "https://www.youtube.com/@SmashJT/")

        def down(_channel):
            raise ApiError("HTTP 503: https://www.youtube.com/@SmashJT/", status=503)

        self.app.service.youtube.public_live_video_id = down
        status, _headers, body = self._request("/api/embed?platform=youtube")
        self.assertEqual(status, 502)
        self.assertNotIn(b"SmashJT", body)
        self.assertIn(b"That address could not be embedded.", body)

    def test_embed_panel_stays_beside_the_ledger(self):
        status, headers, page = self._request("/")
        self.assertEqual(status, 200)
        policy = headers.get("Content-Security-Policy", "")
        self.assertIn(
            "frame-src https://www.youtube.com https://www.youtube-nocookie.com "
            "https://rumble.com https://player.kick.com",
            policy,
        )
        self.assertNotIn("*", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("default-src 'self'", policy)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertLess(page.index(b'id="watch-panel"'), page.index(b'class="mast"'))
        self.assertLess(page.index(b'class="mast"'), page.index(b'id="tab-ledger"'))
        self.assertLess(page.index(b'id="tab-ledger"'), page.index(b'id="stream-banner"'))
        self.assertLess(page.index(b'id="tab-ledger"'), page.index(b'id="tab-youtube"'))
        self.assertIn(b'class="watch-panel is-collapsed"', page)
        self.assertIn(b'<select id="watch-platforms"', page)
        self.assertLess(page.index(b'id="watch-platforms"'), page.index(b'id="watch-reload"'))
        self.assertIn(b'data-platform="youtube"', page)
        self.assertIn(b'data-platform="rumble"', page)
        self.assertIn(b'data-platform="kick"', page)
        self.assertIn(b'referrerpolicy="strict-origin-when-cross-origin"', page)
        self.assertIn(
            b'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"',
            page,
        )
        self.assertNotIn(b"autoplay=1", page)
        self.assertIn(b'id="watch-play"', page)
        self.assertIn(b'id="watch-live"', page)
        self.assertIn(b'id="watch-mute"', page)
        self.assertIn(b'title="Playback"', page)
        self.assertIn(b'title="Live"', page)
        self.assertIn(b'title="Mute"', page)
        self.assertLess(page.index(b'class="watch-stage"'), page.index(b'id="watch-play"'))
        self.assertLess(page.index(b'id="watch-play"'), page.index(b'id="watch-live"'))
        self.assertLess(page.index(b'id="watch-live"'), page.index(b'id="watch-mute"'))
        self.assertLess(page.index(b'id="watch-mute"'), page.index(b'id="watch-chat-lines"'))
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"function watchPlayerUrl(", script)
        self.assertIn(b"function watchPlaybackUrl(", script)
        self.assertIn(b"function showWatchFrame(", script)
        self.assertIn(b'url.searchParams.set("autoplay", "1")', script)
        self.assertIn(b'url.searchParams.set("autoplay", autoplay ? "true" : "false")', script)
        self.assertIn(b'url.searchParams.set("muted", muted ? "true" : "false")', script)
        self.assertIn(b"stream-donations-watch-muted", script)
        self.assertIn(b"if (url.search || url.hash) return \"\";", script)
        self.assertIn(b"function watchAddressSignature(", script)
        self.assertIn(b"stream-donations-watch-open", script)
        self.assertIn(b"stream-donations-watch-platform", script)
        self.assertIn(b"/api/embed?platform=", script)
        self.assertIn(b"www.youtube-nocookie.com", script)
        self.assertIn(b"player.kick.com", script)
        electron = Path(__file__).resolve().parents[2] / "electron"
        main_js = (electron / "main.js").read_text(encoding="utf-8")
        preload_js = (electron / "preload.js").read_text(encoding="utf-8")
        self.assertIn('ipcMain.handle("stream-donations-player"', main_js)
        self.assertIn("no-user-gesture-required", main_js)
        self.assertIn("player.kick.com", main_js)
        self.assertIn('contextBridge.exposeInMainWorld("streamDonationsPlayer"', preload_js)
        self.assertIn('"stream-donations-player"', preload_js)
        refresh = script.split(b"async function refresh(", 1)[1].split(b"\nasync function ", 1)[0]
        self.assertNotIn(b"/api/embed", refresh)
        self.assertNotIn(b"loadWatch", refresh)
        apply = script.split(b"function applySettingsToForm(", 1)[1].split(b"\nfunction ", 1)[0]
        self.assertIn(b"watchAddressSignature", apply)
        self.assertIn(b"loadWatch", apply)
        boot = script.split(b"async function boot(", 1)[1]
        self.assertLess(boot.index(b"applySettingsToForm"), boot.index(b"bind("))
        self.assertIn(b"bindWatch()", script)
        self.assertIn(b'id="watch-chat-lines"', page)
        self.assertIn(b'id="watch-chat-times"', page)
        self.assertIn(b'title="Timestamps"', page)
        self.assertIn(b'aria-label="Timestamps"', page)
        self.assertIn(b'id="watch-giveaway"', page)
        self.assertIn(b'title="Giveaway"', page)
        self.assertLess(page.index(b'id="watch-chat-times"'), page.index(b'id="watch-giveaway"'))
        self.assertLess(page.index(b'id="watch-giveaway"'), page.index(b'id="watch-chat-lines"'))
        self.assertNotIn(b">Time</button>", page)
        self.assertNotIn(b'id="tab-button-giveaway"', page)
        self.assertIn(b'aria-label="Chat"', page)
        self.assertLess(page.index(b'class="watch-stage"'), page.index(b'id="watch-note"'))
        self.assertLess(page.index(b'class="watch-stage"'), page.index(b'id="watch-chat-lines"'))
        self.assertNotIn(b'id="watch-chat-input"', page)
        self.assertIn(b"/api/chat?after=", script)
        self.assertIn(b"/icons/youtube.svg", script)
        self.assertIn(b"/icons/rumble.svg", script)
        self.assertIn(b"/icons/kick.svg", script)
        self.assertIn(b"stream-donations-watch-times", script)
        self.assertIn(b"function setWatchTimes(", script)
        times = script.split(b"function setWatchTimes(", 1)[1].split(b"\nfunction ", 1)[0]
        self.assertNotIn(b"Hide timestamps", times)
        self.assertNotIn(b"Show timestamps", times)
        drawn = script.split(b"function appendWatchChatLine(", 1)[1].split(b"\nfunction renderWatchChat(", 1)[0]
        painted = script.split(b"function renderWatchChat(", 1)[1].split(b"\nfunction loadWatchChat(", 1)[0]
        self.assertIn(b"textContent", drawn)
        self.assertNotIn(b"innerHTML", drawn)
        self.assertNotIn(b"innerHTML", painted)
        self.assertNotIn(b"/api/chat", refresh)
        self.assertNotIn(b"loadWatchChat", refresh)
        self.assertNotIn(b"stream-donations-watch-times", refresh)
        self.assertNotIn(b"setWatchTimes", refresh)
        status, _headers, styles = self._request("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(b".watch-panel.is-collapsed", styles)
        bar = styles.split(b".status-bar {", 1)[1].split(b"}", 1)[0]
        self.assertIn(b"width: 100%", bar)
        self.assertIn(b".status-ephemeral:empty", styles)
        self.assertIn(b"aspect-ratio: 16 / 9", styles)
        self.assertIn(b".watch-chat", styles)
        self.assertIn(b".watch-chat-tools", styles)
        self.assertIn(b".watch-chat-time", styles)
        self.assertIn(b".watch-chat-lines:not(.is-times)", styles)
        self.assertNotIn(b".watch-stage:has", styles)
        narrow = styles.split(b"@media (max-width: 980px)", 1)[1]
        self.assertIn(b".watch-panel:not(.is-collapsed)", narrow)
        self.assertIn(b"position: fixed", narrow)

    def test_chat_lines_round_trip_without_being_logged(self):
        self.store.set_setting("collection_paused", "1")
        self.app.service.chat.add("youtube", [{
            "key": "m1",
            "author": "Viewer",
            "text": "hello there",
            "time": "2026-09-25T11:04:30+00:00",
        }])
        self.app.service.chat.add("kick", [{"key": "m2", "author": "", "text": "yo"}])
        status, _headers, body = self._request("/api/chat", cookie=False)
        self.assertEqual(status, 401)
        status, _headers, body = self._request("/api/chat?after=0&generation=0")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(set(payload), {"lines", "cursor", "generation", "paused"})
        self.assertTrue(payload["paused"])
        self.assertEqual(
            [(line["platform"], line["author"], line["text"], line["time"]) for line in payload["lines"]],
            [
                ("youtube", "Viewer", "hello there", "2026-09-25T11:04:30Z"),
                ("kick", "Someone", "yo", payload["lines"][1]["time"]),
            ],
        )
        self.assertRegex(payload["lines"][1]["time"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(set(payload["lines"][0]), {"id", "platform", "author", "text", "time"})
        logged = "\n".join(row["message"] for row in self.store.recent_log())
        self.assertNotIn("hello there", logged)
        self.assertNotIn("yo", logged)
        status, _headers, body = self._request(
            f"/api/chat?after={payload['cursor']}&generation={payload['generation']}"
        )
        self.assertEqual(json.loads(body)["lines"], [])
        self.store.set_setting("collection_paused", "0")
        status, _headers, body = self._request("/api/chat?after=0&generation=99")
        resumed = json.loads(body)
        self.assertFalse(resumed["paused"])
        self.assertEqual(len(resumed["lines"]), 2)
        status, _headers, body = self._request("/api/chat?after=nope")
        self.assertEqual(status, 400)
        self.assertIn(b"after must be a whole number.", body)
        status, _headers, body = self._request("/api/chat?generation=-1")
        self.assertEqual(status, 400)
        self.assertIn(b"generation must be a whole number.", body)
        for name in ("youtube", "rumble", "kick"):
            status, headers, icon = self._request(f"/icons/{name}.svg")
            self.assertEqual(status, 200)
            self.assertIn("image/svg+xml", headers.get("Content-Type", ""))
            self.assertIn(b"<svg", icon)
        status, headers, icon = self._request("/icons/superberry.png")
        self.assertEqual(status, 200)
        self.assertIn("image/png", headers.get("Content-Type", ""))
        self.assertTrue(icon.startswith(b"\x89PNG"))
        status, headers, font = self._request("/fonts/fredoka.woff2")
        self.assertEqual(status, 200)
        self.assertIn("font/woff2", headers.get("Content-Type", ""))
        self.assertTrue(font.startswith(b"wOF2"))
        status, _headers, _body = self._request("/icons/youtube.svg", cookie=False)
        self.assertEqual(status, 401)
        status, _headers, _body = self._request("/icons/superberry.png", cookie=False)
        self.assertEqual(status, 401)
        status, _headers, _body = self._request("/fonts/fredoka.woff2", cookie=False)
        self.assertEqual(status, 401)
        status, _headers, _body = self._request("/icons/other.svg")
        self.assertEqual(status, 404)

    def test_giveaway_counts_each_platform_once_and_draws_without_logging(self):
        status, _headers, _body = self._request("/api/giveaway", cookie=False)
        self.assertEqual(status, 401)
        status, _headers, _body = self._request(
            "/api/giveaway",
            method="POST",
            body={"action": "redraw"},
            cookie=False,
        )
        self.assertEqual(status, 401)
        status, _headers, _body = self._request(
            "/api/giveaway",
            method="POST",
            body={"action": "start", "keyword": " #GiveawayZz "},
            cookie=False,
        )
        self.assertEqual(status, 401)

        self.app.service.chat.add("youtube", [{
            "key": "before",
            "author": "EarlyZz",
            "text": "#giveawayzz",
        }])
        status, _headers, body = self._request(
            "/api/giveaway",
            method="POST",
            body={"action": "start", "keyword": " #GiveawayZz ", "seconds": 0},
        )
        self.assertEqual(status, 200)
        started = json.loads(body)
        self.assertTrue(started["running"])
        self.assertEqual(started["keyword"], "#GiveawayZz")
        self.assertEqual(started["entries"], 0)
        self.assertEqual(
            started["announce"],
            "Type #GiveawayZz in chat to enter the giveaway.",
        )
        self.assertEqual(started["winner_announce"], "")

        status, _headers, body = self._request(
            "/api/giveaway",
            method="POST",
            body={"action": "start", "keyword": "other"},
        )
        self.assertEqual(status, 400)
        self.assertIn(b"A giveaway is already running.", body)
        status, _headers, body = self._request(
            "/api/giveaway",
            method="POST",
            body={"action": "nope"},
        )
        self.assertEqual(status, 400)
        self.assertIn(b"Giveaway action must be start, stop, or redraw.", body)
        status, _headers, body = self._request(
            "/api/giveaway",
            method="POST",
            body={"action": "redraw"},
        )
        self.assertEqual(status, 400)
        self.assertIn(b"Redraw a winner after the drawing is closed.", body)
        status, _headers, body = self._request(
            "/api/giveaway",
            method="POST",
            body={"action": "start", "keyword": "", "seconds": 1.5},
        )
        self.assertEqual(status, 400)

        self.app.service.chat.add("youtube", [{"key": "y1", "author": "EntrantZz", "text": "#giveawayzz"}])
        self.app.service.chat.add("youtube", [{"key": "y2", "author": "entrantzz", "text": "#GIVEAWAYZZ"}])
        self.app.service.chat.add("youtube", [{"key": "y3", "author": "EntrantZz", "text": "hello #giveawayzz"}])
        self.app.service.chat.add("rumble", [{"key": "r1", "author": "EntrantZz", "text": "#giveawayzz"}])
        self.app.service.chat.add("kick", [{"key": "k1", "author": "OtherZz", "text": "nope"}])

        status, _headers, body = self._request("/api/giveaway")
        self.assertEqual(status, 200)
        counted = json.loads(body)
        self.assertEqual(counted["entries"], 2)
        self.assertEqual(counted["platforms"], {"youtube": 1, "rumble": 1, "kick": 0})

        self.app.service.giveaway.choose = lambda rows: rows[0]
        status, _headers, body = self._request(
            "/api/giveaway",
            method="POST",
            body={"action": "stop"},
        )
        self.assertEqual(status, 200)
        drawn = json.loads(body)
        self.assertFalse(drawn["running"])
        self.assertTrue(drawn["closed"])
        self.assertEqual(drawn["winner"], {"platform": "youtube", "author": "EntrantZz"})
        self.assertEqual(drawn["entries"], 1)
        self.assertEqual(drawn["platforms"], {"youtube": 0, "rumble": 1, "kick": 0})
        self.assertEqual(drawn["winners"], [{"platform": "youtube", "author": "EntrantZz"}])
        self.assertEqual(
            drawn["winner_announce"],
            "The giveaway winner is EntrantZz on YouTube.",
        )
        self.app.service.giveaway.choose = lambda rows: rows[0]
        status, _headers, body = self._request(
            "/api/giveaway",
            method="POST",
            body={"action": "redraw"},
        )
        self.assertEqual(status, 200)
        redrawn = json.loads(body)
        self.assertEqual(redrawn["entries"], 0)
        self.assertEqual(redrawn["platforms"], {"youtube": 0, "rumble": 0, "kick": 0})
        self.assertTrue(redrawn["closed"])
        self.assertFalse(redrawn["running"])
        self.assertEqual(redrawn["winner"], {"platform": "rumble", "author": "EntrantZz"})
        self.assertEqual(redrawn["winners"], [
            {"platform": "youtube", "author": "EntrantZz"},
            {"platform": "rumble", "author": "EntrantZz"},
        ])
        self.assertEqual(
            redrawn["winner_announce"],
            "The giveaway winner is EntrantZz on Rumble.",
        )
        logged = "\n".join(row["message"] for row in self.store.recent_log())
        self.assertNotIn("GiveawayZz", logged)
        self.assertNotIn("EntrantZz", logged)
        self.assertNotIn("EarlyZz", logged)

        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertNotIn(b'id="tab-button-giveaway"', page)
        self.assertIn(b'id="watch-giveaway"', page)
        self.assertIn(b'aria-label="Giveaway"', page)
        self.assertIn(b'id="tab-giveaway"', page)
        self.assertIn(b'id="giveaway-toggle"', page)
        self.assertIn(b'id="giveaway-redraw"', page)
        self.assertIn(b'id="giveaway-winners"', page)
        self.assertIn(b'id="giveaway-copy-keyword"', page)
        self.assertNotIn(b'id="giveaway-copy-winner"', page)
        self.assertLess(page.index(b'id="watch-chat-times"'), page.index(b'id="watch-giveaway"'))
        self.assertLess(page.index(b'id="tab-button-streamers"'), page.index(b'id="tab-button-settings"'))
        self.assertLess(page.index(b'id="tab-giveaway"'), page.index(b'id="tab-settings"'))
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"/api/giveaway", script)
        self.assertIn(b"bindGiveaway()", script)
        self.assertIn(b'action: "redraw"', script)
        self.assertIn(b"function ephemeralStatusItems(", script)
        self.assertIn(b"function giveawayTimerLive(", script)
        poll = script.split(b"function startGiveawayPoll(", 1)[1].split(b"\nfunction ", 1)[0]
        self.assertIn(b"giveawayTimerLive()", poll)
        refresh_body = script.split(b"async function refresh(", 1)[1].split(b"\nasync function ", 1)[0]
        self.assertNotIn(b"ephemeralStatusItems", refresh_body)
        self.assertIn(b"Keyword announcement copied.", script)
        self.assertIn(b"Winner copied.", script)
        refresh = script.split(b"async function refresh(", 1)[1].split(b"\nasync function ", 1)[0]
        self.assertNotIn(b"/api/giveaway", refresh)
        status, _headers, styles = self._request("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(b".giveaway-counts", styles)
        self.assertIn(b".giveaway-winner", styles)
        self.assertIn(b"--ledger-youtube: #c4544a", styles)
        self.assertIn(b"--ledger-rumble: #7aa3c4", styles)
        self.assertIn(b"--ledger-kick: #53c47a", styles)
        self.assertIn(b'.platform-name[data-platform="youtube"]', styles)
        self.assertIn(b"function platformLabel(", script)
        self.assertIn(b".giveaway-winners", styles)
        rendered = script.split(b"function renderGiveaway(", 1)[1].split(b"\nfunction ", 1)[0]
        self.assertIn(b"giveaway-winners", rendered)
        self.assertIn(b"giveawayWinnerText", rendered)
        self.assertIn(b"Copy winner", rendered)
        self.assertNotIn(b"innerHTML", rendered)
        status, _headers, exported = self._request("/api/settings/export")
        self.assertEqual(status, 200)
        self.assertNotIn(b"giveaway", exported.lower())

    def test_archive_opens_jsonl_without_writing_the_ledger(self):
        from streamdonations.normalize import normalize_super_chat_event
        from streamdonations.settings_file import export_settings

        donation = normalize_super_chat_event(
            {
                "id": "archive-me",
                "snippet": {
                    "channelId": "UCb",
                    "supporterDetails": {"displayName": "Fan"},
                    "commentText": "thanks",
                    "createdAt": "2024-06-01T12:00:00Z",
                    "amountMicros": "5000000",
                    "currency": "USD",
                },
            }
        )
        self.store.add_many([donation])
        _stored, before = self.store.list_donations()
        exported = json.loads(self.store.export_jsonl().strip())
        exported["author_name"] = "@Ada"
        exported["message"] = "from-the-archive-only"
        exported["access_token"] = "ledger-archive-token"
        exported["raw"] = {
            "access_token": "ledger-archive-token",
            "snippet": {"amountMicros": "5000000", "currency": "USD"},
        }
        status, _headers, body = self._request(
            "/api/ledger/archive",
            method="POST",
            raw=(json.dumps(exported) + "\n").encode(),
        )
        self.assertEqual(status, 200)
        view = json.loads(body)
        stored, after = self.store.list_donations()
        self.assertEqual(before, after)
        self.assertEqual(after, 1)
        self.assertEqual(stored[0]["author_name"], "Fan")
        self.assertEqual(stored[0]["message"], "thanks")
        self.assertEqual(view["count"], 1)
        self.assertEqual(view["donations"][0]["author_name"], "Ada")
        self.assertEqual(view["donations"][0]["message"], "from-the-archive-only")
        self.assertEqual(view["donations"][0]["amount_value"], 5)
        self.assertEqual(view["total_usd"], 5)
        self.assertNotIn(b"ledger-archive-token", body)
        self.assertNotIn("raw", view["donations"][0])
        self.assertNotIn("access_token", view["donations"][0])
        self.assertNotIn("dedupe_key", view["donations"][0])

        status, _headers, body = self._request(
            "/api/ledger/archive",
            method="POST",
            raw=export_settings({"membership_price": "4.99"}),
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "This is a settings file, not a ledger.")
        _stored, after_settings = self.store.list_donations()
        self.assertEqual(after_settings, 1)

        status, _headers, page = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="archive-open"', page)
        self.assertIn(b'id="tab-archive"', page)
        self.assertIn(b'id="archive-file"', page)
        self.assertIn(b'id="archive-rows"', page)
        self.assertIn(b"The open ledger is not changed.", page)
        self.assertLess(page.index(b'id="tab-button-settings"'), page.index(b'id="archive-open"'))
        self.assertLess(page.index(b'id="archive-open"'), page.index(b'class="menu-sep"'))
        self.assertLess(page.index(b'class="menu-sep"'), page.index(b'id="reset-ledger"'))
        button = page.split(b'id="archive-open"', 1)[1].split(b"</button>", 1)[0]
        self.assertIn(b"Open archive", button)
        self.assertIn(b"View a JSONL export. It is not saved.", button)
        self.assertNotIn(b"data-tab", button)
        settings = page.split(b'id="tab-settings"', 1)[1].split(b"</dialog>", 1)[0]
        self.assertNotIn(b"archive-open", settings)
        self.assertNotIn(b"archive-file", settings)
        status, _headers, script = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"/api/ledger/archive", script)
        self.assertIn(b'openSheet("archive")', script)
        self.assertIn(b"Not saved.", script)
        self.assertIn(b"That ledger file is too large.", script)
        status, _headers, styles = self._request("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(b"#tab-archive.sheet", styles)
        self.assertIn(b"min(72rem, calc(100vw - 2rem))", styles)
        self.assertIn(b"#archive-rows tr", styles)

    def test_meta_reports_the_published_version(self):
        from streamdonations import __version__

        status, _headers, body = self._request("/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], __version__)

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
