"""Verify Kick webhook signatures.

Kick signs message_id + "." + timestamp + "." + raw body with RSA PKCS#1 v1.5
and SHA-256. Verification is plain Python (stdlib only), so it works the same
on Windows, where there is usually no openssl command.
The embedded key is the public key published in Kick's webhook docs. A newer
key from GET /public/v1/public-key replaces it when that call succeeds.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import datetime, timezone

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


# DER DigestInfo prefix for SHA-256 (RFC 8017 section 9.2, note 1).
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_RSA_ENCRYPTION_OID = bytes.fromhex("2a864886f70d010101")
_PEM_BODY = re.compile(r"-----BEGIN PUBLIC KEY-----(.*?)-----END PUBLIC KEY-----", re.S)
_MAX_MODULUS_BITS = 8192


def verify_rsa_sha256(public_key_pem: str, payload: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256 against a PEM SubjectPublicKeyInfo key."""
    if "BEGIN PUBLIC KEY" not in public_key_pem or not signature:
        return False
    try:
        modulus, exponent = _rsa_public_key(public_key_pem)
    except ValueError:
        return False
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    number = int.from_bytes(signature, "big")
    if number >= modulus:
        return False
    encoded = pow(number, exponent, modulus).to_bytes(size, "big")
    digest = _SHA256_DIGEST_INFO + hashlib.sha256(bytes(payload)).digest()
    padding = size - len(digest) - 3
    if padding < 8:
        return False
    expected = b"\x00\x01" + b"\xff" * padding + b"\x00" + digest
    return hmac.compare_digest(encoded, expected)


def _rsa_public_key(pem: str) -> tuple[int, int]:
    match = _PEM_BODY.search(pem)
    if match is None:
        raise ValueError("no public key")
    try:
        der = base64.b64decode("".join(match.group(1).split()), validate=True)
    except Exception as exc:
        raise ValueError("bad base64") from exc
    tag, info, rest = _der_read(der, 0)
    if tag != 0x30 or rest != len(der):
        raise ValueError("not a SubjectPublicKeyInfo")
    tag, algorithm, after = _der_read(der, info[0])
    if tag != 0x30:
        raise ValueError("no algorithm")
    oid_tag, oid, _ = _der_read(der, algorithm[0])
    if oid_tag != 0x06 or der[oid[0]:oid[1]] != _RSA_ENCRYPTION_OID:
        raise ValueError("not an RSA key")
    tag, bits, end = _der_read(der, after)
    if tag != 0x03 or end != info[1] or bits[1] - bits[0] < 2 or der[bits[0]] != 0:
        raise ValueError("no key bits")
    key = der[bits[0] + 1:bits[1]]
    tag, sequence, end = _der_read(key, 0)
    if tag != 0x30 or end != len(key):
        raise ValueError("not an RSAPublicKey")
    tag, n_span, after = _der_read(key, sequence[0])
    if tag != 0x02:
        raise ValueError("no modulus")
    tag, e_span, end = _der_read(key, after)
    if tag != 0x02 or end != sequence[1]:
        raise ValueError("no exponent")
    modulus = int.from_bytes(key[n_span[0]:n_span[1]], "big")
    exponent = int.from_bytes(key[e_span[0]:e_span[1]], "big")
    if modulus.bit_length() < 1024 or modulus.bit_length() > _MAX_MODULUS_BITS or modulus % 2 == 0:
        raise ValueError("unusable modulus")
    if exponent < 3 or exponent % 2 == 0:
        raise ValueError("unusable exponent")
    return modulus, exponent


def _der_read(data: bytes, offset: int) -> tuple[int, tuple[int, int], int]:
    """Tag, (start, end) of the contents, and the offset after this element."""
    if offset + 2 > len(data):
        raise ValueError("short DER")
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or offset + count > len(data):
            raise ValueError("bad DER length")
        length = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    end = offset + length
    if end > len(data):
        raise ValueError("DER runs past the end")
    return tag, (offset, end), end


def looks_like_public_key(value: str) -> bool:
    return "BEGIN PUBLIC KEY" in value and "END PUBLIC KEY" in value
