"""One in-memory giveaway drawing. Nothing is typed into chat or stored."""

from __future__ import annotations

import secrets
import threading
import time

_PLATFORMS = ("youtube", "rumble", "kick")
_LABELS = {"youtube": "YouTube", "rumble": "Rumble", "kick": "Kick"}
_LIMIT = 5000
_MAX_SECONDS = 24 * 60 * 60


def keyword_announce(keyword: str) -> str:
    text = " ".join(str(keyword or "").split())
    if not text:
        return ""
    return f"Type {text} in chat to enter the giveaway."


def winner_announce(winner: dict | None) -> str:
    if not winner:
        return ""
    platform = _LABELS.get(str(winner.get("platform") or ""), "")
    author = " ".join(str(winner.get("author") or "").split())
    if not platform or not author:
        return ""
    return f"The giveaway winner is {author} on {platform}."


class Giveaway:
    def __init__(self, choose=None, limit: int = _LIMIT):
        self.choose = choose or secrets.choice
        self._limit = limit
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._running = False
        self._closed = False
        self._keyword = ""
        self._needle = ""
        self._seconds = 0
        self._deadline: float | None = None
        self._entries: list[dict] = []
        self._seen: set[str] = set()
        self._winner: dict | None = None
        self._winners: list[dict] = []

    def start(self, keyword: object, seconds: object = 0) -> dict:
        text = " ".join(str(keyword or "").split())
        if not text or len(text) > 80:
            raise ValueError("Giveaway keyword must be 1 to 80 characters.")
        duration = _seconds(seconds)
        with self._lock:
            if self._running:
                raise ValueError("A giveaway is already running.")
            self._cancel_timer_locked()
            self._running = True
            self._closed = False
            self._keyword = text
            self._needle = text.casefold()
            self._seconds = duration
            self._entries = []
            self._seen = set()
            self._winner = None
            self._winners = []
            if duration:
                self._deadline = time.monotonic() + duration
                timer = threading.Timer(duration, self._expire)
                timer.daemon = True
                self._timer = timer
                timer.start()
            return self._status_locked()

    def stop(self) -> dict:
        with self._lock:
            if self._running:
                self._close_locked()
            return self._status_locked()

    def redraw(self) -> dict:
        """Pick the next winner from whoever is still in the pool."""
        with self._lock:
            if not self._closed:
                raise ValueError("Redraw a winner after the drawing is closed.")
            if not self._entries:
                raise ValueError("There is nobody else to draw.")
            self._draw_locked()
            return self._status_locked()

    def observe(self, lines: list[dict]) -> None:
        """Count lines that are exactly the keyword. Same person, same platform, once."""
        if not lines:
            return
        with self._lock:
            self._expire_locked()
            if not self._running:
                return
            for line in lines:
                if not isinstance(line, dict):
                    continue
                platform = str(line.get("platform") or "")
                if platform not in _LABELS:
                    continue
                text = " ".join(str(line.get("text") or "").split()).casefold()
                if text != self._needle:
                    continue
                author = " ".join(str(line.get("author") or "").split()) or "Someone"
                token = platform + "\n" + author.casefold()
                if token in self._seen or len(self._entries) >= self._limit:
                    continue
                self._seen.add(token)
                self._entries.append({"platform": platform, "author": author})

    def status(self) -> dict:
        with self._lock:
            self._expire_locked()
            return self._status_locked()

    def _expire(self) -> None:
        with self._lock:
            self._expire_locked()

    def _expire_locked(self) -> None:
        if not self._running or self._deadline is None:
            return
        if time.monotonic() < self._deadline:
            return
        self._close_locked()

    def _close_locked(self) -> None:
        self._running = False
        self._cancel_timer_locked()
        self._closed = True
        if self._entries:
            self._draw_locked()
        else:
            self._winner = None

    def _draw_locked(self) -> None:
        picked = self.choose(self._entries)
        winner = {"platform": picked["platform"], "author": picked["author"]}
        self._winner = winner
        self._winners.append(dict(winner))
        self._entries = [
            entry for entry in self._entries
            if entry["platform"] != winner["platform"] or entry["author"] != winner["author"]
        ]
        self._seen.discard(winner["platform"] + "\n" + winner["author"].casefold())

    def _cancel_timer_locked(self) -> None:
        timer = self._timer
        self._timer = None
        self._deadline = None
        if timer is not None:
            timer.cancel()

    def _status_locked(self) -> dict:
        counts = {name: 0 for name in _PLATFORMS}
        for entry in self._entries:
            counts[entry["platform"]] += 1
        remaining = None
        if self._running and self._deadline is not None:
            remaining = max(0, int(self._deadline - time.monotonic() + 0.999))
        winner = dict(self._winner) if self._winner else None
        return {
            "running": self._running,
            "closed": self._closed,
            "keyword": self._keyword,
            "seconds": self._seconds,
            "remaining": remaining,
            "entries": len(self._entries),
            "platforms": counts,
            "winner": winner,
            "winners": [dict(item) for item in self._winners],
            "announce": keyword_announce(self._keyword),
            "winner_announce": winner_announce(winner) if self._closed else "",
        }


def _seconds(value: object) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise ValueError("Giveaway timer must be a whole number of seconds, up to 24 hours.")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("Giveaway timer must be a whole number of seconds, up to 24 hours.")
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Giveaway timer must be a whole number of seconds, up to 24 hours.") from exc
    if seconds < 0 or seconds > _MAX_SECONDS:
        raise ValueError("Giveaway timer must be a whole number of seconds, up to 24 hours.")
    return seconds
