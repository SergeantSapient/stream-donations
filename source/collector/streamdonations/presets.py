"""Streamer presets: a shared list of channel addresses and goal tiers.

A preset is not a ledger. Saving one does not change the open ledger, and
opening a ledger does not copy a preset until that ledger's address dialog
is saved with the preset selected. Presets stay out of the settings-file export.
"""

from __future__ import annotations

import json
import uuid

from .goals import normalize_tiers
from .kick_public import validate_kick_watch
from .rumble import validate_rumble_channel
from .youtube_public import validate_youtube_channel

PRESETS_KEY = "streamer_presets"
MAX_PRESETS = 40
MAX_NAME = 80
MAX_URL = 2000
_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"


def parse_presets(raw: str) -> list[dict]:
    """Stored presets. A broken document reads as none so the page still opens."""
    if not raw or not str(raw).strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    presets: list[dict] = []
    seen: set[str] = set()
    for item in parsed:
        preset = _stored_preset(item)
        if preset is None or preset["id"] in seen:
            continue
        seen.add(preset["id"])
        presets.append(preset)
        if len(presets) >= MAX_PRESETS:
            break
    return presets


def dump_presets(presets: list[dict]) -> str:
    return json.dumps(presets, ensure_ascii=False, separators=(",", ":"))


def public_presets(presets: list[dict]) -> list[dict]:
    return sorted(presets, key=lambda preset: preset["name"].casefold())


def upsert_preset(
    presets: list[dict],
    name: object,
    youtube_channel: object,
    rumble_channel: object,
    kick_watch: object,
    tiers_enabled: object = False,
    goal_tiers: object = None,
    script_id: object = "",
) -> tuple[list[dict], dict]:
    """Add a preset, or replace its channels and goals when the name already exists.

    Omitted goals mean tiers off and an empty list. A replace clears goals the
    same way an omitted channel clears that address. Goals alone are not enough.
    """
    fields = _preset_fields(name, youtube_channel, rumble_channel, kick_watch)
    fields.update(_goal_fields(tiers_enabled, goal_tiers))
    fields["script_id"] = _script_id(script_id)
    key = fields["name"].casefold()
    for preset in presets:
        if preset["name"].casefold() == key:
            preset.update(fields)
            return presets, dict(preset)
    if len(presets) >= MAX_PRESETS:
        raise ValueError("You can save up to 40 streamer presets.")
    saved = {"id": uuid.uuid4().hex, **fields}
    presets.append(saved)
    return presets, dict(saved)


def remove_preset(presets: list[dict], preset_id: object) -> tuple[list[dict], dict]:
    target = str(preset_id or "").strip()
    match = next((preset for preset in presets if preset["id"] == target), None)
    if match is None:
        raise ValueError("That streamer preset is not saved.")
    return [preset for preset in presets if preset["id"] != target], dict(match)


def _preset_fields(name: object, youtube_channel: object, rumble_channel: object, kick_watch: object) -> dict:
    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        raise ValueError("Name the streamer.")
    if len(cleaned) > MAX_NAME:
        raise ValueError("Streamer name is limited to 80 characters.")
    fields = {
        "name": cleaned,
        "youtube_channel": _channel(
            youtube_channel,
            validate_youtube_channel,
            "That is a video link. Paste the YouTube channel URL, such as https://www.youtube.com/@channel.",
        ),
        "rumble_channel": _channel(
            rumble_channel,
            validate_rumble_channel,
            "That is a video link. Paste the Rumble channel URL, such as https://rumble.com/c/channel.",
        ),
        "kick_watch": _channel(
            kick_watch,
            validate_kick_watch,
            "Paste a Kick channel URL, such as https://kick.com/channel.",
        ),
    }
    if not any(fields[key] for key in ("youtube_channel", "rumble_channel", "kick_watch")):
        raise ValueError("Add at least one channel URL.")
    return fields


def _goal_fields(tiers_enabled: object, goal_tiers: object) -> dict:
    if goal_tiers is None:
        tiers = normalize_tiers([])
    elif isinstance(goal_tiers, list):
        tiers = normalize_tiers(goal_tiers)
    else:
        raise ValueError("Goal tiers must be a list.")
    return {
        "tiers_enabled": _flag(tiers_enabled),
        "goal_tiers": [tier.as_dict() for tier in tiers],
    }


def _flag(value: object) -> bool:
    """True only for a real boolean or the usual on-words. bool("0") is True."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _channel(value: object, validate, video_message: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > MAX_URL:
        raise ValueError("That channel address is too long.")
    try:
        return validate(text)
    except ValueError as exc:
        if "video" in str(exc).lower():
            raise ValueError(video_message) from exc
        raise


def _stored_preset(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    preset_id = str(item.get("id") or "").strip()
    name = " ".join(str(item.get("name") or "").split())
    if (
        not preset_id
        or not name
        or len(preset_id) > 64
        or len(name) > MAX_NAME
        or any(char not in _ID_CHARS for char in preset_id)
    ):
        return None
    goals = _stored_goals(item)
    return {
        "id": preset_id,
        "name": name,
        "youtube_channel": _stored_url(item.get("youtube_channel")),
        "rumble_channel": _stored_url(item.get("rumble_channel")),
        "kick_watch": _stored_url(item.get("kick_watch")),
        "tiers_enabled": goals["tiers_enabled"],
        "goal_tiers": goals["goal_tiers"],
        "script_id": _script_id(item.get("script_id")),
    }


def _stored_goals(item: dict) -> dict:
    """Older presets have no goals. A broken list reads as empty, not as a drop."""
    raw = item.get("goal_tiers")
    tiers = normalize_tiers(raw if isinstance(raw, list) else None)
    return {
        "tiers_enabled": _flag(item.get("tiers_enabled")),
        "goal_tiers": [tier.as_dict() for tier in tiers],
    }


def _script_id(value: object) -> str:
    """A saved script id, or none. A broken id is dropped rather than the preset."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > 64 or any(char not in _ID_CHARS for char in text):
        return ""
    return text


def _stored_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:MAX_URL]
