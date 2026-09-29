"""Verify Kick webhook signatures.

Kick signs message_id + "." + timestamp + "." + raw body with RSA PKCS#1 v1.5
and SHA-256. Verification uses OpenSSL so the collector stays on the stdlib.
The embedded key is the public key published in Kick's webhook docs. A newer
key from GET /public/v1/public-key replaces it when that call succeeds.
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .util import parse_datetime

# Published at https://docs.kick.com/events/webhook-security
EMBEDDED_KICK_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAq/+l1WnlRrGSolDMA+A8
6rAhMbQGmQ2SapVcGM3zq8ANXjnhDWocMqfWcTd95btDydITa10kDvHzw9WQOqp2
MZI7ZyrfzJuz5nhTPCiJwTwnEtWft7nV14BYRDHvlfqPUaZ+1KR4OCaO/wWIk/rQ
L/TjY0M70gse8rlBkbo2a8rKhu69RQTRsoaf4DVhDPEeSeI5jVrRDGAMGL3cGuyY
6CLKGdjVEM78g3JfYOvDU/RvfqD7L89TZ3iN94jrmWdGz34JNlEI5hqK8dd7C5EF
BEbZ5jgB8s8ReQV8H+MkuffjdAj3ajDDX3DOJMIut1lBrUVD1AaSrGCKHooWoL2e
twIDAQAB
-----END PUBLIC KEY-----
"""


def signed_payload(message_id: str, timestamp: str, body: bytes) -> bytes:
    return message_id.encode("utf-8") + b"." + timestamp.encode("utf-8") + b"." + body


def decode_signature(value: str) -> bytes:
    text = (value or "").strip()
    if not text:
        raise ValueError("missing signature")
    padded = text + ("=" * (-len(text) % 4))
    try:
        return base64.b64decode(padded, validate=True)
    except Exception:
        return base64.urlsafe_b64decode(padded)


def webhook_is_fresh(timestamp: str, *, now: datetime | None = None, window_seconds: int = 1800) -> bool:
    parsed = parse_datetime(timestamp)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    return abs((current - parsed).total_seconds()) <= window_seconds


def verify_rsa_sha256(public_key_pem: str, payload: bytes, signature: bytes) -> bool:
    if "BEGIN PUBLIC KEY" not in public_key_pem or not signature:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="stream-donations-") as directory:
            root = Path(directory)
            key_path = root / "key.pem"
            sig_path = root / "sig.bin"
            data_path = root / "data.bin"
            key_path.write_text(public_key_pem, encoding="utf-8")
            sig_path.write_bytes(signature)
            data_path.write_bytes(payload)
            completed = subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(key_path),
                    "-signature",
                    str(sig_path),
                    str(data_path),
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and b"Verified OK" in completed.stdout


def looks_like_public_key(value: str) -> bool:
    return "BEGIN PUBLIC KEY" in value and "END PUBLIC KEY" in value
