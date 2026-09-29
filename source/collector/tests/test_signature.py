"""Kick webhook signatures, using OpenSSL the same way the collector does."""

import base64
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from streamdonations.kick_sig import signed_payload, verify_rsa_sha256, webhook_is_fresh


def _openssl(*args, input_bytes=None):
    return subprocess.run(
        ["openssl", *args],
        input=input_bytes,
        capture_output=True,
        check=True,
    )


class SignatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.key = root / "key.pem"
        self.pub = root / "pub.pem"
        _openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(self.key))
        _openssl("rsa", "-in", str(self.key), "-pubout", "-out", str(self.pub))
        self.public_pem = self.pub.read_text(encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _sign(self, payload: bytes) -> str:
        signature = _openssl("dgst", "-sha256", "-sign", str(self.key), input_bytes=payload).stdout
        return base64.b64encode(signature).decode("ascii")

    def test_accepts_a_matching_signature_and_rejects_tampering(self):
        body = b'{"gift":{"amount":1}}'
        payload = signed_payload("01MSG", "2026-09-24T12:00:00Z", body)
        signature = self._sign(payload)
        raw = base64.b64decode(signature)
        self.assertTrue(verify_rsa_sha256(self.public_pem, payload, raw))
        tampered = payload[:-1] + b"x"
        self.assertFalse(verify_rsa_sha256(self.public_pem, tampered, raw))

    def test_freshness_window(self):
        now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
        fresh = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        stale = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertTrue(webhook_is_fresh(fresh, now=now))
        self.assertFalse(webhook_is_fresh(stale, now=now, window_seconds=1800))
        self.assertFalse(webhook_is_fresh("not-a-date", now=now))
        # The helper is what the webhook uses; the body itself is irrelevant here.
        json.dumps({"ok": True})


if __name__ == "__main__":
    unittest.main()
