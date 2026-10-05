"""Verbose ledger payloads, the 100-line page, and the unbounded log file."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from streamdonations.normalize import Donation
from streamdonations.payload_log import compact_payload, payload_message
from streamdonations.store import LOG_NAME, Store

_SECRET = "payload-secret-value"


def _donation(raw, key="youtube:test:1"):
    return Donation(
        dedupe_key=key,
        platform="youtube",
        kind="super_chat",
        external_id=key.rsplit(":", 1)[-1],
        channel="",
        author_name="kayeend",
        author_id="",
        message="tip",
        amount_value=50.0,
        amount_micros=50_000_000,
        currency="PHP",
        created_at="2026-10-01T04:40:52Z",
        raw=raw,
    )


class PayloadCompactTests(unittest.TestCase):
    def test_drops_presentation_fields_and_redacts_secrets(self):
        raw = {
            "authorName": {"simpleText": "kayeend"},
            "purchaseAmountText": {"simpleText": "₱50.00"},
            "access_token": _SECRET,
            "authorization": f"Bearer {_SECRET}",
            "authorPhoto": {"thumbnails": [{"url": "https://example.test/thumb.jpg"}]},
            "trackingParams": "track-me",
            "accessibility": {"accessibilityData": {"label": "fire"}},
            "message": {
                "runs": [{
                    "text": "juicy",
                    "navigationEndpoint": {"url": f"https://example.test/go?key={_SECRET}"},
                }],
            },
            "watch": f"https://rumble.example/api?key={_SECRET}&ok=1",
            "note": f"sd_session={_SECRET}",
            "count": 0,
            "thumbnails": [],
        }
        compacted = compact_payload(raw)
        encoded = json.dumps(compacted, ensure_ascii=False)
        self.assertNotIn(_SECRET, encoded)
        self.assertNotIn("thumb.jpg", encoded)
        self.assertNotIn("track-me", encoded)
        self.assertNotIn("fire", encoded)
        self.assertNotIn("navigationEndpoint", encoded)
        self.assertIn("kayeend", encoded)
        self.assertIn("₱50.00", encoded)
        self.assertIn("juicy", encoded)
        self.assertIn('"count":0', encoded.replace(" ", ""))
        self.assertIn("key=REDACTED", encoded)
        self.assertIn("sd_session=REDACTED", encoded)
        self.assertEqual(compacted["access_token"], "REDACTED")
        self.assertEqual(compacted["authorization"], "REDACTED")

    def test_keeps_a_name_that_only_looks_like_a_secret_key(self):
        compacted = compact_payload({"authorName": {"simpleText": "tokenmaster"}})
        self.assertEqual(compacted["authorName"]["simpleText"], "tokenmaster")

    def test_message_is_one_compact_json_line(self):
        text = payload_message({"manual": True, "note": "a\nb"}, "manual:abc")
        self.assertTrue(text.startswith("payload manual:abc {"))
        self.assertNotIn("\n", text)
        self.assertIn('"manual":true', text)


class LogFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_verbose_off_skips_payloads_and_duplicate_inserts_stay_quiet(self):
        donation = _donation({"purchaseAmountText": {"simpleText": "₱50.00"}, "id": "one"})
        self.assertFalse(self.store.verbose_logs())
        self.assertEqual(self.store.add_many([donation]), 1)
        self.assertEqual(self.store.add_many([donation]), 0)
        self.assertFalse(any(row["message"].startswith("payload ") for row in self.store.recent_log()))

        self.store.set_verbose_logs(True)
        again = _donation({"purchaseAmountText": {"simpleText": "₱50.00"}, "id": "two"}, "youtube:test:2")
        duplicate = _donation({"purchaseAmountText": {"simpleText": "₱50.00"}, "id": "two"}, "youtube:test:2")
        self.assertEqual(self.store.add_many([again, duplicate]), 1)
        payloads = [row["message"] for row in self.store.recent_log() if row["message"].startswith("payload ")]
        self.assertEqual(payloads, [payload_message(again.raw, again.dedupe_key)])

    def test_manual_entry_logs_its_payload_only_when_verbose(self):
        self.store.set_verbose_logs(True)
        self.store.add_manual_entry(
            author_name="kayeend",
            platform="youtube",
            kind="super_chat",
            amount="50.00",
            currency="PHP",
            message="juicy tip",
        )
        messages = [row["message"] for row in self.store.recent_log()]
        self.assertTrue(any(message.startswith("Manual super_chat from kayeend") for message in messages))
        self.assertTrue(any('"manual":true' in message for message in messages))
        self.assertNotIn(_SECRET, " ".join(messages))

    def test_file_has_no_length_cap_and_restarts_after_delete(self):
        long_line = "x" * 5000
        self.store.log("ledger", "info", long_line)
        path = self.store.log_path()
        self.assertEqual(path.name, LOG_NAME)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertIn(long_line, path.read_text(encoding="utf-8"))
        self.assertEqual(self.store.recent_log(1)[0]["message"], long_line)

        for index in range(401):
            self.store.log("ledger", "info", f"row-{index}")
        text = path.read_text(encoding="utf-8")
        self.assertIn(long_line, text)
        self.assertIn("row-0", text)
        self.assertIn("row-400", text)
        with self.store._lock:
            kept = self.store._conn.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0]
        self.assertEqual(kept, 400)
        page = self.store.recent_log()
        self.assertEqual(len(page), 100)
        self.assertEqual(page[0]["message"], "row-400")
        self.assertEqual(page[-1]["message"], "row-301")
        self.assertNotIn("row-0", [row["message"] for row in page])

        path.unlink()
        self.assertFalse(path.exists())
        self.store.log("ledger", "info", "after-delete")
        restarted = path.read_text(encoding="utf-8")
        self.assertIn("after-delete", restarted)
        self.assertNotIn("row-0", restarted)
        self.assertNotIn(long_line, restarted)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_symlink_is_not_followed(self):
        target = Path(self.tmp.name) / "elsewhere.txt"
        target.write_text("untouched", encoding="utf-8")
        link = self.store.log_path()
        link.symlink_to(target)
        self.store.log("ledger", "info", "should-not-follow")
        self.assertEqual(target.read_text(encoding="utf-8"), "untouched")
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(target))
