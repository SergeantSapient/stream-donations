"""Chat commands: !total and !top.

A viewer types !total or !top as the first word of a public chat line. The
reply goes back into that same chat as the signed-in account: YouTube and Kick
through their chat APIs, Rumble through the desktop window that types into the
signed-in Rumble chat. Commands are off until a ledger turns them on for a
platform.

One cooldown covers both commands in one chat, across every ledger reading it.
A reply attempt starts two minutes in which every command line in that chat is
ignored. Nothing is queued for later. The cooldown is kept in memory only.

Lines from before this process started listening, lines older than a minute,
and lines without a platform time in the first batch after a (re)connect are
history and never answer. A message is considered once, even when two ledgers,
or a reconnect, see it again. The signed-in account's own lines and the app's
own recent replies are ignored.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque

from .chat_feed import clean_chat_text
from .fx import cached_usd_rates
from .goals import donor_bar_line, donor_leaderboard, load_tiers, running_usd, stream_chat_line
from .util import display_donor, parse_datetime

COMMANDS = {"!total": "total", "!top": "top"}
COOLDOWN_SECONDS = 120
MAX_AGE_SECONDS = 60
# YouTube: 200 characters per message (support.google.com/youtube/answer/15270973).
# Kick: 500 characters (docs.kick.com/apis/chat). Rumble publishes no limit;
# 200 is a cautious guess.
LIMITS = {"youtube": 200, "kick": 500, "rumble": 200}
FIELDS = {
    "youtube": "youtube_chat_commands",
    "rumble": "rumble_chat_commands",
    "kick": "kick_chat_commands",
}
LABELS = {"youtube": "YouTube", "rumble": "Rumble", "kick": "Kick"}
# Placeholder or merged names. Publishing them as a "top donor" says nothing.
_UNNAMED = {"", "—", "-", "someone", "anonymous"}
_SEEN_LIMIT = 5000
_REPLY_MEMORY_SECONDS = 600
# The desktop window asks for Rumble replies every few seconds. Without a
# recent ask, Rumble commands are skipped and do not start the cooldown.
RUMBLE_WINDOW_SECONDS = 20
RUMBLE_ITEM_SECONDS = 60
_PUNCTUATION = ".,!?;:"


def parse_command(text: object) -> str:
    """'total' or 'top' when the first word is the command. Case does not matter."""
    words = str(text or "").split()
    if not words:
        return ""
    first = words[0].casefold()
    if first in COMMANDS:
        return COMMANDS[first]
    bare = first.rstrip(_PUNCTUATION)
    if bare.startswith("!") and bare in COMMANDS:
        return COMMANDS[bare]
    return ""


def trim_reply(text: str, limit: int) -> str:
    """Fit one chat message. A cut ends on a word and an ellipsis."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space].rstrip(" ,;:")
    return cut + "…"


def _named(value: object) -> str:
    name = display_donor(value)
    return "" if name.casefold() in _UNNAMED else name


def mention(value: object) -> str:
    """@name for a real name. Empty for anonymous or unnamed donors."""
    name = _named(value).lstrip("@").strip()
    return "@" + name if name else ""


def _short(name: str, cap: int) -> str:
    return name if len(name) <= cap else name[: cap - 1].rstrip() + "…"


def total_reply(tab: dict, rows: list[dict], limit: int) -> str:
    """The ledger's stream-total chat line. The extra note goes first when it does not fit."""
    total = running_usd(rows)
    tiers = load_tiers(tab.get("goal_tiers"))
    enabled = str(tab.get("tiers_enabled") or "") == "1"
    extra = str(tab.get("chat_extra") or "")
    line = stream_chat_line(total, enabled=enabled, tiers=tiers, extra=extra)
    if len(line) > limit and extra.strip():
        line = stream_chat_line(total, enabled=enabled, tiers=tiers, extra="")
    return trim_reply(line, limit)


def top_reply(rows: list[dict], limit: int) -> str:
    """Top three named donors, without @. Anonymous and unnamed rows are skipped."""
    board = donor_leaderboard(rows, limit=max(1, len(rows)))["rows"]
    named = []
    for row in board:
        name = _named(row.get("donor"))
        if name:
            named.append(dict(row, donor=name))
        if len(named) == 3:
            break
    line = donor_bar_line(named, mentions=False)
    for cap in (48, 32, 20, 12):
        if len(line) <= limit:
            break
        line = donor_bar_line([dict(row, donor=_short(row["donor"], cap)) for row in named], mentions=False)
    return trim_reply(line, limit)


def top_donors_hidden(tab: dict) -> bool:
    return str(tab.get("show_top_donors", "1") or "1") == "0"


def ledger_rows(store, tab: dict) -> list[dict]:
    """The rows a !total or !top reply counts, as the banner counts them."""
    return store.command_rows(tab["id"], cached_usd_rates(store))


def blurb(command: str, tab: dict, rows: list[dict], limit: int) -> str:
    """The !total or !top line for one ledger, fitted to limit characters.

    BLUE's {TOTALLINE} and {TOPLINE} use this too. The top line is empty when
    the ledger hides its top donors.
    """
    if command == "top":
        if top_donors_hidden(tab):
            return ""
        return top_reply(rows, limit)
    return total_reply(tab, rows, limit)


class ChatCommands:
    def __init__(self, service, *, clock=time.monotonic, wall=time.time, run=None):
        self.service = service
        self._clock = clock
        self._wall = wall
        self._run = run or self._spawn
        self._lock = threading.Lock()
        self._started = wall()
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._replies: dict[tuple[str, str], list[tuple[str, float]]] = {}
        self._rumble: list[dict] = []
        self._rumble_seen_at: float | None = None

    # Lines in -------------------------------------------------------------

    def listen(self, platform: str, rows: list[dict], *, stream_id: str | None = None,
               chat: str = "", fresh: bool = False) -> None:
        """Called by a chat reader. A failure here must not stop that reader."""
        try:
            self._observe(platform, rows, stream_id, chat, fresh)
        except Exception:
            return

    def _observe(self, platform, rows, stream_id, chat, fresh) -> None:
        field = FIELDS.get(platform)
        if field is None or not rows:
            return
        tab = self._tab(stream_id)
        if tab is None or str(tab.get("collection_paused") or "") != "0":
            return
        if str(tab.get(field) or "") != "1":
            return
        chat_id = str(chat or "").strip().casefold()
        key = (platform, chat_id or "ledger:" + tab["id"])
        own = self._own_names(platform)
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = clean_chat_text(row.get("text"), 500)
            command = parse_command(text)
            if not command:
                continue
            if not self._first_sighting(platform, row):
                continue
            if self._is_own_reply(key, text):
                continue
            author = clean_chat_text(row.get("author"), 80).lstrip("@").casefold()
            if author and author in own:
                continue
            if not self._is_current(row.get("time"), fresh):
                continue
            if command == "top" and top_donors_hidden(tab):
                continue
            if not self._can_send(platform):
                continue
            now = self._clock()
            with self._lock:
                if now < self._cooldowns.get(key, 0.0):
                    continue
                self._cooldowns[key] = now + COOLDOWN_SECONDS
            tab_id = tab["id"]
            self._run(lambda c=command, p=platform, t=tab_id, k=key: self._reply(c, p, t, k))

    def _first_sighting(self, platform: str, row: dict) -> bool:
        source = str(row.get("key") or "").strip()
        if not source:
            return True
        token = platform + ":" + source
        with self._lock:
            if token in self._seen:
                return False
            self._seen.add(token)
            self._seen_order.append(token)
            while len(self._seen_order) > _SEEN_LIMIT:
                self._seen.discard(self._seen_order.popleft())
        return True

    def _is_current(self, value: object, fresh: bool) -> bool:
        moment = parse_datetime(value) if value else None
        if moment is None:
            # No platform time. The first batch after a connect is history.
            return not fresh
        stamp = moment.timestamp()
        return stamp >= self._started and stamp >= self._wall() - MAX_AGE_SECONDS

    def _is_own_reply(self, key: tuple[str, str], text: str) -> bool:
        now = self._clock()
        wanted = text.casefold()
        with self._lock:
            kept = [(item, until) for item, until in self._replies.get(key, []) if until > now]
            self._replies[key] = kept
            return any(item == wanted for item, _until in kept)

    def _own_names(self, platform: str) -> set[str]:
        store = self.service.store
        setting = {"youtube": "youtube_sender_name", "kick": "kick_sender_name"}.get(platform)
        if not setting:
            return set()
        name = " ".join(str(store.get_setting(setting) or "").split()).lstrip("@").casefold()
        return {name} if name else set()

    def can_send(self, platform: str) -> bool:
        """Whether this process can post into that platform's chat right now."""
        try:
            return self._can_send(platform)
        except Exception:
            return False

    def _can_send(self, platform: str) -> bool:
        if platform == "youtube":
            youtube = self.service.youtube
            return bool(youtube.can_send()) and not youtube._quota.paused()
        if platform == "kick":
            return bool(self.service.kick.can_send())
        if platform == "rumble":
            return self.rumble_ready()
        return False

    def _tab(self, stream_id: str | None) -> dict | None:
        store = self.service.store
        wanted = stream_id or store._active_stream_id()
        for tab in store.ledger_snapshots():
            if tab["id"] == wanted:
                return tab
        return None

    # Replies out ----------------------------------------------------------

    def _spawn(self, job) -> None:
        threading.Thread(target=job, name="chat-command", daemon=True).start()

    def reply_text(self, command: str, platform: str, tab: dict) -> str:
        rows = ledger_rows(self.service.store, tab)
        limit = LIMITS[platform]
        if command == "top":
            return top_reply(rows, limit)
        return total_reply(tab, rows, limit)

    def _reply(self, command: str, platform: str, stream_id: str, key: tuple[str, str]) -> None:
        store = self.service.store
        try:
            tab = self._tab(stream_id)
            if tab is None or str(tab.get("collection_paused") or "") != "0":
                return
            text = self.reply_text(command, platform, tab)
            with self._lock:
                self._replies.setdefault(key, []).append(
                    (text.casefold(), self._clock() + _REPLY_MEMORY_SECONDS)
                )
            if platform == "rumble":
                with self._lock:
                    self._rumble.append({
                        "id": uuid.uuid4().hex,
                        "command": "!" + command,
                        "text": text,
                        "stream_id": stream_id,
                        "queued": self._clock(),
                    })
                return
            result = self.service._send_chat_one(platform, text, tab)
            if result.get("ok"):
                store.log(platform, "info", f"Answered !{command} in {LABELS[platform]} chat.")
        except Exception:
            store.log(platform, "error", f"!{command} could not be answered.")

    # The desktop window delivers Rumble replies ----------------------------

    def rumble_ready(self) -> bool:
        seen = self._rumble_seen_at
        return seen is not None and self._clock() - seen <= RUMBLE_WINDOW_SECONDS

    def claim_rumble(self, ready: bool) -> list[dict]:
        """Hand waiting Rumble replies to a window that can type them."""
        now = self._clock()
        expired: list[dict] = []
        with self._lock:
            if ready:
                self._rumble_seen_at = now
            kept = []
            for item in self._rumble:
                (kept if now - item["queued"] <= RUMBLE_ITEM_SECONDS else expired).append(item)
            if ready:
                handed, self._rumble = kept, []
            else:
                handed, self._rumble = [], kept
        for item in expired:
            self.service.store.log("rumble", "info", f"{item['command']} was not answered. The window did not take it.")
        return [{"id": item["id"], "text": item["text"], "stream_id": item["stream_id"]} for item in handed]

    def record_rumble(self, payload: object) -> None:
        if not isinstance(payload, dict) or payload.get("ok") is True:
            return
        reason = clean_chat_text(payload.get("reason"), 300) or "Rumble chat was not sent."
        self.service.store.log("rumble", "info", "Chat was not sent. " + reason)

    def status(self) -> dict:
        return {"rumble_window": self.rumble_ready()}
