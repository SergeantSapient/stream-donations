"""YouTube Data API quota for the signed-in channel.

Google's default project quota is 10,000 units per day for every method
except search.list and videos.insert, which have their own buckets. The day
resets at midnight Pacific Time. Each call is counted at Google's published
cost before it is sent: 1 unit for channels.list, videos.list,
superChatEvents.list, and liveBroadcasts.list, 5 units for
liveChatMessages.list, and 20 units for liveChatMessages.insert.
A quotaExceeded response, or a local count that reaches 10,000, pauses
signed-in calls until that reset. Public innertube chat is not this API and
is not counted.

https://developers.google.com/youtube/v3/determine_quota_cost
https://developers.google.com/youtube/v3/live/docs/liveChatMessages/list
https://developers.google.com/youtube/v3/live/docs/liveChatMessages/insert
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .store import Store

PACIFIC = ZoneInfo("America/Los_Angeles")
DAILY_LIMIT = 10000
# Five minutes is 288 history checks a day. Shorter than that spends the
# daily budget on a list that is only a 30-day backfill.
MIN_POLL_SECONDS = 300
MAX_POLL_SECONDS = 3600
DEFAULT_POLL_SECONDS = 900
CALL_COST = 1
# Units per call, from the quota calculator. A path that is not listed is
# refused, so a new call cannot be counted at the wrong price by default.
LIST_COSTS = {
    "/channels": 1,
    "/videos": 1,
    "/superChatEvents": 1,
    "/liveBroadcasts": 1,
    "/liveChat/messages": 5,
}
INSERT_COST = 20
# liveChatMessages.list is 5 units. Every 30 seconds is 600 units an hour for
# one live chat. YouTube's pollingIntervalMillis is used when it is longer.
LIVE_CHAT_MIN_SECONDS = 30

QUOTA_LOG = (
    "YouTube Data API quota is used up. Signed-in requests are paused until "
    "midnight Pacific Time, when the daily quota resets. Public live chat is unaffected."
)
BUDGET_LOG = (
    "Paused signed-in YouTube requests at the 10,000 unit daily quota. "
    "They resume at midnight Pacific Time. Public live chat is unaffected."
)


def list_cost(path: str) -> int:
    """Units for one GET on this API path. Unknown paths raise ValueError."""
    try:
        return LIST_COSTS[path]
    except KeyError:
        raise ValueError(f"No quota cost is known for {path}") from None


class QuotaPaused(Exception):
    """Signed-in YouTube calls are waiting for the Pacific-time quota reset."""


def clamp_poll_seconds(value: object, default: int = DEFAULT_POLL_SECONDS) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return min(MAX_POLL_SECONDS, max(MIN_POLL_SECONDS, number))


def pacific_day(now: datetime | None = None) -> str:
    return _pacific(now).date().isoformat()


def next_pacific_midnight(now: datetime | None = None) -> float:
    current = _pacific(now)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return (start + timedelta(days=1)).timestamp()


def _pacific(now: datetime | None) -> datetime:
    current = now or datetime.now(PACIFIC)
    if current.tzinfo is None:
        return current.replace(tzinfo=PACIFIC)
    return current.astimezone(PACIFIC)


class YouTubeQuota:
    def __init__(self, store: Store):
        self.store = store

    def paused(self, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        resume = self.store.get_setting("youtube_quota_resume_at")
        if resume:
            try:
                if moment < float(resume):
                    return True
            except ValueError:
                pass
        return self.units_used() >= DAILY_LIMIT

    def units_used(self, now: datetime | None = None) -> int:
        if self.store.get_setting("youtube_quota_day") != pacific_day(now):
            return 0
        raw = self.store.get_setting("youtube_quota_units") or "0"
        try:
            return max(0, int(raw))
        except ValueError:
            return 0

    def reserve(self, cost: int = CALL_COST) -> None:
        # bool is an int. A mistaken True would add 1 and hide the caller's cost.
        if isinstance(cost, bool) or not isinstance(cost, int) or cost < 1:
            raise ValueError("quota cost must be at least 1")
        if self.paused():
            raise QuotaPaused(self.status_detail())
        used = self.units_used()
        if used + cost > DAILY_LIMIT:
            self._block(BUDGET_LOG)
            raise QuotaPaused(self.status_detail())
        day = pacific_day()
        if self.store.get_setting("youtube_quota_day") != day:
            used = 0
        self.store.set_setting("youtube_quota_day", day)
        self.store.set_setting("youtube_quota_units", str(used + cost))

    def note_exceeded(self) -> None:
        self._block(QUOTA_LOG)

    def status_detail(self) -> str:
        raw = self.store.get_setting("youtube_quota_resume_at")
        try:
            resume = float(raw)
        except (TypeError, ValueError):
            resume = next_pacific_midnight()
        when = datetime.fromtimestamp(resume, PACIFIC).strftime("%Y-%m-%d %H:%M %Z")
        return (
            "YouTube Data API quota is used for today. "
            f"Signed-in requests are paused until {when}. "
            "Public live chat does not use this quota."
        )

    def _block(self, message: str) -> None:
        stamp = f"{next_pacific_midnight():.0f}"
        already = self.store.get_setting("youtube_quota_resume_at")
        self.store.set_setting("youtube_quota_resume_at", stamp)
        if already == stamp:
            return
        self.store.log("youtube", "info", message)
