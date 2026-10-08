"""Compact settings file for the Settings tab.

The document is one JSON object with a kind and an integer version. A newer
file may add keys. This reader keeps the keys it understands, ignores the
rest, and does not change a saved setting the file leaves out. API keys and
secrets are never written into the file, and a file that contains them does
not get those values applied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

KIND = "stream-donations-settings"
VERSION = 1
MAX_BYTES = 256 * 1024

# Settings tab only. Add a key here when that tab grows.
TAB_KEYS = (
    "membership_price",
    "show_top_donors",
    "chat_extra",
    "ledger_page_size",
)

# Never copy these into a file or apply them from one, even if a later edit
# puts one of them in TAB_KEYS by mistake.
SECRET_KEYS = frozenset({
    "youtube_client_id",
    "youtube_client_secret",
    "youtube_refresh_token",
    "youtube_access_token",
    "youtube_access_expiry",
    "kick_client_id",
    "kick_client_secret",
    "kick_refresh_token",
    "kick_access_token",
    "kick_access_expiry",
    "rumble_api_url",
    "fx_response_json",
})

_TOP_LEVEL = frozenset({"kind", "version", "settings"})


@dataclass(frozen=True)
class SettingsFile:
    version: int
    settings: dict
    ignored: tuple[str, ...]


def export_settings(values: dict) -> bytes:
    if not isinstance(values, dict):
        raise ValueError("Settings must be an object.")
    settings = {
        key: values[key]
        for key in TAB_KEYS
        if key in values and key not in SECRET_KEYS
    }
    document = {"kind": KIND, "version": VERSION, "settings": settings}
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_settings_file(body: bytes) -> SettingsFile:
    if isinstance(body, bytearray):
        body = bytes(body)
    if not isinstance(body, bytes):
        raise ValueError("Settings file was not JSON.")
    if len(body) > MAX_BYTES:
        raise ValueError("Settings file is too large.")
    if body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    if not body.strip():
        raise ValueError("Settings file was empty.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Settings file was not JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Settings file must be an object.")
    if payload.get("kind") != KIND:
        raise ValueError("This file is not a SuperBerry settings file.")
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("Settings file version must be a whole number of 1 or higher.")
    raw = payload.get("settings", {})
    if not isinstance(raw, dict):
        raise ValueError("Settings in the file must be an object.")
    settings = {
        key: raw[key]
        for key in TAB_KEYS
        if key in raw and key not in SECRET_KEYS
    }
    ignored = [
        str(key)
        for key in raw
        if key not in TAB_KEYS or key in SECRET_KEYS
    ]
    ignored.extend(str(key) for key in payload if key not in _TOP_LEVEL)
    return SettingsFile(version, settings, tuple(sorted(set(ignored))))
