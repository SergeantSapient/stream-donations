"""Recent public chat lines for the stream panel.

Lines stay in memory for the open window, ordered by each message's own clock.
A late poll is inserted at that time. They are not written to the ledger and
they are not logged.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone

from urllib.parse import urlparse

from .util import format_utc, to_utc_iso, utc_now_iso

_PLATFORMS = {"youtube", "rumble", "kick"}
_LIMIT = 200


# Not printable by Python's rules, but part of an emoji: the zero-width joiner
# (family and profession emoji) and the tag characters (subdivision flags).
_EMOJI_JOINERS = frozenset({"\u200d"}) | frozenset(chr(code) for code in range(0xE0020, 0xE0080))
_MAX_PARTS = 120


def _visible(ch: str) -> bool:
    return ch.isprintable() or ch in _EMOJI_JOINERS


def clean_chat_text(value: object, limit: int) -> str:
    text = "".join(ch if _visible(ch) else " " for ch in str(value or ""))
    return " ".join(text.split())[:limit]


# Custom YouTube emoji pictures come from yt3.ggpht.com (seen in live chat,
# 2026-10-19). yt4 is YouTube's other avatar host. The page's img-src names
# exactly these hosts, without a wildcard.
EMOJI_IMAGE_HOSTS = ("yt3.ggpht.com", "yt4.ggpht.com")


def emoji_image_ok(url: object) -> bool:
    """An https image on a YouTube emoji host, with no login or port."""
    text = str(url or "")
    if not text or len(text) > 500 or any(ch.isspace() or ch in "\\\"'<>" for ch in text):
        return False
    parsed = urlparse(text)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port not in (None, 443):
        return False
    host = (parsed.hostname or "").lower()
    return host in EMOJI_IMAGE_HOSTS


def clean_chat_parts(value: object, limit: int) -> list[dict]:
    """Display pieces for one line: text, and custom emoji pictures on YouTube's image hosts.

    A picture whose address is not allowed becomes its shortcut text. The pieces
    never hold more text than the line itself.
    """
    if not isinstance(value, list) or not value:
        return []
    parts: list[dict] = []
    used = 0
    pictured = False
    for piece in value[:_MAX_PARTS]:
        if not isinstance(piece, dict):
            continue
        shortcut = clean_chat_text(piece.get("emoji"), 80) if "emoji" in piece else ""
        if "emoji" in piece and emoji_image_ok(piece.get("src")) and shortcut:
            if used + len(shortcut) > limit:
                break
            parts.append({"emoji": shortcut, "src": str(piece["src"])})
            used += len(shortcut)
            pictured = True
            continue
        raw = shortcut if "emoji" in piece else str(piece.get("text") or "")
        text = _squash_spaces("".join(ch if _visible(ch) else " " for ch in raw))
        if not text:
            continue
        if not parts and text.startswith(" "):
            text = text.lstrip()
        text = text[: max(0, limit - used)]
        if not text:
            break
        if parts and "text" in parts[-1]:
            parts[-1] = {"text": _squash_spaces(parts[-1]["text"] + text)}
        else:
            parts.append({"text": text})
        used += len(text)
    if parts and "text" in parts[-1]:
        parts[-1] = {"text": parts[-1]["text"].rstrip()}
        if not parts[-1]["text"]:
            parts.pop()
    return parts if pictured else []


def _squash_spaces(text: str) -> str:
    out = []
    for ch in text:
        if ch.isspace():
            if out and out[-1] == " ":
                continue
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def chat_time(value: object) -> str:
    """UTC time from a chat payload. Blank when the platform did not send one."""
    if isinstance(value, str):
        parsed = to_utc_iso(value)
        if parsed:
            return parsed
        text = value.strip()
        if not text.isdigit() or len(text) > 16:
            return ""
        value = int(text)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    number = int(value)
    if number >= 10**14:
        seconds = number / 1_000_000
    elif number >= 10**11:
        seconds = number / 1_000
    else:
        seconds = float(number)
    if seconds < 1_000_000_000 or seconds > 4_000_000_000:
        return ""
    return format_utc(datetime.fromtimestamp(seconds, tz=timezone.utc))


def _insert_by_time(lines: deque[dict], item: dict) -> None:
    """Place a line by its clock. The same second keeps the earlier id."""
    for index, row in enumerate(lines):
        if item["time"] < row["time"] or (item["time"] == row["time"] and item["id"] < row["id"]):
            lines.insert(index, item)
            return
    lines.append(item)


class ChatFeed:
    def __init__(self, limit: int = _LIMIT):
        self._limit = limit
        self._lock = threading.Lock()
        self._lines: deque[dict] = deque()
        self._seen: set[str] = set()
        self._cursor = 0
        self._generation = 0
        self.on_lines = None

    def add(self, platform: str, rows: list[dict]) -> None:
        if platform not in _PLATFORMS or not rows:
            return
        added: list[dict] = []
        with self._lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                text = clean_chat_text(row.get("text"), 500)
                if not text:
                    continue
                author = clean_chat_text(row.get("author"), 80) or "Someone"
                source = clean_chat_text(row.get("key"), 120)
                token = f"{platform}:{source}" if source else ""
                if token and token in self._seen:
                    continue
                when = chat_time(row.get("time")) or utc_now_iso()
                self._cursor += 1
                item = {
                    "id": self._cursor,
                    "platform": platform,
                    "author": author,
                    "text": text,
                    "time": when,
                    "key": token,
                }
                parts = clean_chat_parts(row.get("parts"), 500)
                if parts:
                    item["parts"] = parts
                _insert_by_time(self._lines, item)
                added.append({
                    "platform": platform,
                    "author": author,
                    "text": text,
                    "time": when,
                })
                if token:
                    self._seen.add(token)
                while len(self._lines) > self._limit:
                    old = self._lines.popleft()
                    if old["key"]:
                        self._seen.discard(old["key"])
        listener = self.on_lines
        if added and listener is not None:
            try:
                listener(added)
            except Exception:
                return

    def clear(self, platform: str | None = None) -> None:
        """Drop one platform, or every line. The generation tells the panel to repaint."""
        with self._lock:
            if platform is not None and platform not in _PLATFORMS:
                return
            self._generation += 1
            if platform is None:
                self._lines.clear()
                self._seen.clear()
                return
            kept = deque(row for row in self._lines if row["platform"] != platform)
            self._lines = kept
            self._seen = {row["key"] for row in kept if row["key"]}

    def since(self, after: int, generation: int, paused: bool) -> dict:
        with self._lock:
            start = 0 if generation != self._generation else after
            lines = []
            for row in self._lines:
                if row["id"] <= start:
                    continue
                line = {
                    "id": row["id"],
                    "platform": row["platform"],
                    "author": row["author"],
                    "text": row["text"],
                    "time": row["time"],
                }
                if row.get("parts"):
                    line["parts"] = [dict(piece) for piece in row["parts"]]
                lines.append(line)
            lines.sort(key=lambda row: (row["time"], row["id"]))
            return {
                "lines": lines,
                "cursor": self._cursor,
                "generation": self._generation,
                "paused": paused,
            }


class CommandTap:
    """Chat for a ledger that is not on screen. Lines go to chat commands only.

    The panel and the giveaway follow the open ledger, so nothing is kept here.
    """

    def __init__(self, listen, stream_id: str):
        self._listen = listen
        self.stream_id = stream_id

    def add(self, platform: str, rows: list[dict]) -> None:
        return None

    def clear(self, platform: str | None = None) -> None:
        return None

    def command_tap(self, platform: str, rows: list[dict], *, chat: str = "", fresh: bool = False) -> None:
        self._listen(platform, rows, stream_id=self.stream_id, chat=chat, fresh=fresh)


def offer_chat(
    feed: ChatFeed | None,
    platform: str,
    rows: list[dict],
    *,
    chat: str = "",
    fresh: bool = False,
) -> None:
    """Record lines. A feed failure must not stop the donation that arrived with them.

    chat is the chat these lines came from (video id, chat id, or channel slug).
    fresh marks the first batch after a connect, which can hold history.
    """
    if feed is None or not rows:
        return
    try:
        feed.add(platform, rows)
    except Exception:
        pass
    tap = getattr(feed, "command_tap", None)
    if tap is None:
        return
    try:
        tap(platform, rows, chat=chat, fresh=fresh)
    except Exception:
        return


def remember_chat(feed: ChatFeed | None, platform: str, previous: str, target: str) -> str:
    """Clear one platform when its watch target changes. Pause and thread restarts do not."""
    if target == previous:
        return previous
    if feed is not None:
        try:
            feed.clear(platform)
        except Exception:
            return target
    return target
