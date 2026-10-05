"""Recent public chat lines for the stream panel.

Lines stay in memory for the open window. They are not written to the ledger
and they are not logged.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone

from .util import format_utc, to_utc_iso, utc_now_iso

_PLATFORMS = {"youtube", "rumble", "kick"}
_LIMIT = 200


def clean_chat_text(value: object, limit: int) -> str:
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return " ".join(text.split())[:limit]


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
                self._lines.append(item)
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
            lines = [
                {
                    "id": row["id"],
                    "platform": row["platform"],
                    "author": row["author"],
                    "text": row["text"],
                    "time": row["time"],
                }
                for row in self._lines
                if row["id"] > start
            ]
            return {
                "lines": lines,
                "cursor": self._cursor,
                "generation": self._generation,
                "paused": paused,
            }


def offer_chat(feed: ChatFeed | None, platform: str, rows: list[dict]) -> None:
    """Record lines. A feed failure must not stop the donation that arrived with them."""
    if feed is None or not rows:
        return
    try:
        feed.add(platform, rows)
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
