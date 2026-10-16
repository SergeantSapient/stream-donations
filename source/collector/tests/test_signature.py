"""Kick webhook signatures. The collector checks them in plain Python.

A fixed vector runs everywhere. Fresh keys from the openssl command run
where that command exists, as a cross-check.
"""

import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from streamdonations.kick_sig import (
    EMBEDDED_KICK_PUBLIC_KEY,
    signed_payload,
    verify_rsa_sha256,
    webhook_is_fresh,
)

# Made once with: openssl genpkey RSA 2048, then openssl dgst -sha256 -sign.
VECTOR_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAzMi9mqzkucW20Ond/QkN
j86u+bUyw5NTNHr/Mj4fnL7zsQLl/jzYQNNl4o2iUwL85DJOBekhlwOy3nMvxkjp
KnguX2gkcPmzFDwUvMTW8juUKT7WuV/OLKqC1CMMWzfsbHk4j5MLRJCjc0TX1UYE
rryM0wQKzXkFp9/YnvGpVfOGwFcUssXyqEtQu69z7si5xRb3DiKngnfePD72pmiW
AE11grrfZ2DNGFbeajQ7lhi48q+qKYhZ425Uvaa4kXgHX0h2Rt9/r8EOq13A0MTh
qn3IWFDFNKKDNE3attvnlRdmmwShu5rpfFXgLtTt5OWPAPctE87k3f1hFtWfvxHV
iwIDAQAB
-----END PUBLIC KEY-----
"""
VECTOR_PAYLOAD = signed_payload("01VECTOR", "2026-10-16T19:00:00Z", b'{"kicks":{"amount":100}}')
VECTOR_SIGNATURE = base64.b64decode(
    "g7qbkd4hqdRWc9JXK4ebF0IgHgKPW4chhKwN/RG+lGrMiVRoS8/FgOw6uCFyR34zPQUzoGEXv8+/8vVv/tsOm5xwQ1mxXfMWpGmyGtBoq5acuTN4Ef+sTF+Fpk/Ma/VTlWM4xOkKWe6aWtOzyIQTdQ5kVMEABSz2jiHMZo+f+N9uwDtfgvX8AZYPwFaNk6CjgXuqvl3v4Drrnsq/RM6x4KL81wxcEkPTuOtV7FTBdG76rkLILW0R2qtUYuAt2qqWQjFSLq1FDmMFeuu7uRt2gIntlneQdIjrr7ggh7aw4kBZbLi/NC7lFL0w7gI1wUHFhaE8nLZdo72kPPeDiCIZIw=="
)


class KnownVectorTests(unittest.TestCase):
    def test_known_vector_verifies_without_openssl(self):
        self.assertTrue(verify_rsa_sha256(VECTOR_PUBLIC_KEY, VECTOR_PAYLOAD, VECTOR_SIGNATURE))

    def test_known_vector_rejects_changes(self):
        self.assertFalse(verify_rsa_sha256(VECTOR_PUBLIC_KEY, VECTOR_PAYLOAD + b" ", VECTOR_SIGNATURE))
        flipped = bytearray(VECTOR_SIGNATURE)
        flipped[-1] ^= 1
        self.assertFalse(verify_rsa_sha256(VECTOR_PUBLIC_KEY, VECTOR_PAYLOAD, bytes(flipped)))
        self.assertFalse(verify_rsa_sha256(VECTOR_PUBLIC_KEY, VECTOR_PAYLOAD, VECTOR_SIGNATURE[:-1]))
        self.assertFalse(verify_rsa_sha256(EMBEDDED_KICK_PUBLIC_KEY, VECTOR_PAYLOAD, VECTOR_SIGNATURE))
        self.assertFalse(verify_rsa_sha256(VECTOR_PUBLIC_KEY, VECTOR_PAYLOAD, b""))

    def test_broken_keys_are_refused(self):
        self.assertFalse(verify_rsa_sha256("not a key", VECTOR_PAYLOAD, VECTOR_SIGNATURE))
        broken = "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n"
        self.assertFalse(verify_rsa_sha256(broken, VECTOR_PAYLOAD, VECTOR_SIGNATURE))
        truncated = VECTOR_PUBLIC_KEY.replace("iwIDAQAB", "")
        self.assertFalse(verify_rsa_sha256(truncated, VECTOR_PAYLOAD, VECTOR_SIGNATURE))

    def test_no_openssl_command_is_needed(self):
        source = Path(__file__).resolve().parents[1] / "streamdonations" / "kick_sig.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("subprocess", text)
        self.assertNotIn('"openssl"', text)


@unittest.skipUnless(shutil.which("openssl"), "openssl command not installed")


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
