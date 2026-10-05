"""Compact a ledger payload for the verbose log.

Photos, thumbnails, tracking fields, and accessibility images are dropped.
Secret-looking keys and query values are replaced. The JSON has no length cap.
"""

from __future__ import annotations

import json
import re

_DROP = frozenset({
    "accessibility",
    "authorbadges",
    "authorphoto",
    "avatar",
    "clicktrackingparams",
    "contextmenuendpoint",
    "customemoji",
    "emoji",
    "image",
    "images",
    "loggingdirectives",
    "navigationendpoint",
    "profileimage",
    "sticker",
    "thumbnail",
    "thumbnails",
    "trackingparams",
})

_SECRET_NAMES = frozenset({
    "accesstoken",
    "apikey",
    "authorization",
    "clientsecret",
    "codeverifier",
    "cookie",
    "idtoken",
    "key",
    "nextpagetoken",
    "pagetoken",
    "password",
    "refreshtoken",
    "secret",
    "session",
    "token",
})

_QUERY_SECRET = re.compile(
    r"(?i)(access_token|refresh_token|client_secret|code_verifier|token|key|code)=([^&\s]+)"
)
_BEARER = re.compile(r"(?i)(bearer\s+)\S+")
_SESSION = re.compile(r"(?i)(sd_session=)[^;\s&]+")

_OMIT = object()


def compact_payload(value: object) -> object:
    """Return a JSON-ready copy with presentation fields and secrets removed."""
    compacted = _compact(value)
    if compacted is _OMIT:
        return {}
    return compacted


def payload_message(raw: object, dedupe_key: str) -> str:
    body = json.dumps(
        compact_payload(raw),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return f"payload {dedupe_key} {body}"


def _compact(value: object) -> object:
    if isinstance(value, dict):
        kept: dict[str, object] = {}
        for key, item in value.items():
            name = key if isinstance(key, str) else str(key)
            if _drop_key(name):
                continue
            if _secret_key(name):
                kept[name] = "REDACTED"
                continue
            compacted = _compact(item)
            if compacted is _OMIT:
                continue
            kept[name] = compacted
        return kept if kept else _OMIT
    if isinstance(value, list):
        kept_items = []
        for item in value:
            compacted = _compact(item)
            if compacted is _OMIT:
                continue
            kept_items.append(compacted)
        return kept_items if kept_items else _OMIT
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_text(str(value))


def _drop_key(name: str) -> bool:
    folded = re.sub(r"[^a-z0-9]", "", name.lower())
    if folded in _DROP:
        return True
    return "thumbnail" in folded


def _secret_key(name: str) -> bool:
    lower = name.lower()
    folded = re.sub(r"[^a-z0-9]", "", lower)
    if folded in _SECRET_NAMES:
        return True
    if lower.endswith((
        "_token", "-token", "_secret", "-secret", "_password", "-password",
        "_key", "-key", "_cookie", "-cookie",
    )):
        return True
    return (
        "clientsecret" in folded
        or "accesstoken" in folded
        or "refreshtoken" in folded
    )


def _redact_text(text: str) -> str:
    text = _QUERY_SECRET.sub(r"\1=REDACTED", text)
    text = _BEARER.sub(r"\1REDACTED", text)
    return _SESSION.sub(r"\1REDACTED", text)
