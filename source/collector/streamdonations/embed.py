"""Player URLs for the open ledger's saved addresses.

Kick is local. A YouTube or Rumble video id already in the address is local
too. Follow live calls the same page lookup the collectors use, passed in so
this module never fetches on its own. The returned URL is only an official
player address, never the saved channel or video URL.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urlparse

from .kick_public import kick_slug
from .rumble import embed_to_chat_id
from .util import ApiError
from .youtube_public import video_id_from_target

# Origins the ledger page may frame. No wildcard. frame-ancestors stays 'none'.
FRAME_SRC = (
    "https://www.youtube.com",
    "https://www.youtube-nocookie.com",
    "https://rumble.com",
    "https://player.kick.com",
)

# Same reserved first path segments kick_slug refuses on a channel URL.
_KICK_RESERVED = {"api", "settings", "dashboard", "browse", "categories", "following"}
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def youtube_player_url(video_id: str) -> str:
    """https://www.youtube.com/embed/VIDEO_ID with no autoplay parameter."""
    text = (video_id or "").strip()
    if video_id_from_target(text) != text:
        raise ValueError("That is not a YouTube video id.")
    return f"https://www.youtube.com/embed/{text}"


def rumble_player_url(chat_id: str) -> str:
    """https://rumble.com/embed/v{base36}/ — the inverse of embed_to_chat_id."""
    text = str(chat_id or "").strip()
    if not re.fullmatch(r"[0-9]{1,20}", text):
        raise ValueError("That is not a Rumble chat id.")
    token = "v" + _base36(int(text))
    if embed_to_chat_id(token) != str(int(text)):
        raise ValueError("That is not a Rumble chat id.")
    return f"https://rumble.com/embed/{token}/"


def kick_player_url(value: str) -> str:
    """https://player.kick.com/SLUG from a channel URL or slug. No request."""
    slug = kick_slug(value)
    if not slug or slug in _KICK_RESERVED or not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", slug):
        raise ValueError("Paste a Kick channel URL or slug.")
    return f"https://player.kick.com/{slug}"


def allowed_player_url(value: str) -> bool:
    """True only for the player URLs this module builds."""
    parsed = urlparse(value or "")
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 443) or parsed.query or parsed.fragment:
        return False
    host = (parsed.hostname or "").lower()
    if host in {"www.youtube.com", "www.youtube-nocookie.com"}:
        return bool(re.fullmatch(r"/embed/[A-Za-z0-9_-]{11}", parsed.path))
    if host == "rumble.com":
        return bool(re.fullmatch(r"/embed/v[0-9a-z]{4,16}/", parsed.path))
    if host == "player.kick.com":
        match = re.fullmatch(r"/([A-Za-z0-9_-]{1,50})", parsed.path)
        return bool(match) and match.group(1).lower() not in _KICK_RESERVED
    return False


def player_for_ledger(
    platform: str,
    settings: dict,
    *,
    youtube_video: Callable[[str], str],
    youtube_live: Callable[[str], str | None],
    rumble_chat: Callable[[str], str],
    rumble_live: Callable[[str], str | None],
) -> dict:
    """`{platform, url, message}`. An empty url is a note, not a failure.

    Unknown platform raises ValueError. ApiError from a lookup is re-raised
    after the saved address is removed from its message.
    """
    name = (platform or "").strip().lower()
    if name not in {"youtube", "rumble", "kick"}:
        raise ValueError("Stream source must be YouTube, Rumble, or Kick.")
    saved = _saved(settings or {})
    try:
        if name == "youtube":
            url, message = _youtube(settings or {}, youtube_video, youtube_live)
        elif name == "rumble":
            url, message = _rumble(settings or {}, rumble_chat, rumble_live)
        else:
            url, message = _kick(settings or {})
    except ApiError as exc:
        cleaned = _safe_message(str(exc), saved)
        if cleaned != " ".join(str(exc).split()):
            raise ApiError(cleaned, status=exc.status) from exc
        raise
    except ValueError as exc:
        return {"platform": name, "url": "", "message": _safe_message(str(exc), saved)}
    if url and not allowed_player_url(url):
        return {"platform": name, "url": "", "message": "That stream could not be embedded."}
    return {"platform": name, "url": url, "message": message}


def _youtube(settings: dict, youtube_video: Callable[[str], str], youtube_live: Callable[[str], str | None]) -> tuple[str, str]:
    # "0" is off. bool("0") would be on, so this is a string compare.
    if _flag(settings.get("youtube_follow_live")):
        channel = _text(settings, "youtube_channel")
        if not channel:
            return "", "This ledger has no YouTube channel to embed."
        video_id = youtube_live(channel)
        if not video_id:
            return "", "That YouTube channel is not live."
        return youtube_player_url(str(video_id).strip()), ""
    watch = _text(settings, "youtube_watch")
    if not watch:
        return "", "This ledger has no YouTube video to embed."
    video_id = video_id_from_target(watch)
    if not video_id:
        video_id = youtube_video(watch)
    return youtube_player_url(str(video_id).strip()), ""


def _rumble(settings: dict, rumble_chat: Callable[[str], str], rumble_live: Callable[[str], str | None]) -> tuple[str, str]:
    if _flag(settings.get("rumble_follow_live")):
        channel = _text(settings, "rumble_channel")
        if not channel:
            return "", "This ledger has no Rumble channel to embed."
        chat_id = rumble_live(channel)
        if not chat_id:
            return "", "That Rumble channel is not live."
        return rumble_player_url(str(chat_id).strip()), ""
    watch = _text(settings, "rumble_watch")
    if not watch:
        return "", "This ledger has no Rumble video to embed."
    return rumble_player_url(str(rumble_chat(watch)).strip()), ""


def _kick(settings: dict) -> tuple[str, str]:
    watch = _text(settings, "kick_watch")
    if not watch:
        return "", "This ledger has no Kick channel to embed."
    return kick_player_url(watch), ""


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip() == "1"


def _text(settings: dict, key: str) -> str:
    return str(settings.get(key) or "").strip()


def _saved(settings: dict) -> list[str]:
    found = []
    for key in ("youtube_watch", "youtube_channel", "rumble_watch", "rumble_channel", "kick_watch"):
        text = _text(settings, key)
        if text:
            found.append(text)
    return found


def _safe_message(message: str, saved: list[str]) -> str:
    text = " ".join(str(message or "").split())
    folded = text.casefold()
    for item in saved:
        token = item.strip()
        if len(token) >= 8 and token.casefold() in folded:
            return "That address could not be embedded."
    return text or "That address could not be embedded."


def _base36(number: int) -> str:
    if number < 0:
        raise ValueError("That is not a Rumble chat id.")
    if number == 0:
        return "0"
    body = ""
    value = number
    while value:
        value, remainder = divmod(value, 36)
        body = _BASE36[remainder] + body
    return body
