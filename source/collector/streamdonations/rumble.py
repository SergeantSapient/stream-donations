"""Rumble donation collectors.

The account Live Stream API (https://rumble.com/account/livestream-api) only
works for a channel you own. The URL contains that channel's key.

Anyone watching a public stream can read rants from the same chat Rumble's
player uses:

    GET https://web7.rumble.com/chat/api/chat/{video_id}/stream

`video_id` is the numeric id from the watch page (`data-video-id`). An embed
id such as v7ds6x0 is that number written in base 36 with a leading v.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .chat_feed import chat_time, offer_chat, remember_chat
from .normalize import Donation, rumble_donations
from .store import Store
from .transport import RUMBLE_UA, Transport, api_error_message
from .util import ApiError, amount_from_cents, as_int, host_allowed, redact, stable_key, to_utc_iso

RUMBLE_HOSTS = {"rumble.com"}
CHAT_API_BASE = "https://web7.rumble.com/chat/api"
_VIDEO_ID_RE = re.compile(r'data-video-id="(\d+)"')
_EMBED_RE = re.compile(r"""["']video["']\s*:\s*["']v([0-9a-z]+)["']""")
_EMBED_URL_RE = re.compile(r"rumble\.com/embed/(?:[0-9a-z]+\.)?v([0-9a-z]+)")
_CHAT_ID_IN_URL_RE = re.compile(r"/chat/(?:api/chat|popup)/([0-9a-z]+)")


def validate_rumble_api_url(url: str) -> str:
    text = (url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise ValueError("Rumble API URL must start with https://")
    if parsed.username or parsed.password:
        raise ValueError("Rumble API URL must not include a username or password")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host_allowed(host, RUMBLE_HOSTS):
        raise ValueError("Rumble API URL host must be rumble.com")
    if parsed.port not in (None, 443):
        raise ValueError("Rumble API URL must use port 443")
    if not parsed.path.startswith("/-livestream-api/"):
        raise ValueError(
            "That is not a Live Stream API URL. On rumble.com/account/livestream-api, copy the generated URL."
        )
    return text


def validate_rumble_watch(value: str) -> str:
    """A public video URL, embed id, or numeric chat id. No owner key."""
    text = (value or "").strip()
    if not text:
        raise ValueError("Paste a Rumble video URL or numeric chat id.")
    if re.fullmatch(r"\d{3,20}", text):
        return text
    if re.fullmatch(r"v[0-9a-z]{4,16}", text):
        return text
    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise ValueError("A Rumble watch link must start with https://")
    if parsed.username or parsed.password:
        raise ValueError("Rumble watch link must not include a username or password")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host_allowed(host, RUMBLE_HOSTS):
        raise ValueError("Rumble watch link host must be rumble.com")
    if parsed.port not in (None, 443):
        raise ValueError("Rumble watch link must use port 443")
    return text


def embed_to_chat_id(embed_body: str) -> str:
    """v7ds6x0 and 7ds6x0 are the numeric chat id in base 36."""
    token = embed_body.strip().lower()
    if token.startswith("v"):
        token = token[1:]
    if not re.fullmatch(r"[0-9a-z]{4,16}", token):
        raise ValueError("That is not a Rumble embed id.")
    return str(int(token, 36))


_RUMBLE_CHANNEL_TABS = {"about", "clips", "live", "livestreams", "shorts", "videos"}


def validate_rumble_channel(value: str) -> str:
    """A channel page. A video URL, embed id, or the owner API URL is not one."""
    text = (value or "").strip()
    if re.fullmatch(r"\d{3,20}", text) or re.fullmatch(r"v[0-9a-z]{4,16}", text):
        raise ValueError(
            "That is a video id. Paste a channel URL, such as https://rumble.com/c/channel, "
            "or turn off follow live and use the video field."
        )
    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise ValueError("A Rumble channel link must start with https://")
    if parsed.username or parsed.password:
        raise ValueError("Rumble channel link must not include a username or password")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host_allowed(host, RUMBLE_HOSTS):
        raise ValueError("Rumble channel link host must be rumble.com")
    if parsed.port not in (None, 443):
        raise ValueError("Rumble channel link must use port 443")
    parts = [part for part in parsed.path.split("/") if part]
    if parts and (parts[0].startswith("-") or parsed.path.startswith("/-livestream-api/")):
        raise ValueError(
            "The owner API URL is not a channel address. Paste a channel URL such as https://rumble.com/c/channel."
        )
    if len(parts) < 2 or parts[0] not in {"c", "user"}:
        raise ValueError("Paste a Rumble channel URL, such as https://rumble.com/c/channel.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", parts[1]):
        raise ValueError("That Rumble channel address is not valid.")
    if len(parts) > 3 or (len(parts) == 3 and parts[2].lower() not in _RUMBLE_CHANNEL_TABS):
        raise ValueError("Paste the channel URL, not a video. A video URL stays in the video field.")
    return text


def rumble_channel_page_url(value: str) -> str:
    """The channel root. The livestreams tab is filled in by script and lists no video."""
    text = validate_rumble_channel(value)
    parts = [part for part in urlparse(text).path.split("/") if part]
    if parts[-1].lower() in _RUMBLE_CHANNEL_TABS:
        parts = parts[:-1]
    return "https://rumble.com/" + "/".join(parts)


def live_chat_id_from_channel_html(html: str) -> str | None:
    """Numeric chat id of the one broadcast on now, or the one upcoming chat that is open.

    A channel page's other videos are not the chat to read. The stylesheet
    defines the live class for every page, so only a badge on a video card counts.
    The channel grid is also a JSON list: live is on now, and live_placeholder is
    a scheduled broadcast. A finished video is neither.
    """
    live = _badge_live_ids(html)
    grid_live, upcoming = _grid_broadcast_ids(html)
    for video_id in grid_live:
        if video_id not in live:
            live.append(video_id)
    if len(live) > 1:
        raise ValueError(
            "That Rumble channel lists more than one live video. Paste the live video's URL or turn off follow live."
        )
    if len(live) == 1:
        return live[0]
    upcoming = list(dict.fromkeys(upcoming))
    if len(upcoming) > 1:
        raise ValueError(
            "That Rumble channel lists more than one upcoming video. "
            "Paste that video's URL or turn off follow live."
        )
    if len(upcoming) == 1:
        return upcoming[0]
    return None


def _badge_live_ids(html: str) -> list[str]:
    found: list[str] = []
    for part in re.split(r'<div\s+class="videostream thumbnail', html)[1:]:
        head, _, rest = part.partition(">")
        blob = head + ">" + rest[:4000]
        video = re.search(r'data-video-id="(\d+)"', head) or re.search(r'data-video-id="(\d+)"', blob[:1500])
        if video and 'videostream__status--live"' in blob and video.group(1) not in found:
            found.append(video.group(1))
    return found


def _grid_broadcast_ids(html: str) -> tuple[list[str], list[str]]:
    """Live ids, then scheduled ids, from the channel's embedded video grid."""
    live: list[str] = []
    upcoming: list[str] = []
    for match in re.finditer(r'<script type="application/json">\s*(\{.*?\})\s*</script>', html, re.S):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            video_id = item.get("id")
            if isinstance(video_id, bool) or not isinstance(video_id, (int, str)):
                continue
            text = str(video_id)
            if not text.isdigit():
                continue
            if item.get("live") is True:
                live.append(text)
            elif item.get("live_placeholder") is True:
                upcoming.append(text)
    return live, upcoming


def chat_id_from_html(html: str) -> str:
    """Prefer the player embed. A directory page has many videos and no single chat."""
    embeds = list(dict.fromkeys(_EMBED_RE.findall(html)))
    if len(embeds) == 1:
        return embed_to_chat_id(embeds[0])
    page_embeds = list(dict.fromkeys(_EMBED_URL_RE.findall(html)))
    if len(page_embeds) == 1:
        return embed_to_chat_id(page_embeds[0])
    video_ids = list(dict.fromkeys(_VIDEO_ID_RE.findall(html)))
    if len(video_ids) == 1:
        return video_ids[0]
    if len(video_ids) > 1:
        raise ValueError(
            "That Rumble page lists more than one video. Paste the live video's URL or its numeric chat id."
        )
    raise ValueError("Could not find a Rumble chat id on that page.")


def chat_lines_from_chat_event(event: dict) -> list[dict]:
    """Typed public-chat lines, including a rant's text. Notices without text are skipped."""
    if not isinstance(event, dict):
        return []
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    if not isinstance(data, dict):
        return []
    users = {
        str(person.get("id")): str(person.get("username") or "")
        for person in data.get("users") or []
        if isinstance(person, dict) and person.get("id") is not None
    }
    messages = data.get("messages")
    if not isinstance(messages, list):
        messages = [data] if _looks_like_chat_message(data) else []
    found: list[dict] = []
    seen: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        text = " ".join(str(message.get("text") or "").split())
        if not text:
            continue
        author, _author_id = _chat_author(message, users)
        key = str(message.get("id") or "")
        if not key:
            key = stable_key("rumble:chat-line:", [author, message.get("time"), text])
        if key in seen:
            continue
        seen.add(key)
        found.append({
            "key": key,
            "author": author,
            "text": text,
            "time": chat_time(message.get("time")),
        })
    return found


def donations_from_chat_event(event: dict) -> list[Donation]:
    """Rants, gifted subs, and new subscribers from one public chat SSE payload."""
    if not isinstance(event, dict):
        return []
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    if not isinstance(data, dict):
        return []
    users = {
        str(person.get("id")): str(person.get("username") or "")
        for person in data.get("users") or []
        if isinstance(person, dict) and person.get("id") is not None
    }
    channel = ""
    channels = data.get("channels") or []
    if isinstance(channels, list) and channels and isinstance(channels[0], dict):
        channel = str(channels[0].get("username") or "")
    messages = data.get("messages")
    if not isinstance(messages, list):
        if _looks_like_chat_message(data):
            messages = [data]
        else:
            messages = []
    found: dict[str, Donation] = {}
    for message in messages:
        donation = _chat_message_donation(message, users, channel)
        if donation is not None:
            found[donation.dedupe_key] = donation
    return list(found.values())


def _looks_like_chat_message(node: dict) -> bool:
    return bool(node.get("id")) and (
        "text" in node
        or "rant" in node
        or "notification" in node
        or "gift_purchase_notification" in node
    )


def _chat_author(message: dict, users: dict[str, str]) -> tuple[str, str]:
    user_id = str(message.get("user_id") or "")
    name = users.get(user_id) or ""
    if not name and user_id:
        name = f"user {user_id}"
    return name, user_id


def _chat_message_donation(message: object, users: dict[str, str], channel: str) -> Donation | None:
    if not isinstance(message, dict):
        return None
    message_id = str(message.get("id") or "")
    author, author_id = _chat_author(message, users)
    created = to_utc_iso(message.get("time"))
    text = str(message.get("text") or "")
    rant = message.get("rant") if isinstance(message.get("rant"), dict) else None
    if message.get("type") == "rant" or rant:
        cents = None if rant is None else rant.get("price_cents")
        micros, amount = amount_from_cents(cents, None)
        dedupe = f"rumble:chat:{message_id}" if message_id else stable_key(
            "rumble:chat:", [author, message.get("time"), cents, text]
        )
        return Donation(
            dedupe_key=dedupe,
            platform="rumble",
            kind="rant",
            external_id=message_id,
            channel=channel,
            author_name=author,
            author_id=author_id,
            message=text,
            amount_value=amount,
            amount_micros=micros,
            currency="USD" if amount is not None else None,
            created_at=created,
            raw=message,
        )
    gift = message.get("gift_purchase_notification")
    if isinstance(gift, dict):
        total = as_int(gift.get("total_gifts")) or 1
        noun = "subscription" if total == 1 else "subscriptions"
        gift_type = str(gift.get("gift_type") or "")
        body = f"Gifted {total} {noun}"
        if gift_type:
            body += f" ({gift_type})"
        dedupe = f"rumble:chat:{message_id}" if message_id else stable_key(
            "rumble:chat:", [author, message.get("time"), total, gift_type]
        )
        return Donation(
            dedupe_key=dedupe,
            platform="rumble",
            kind="gifted_subscriptions",
            external_id=message_id,
            channel=channel,
            author_name=author,
            author_id=author_id,
            message=body,
            amount_value=None,
            amount_micros=None,
            currency=None,
            created_at=created,
            raw=message,
        )
    note = message.get("notification")
    if isinstance(note, dict):
        badge = str(note.get("badge") or "")
        if badge not in {"recurring_subscription", "subscription"}:
            return None
        dedupe = f"rumble:chat:{message_id}" if message_id else stable_key(
            "rumble:chat:", [author, message.get("time"), badge, note.get("text")]
        )
        return Donation(
            dedupe_key=dedupe,
            platform="rumble",
            kind="subscription",
            external_id=message_id,
            channel=channel,
            author_name=author,
            author_id=author_id,
            message=str(note.get("text") or "Subscribed"),
            amount_value=None,
            amount_micros=None,
            currency=None,
            created_at=created,
            raw=message,
        )
    return None


class RumbleClient:
    def __init__(self, store: Store, transport: Transport | None = None, chat_feed=None):
        self.store = store
        self.transport = transport or Transport()
        self._chat_feed = chat_feed
        self._chat_for = ""
        self._next_poll = 0.0
        self._chat_target = ""
        self._chat_stop = threading.Event()
        self._chat_thread: threading.Thread | None = None
        self._sse_response = None
        self._sse_lock = threading.Lock()
        self._chat_detail = ""
        self._chat_error = ""
        self._chat_level = ""

    def configured(self) -> bool:
        if self.store.get_setting("rumble_api_url"):
            return True
        if self.store.get_setting("rumble_follow_live") == "1":
            return bool(self.store.get_setting("rumble_channel"))
        return bool(self.store.get_setting("rumble_watch"))

    def interval(self) -> float:
        raw = self.store.get_setting("rumble_poll_seconds", "8")
        try:
            seconds = int(raw)
        except ValueError:
            seconds = 8
        return float(min(120, max(5, seconds)))

    def rest(self) -> None:
        """Stop the owner poll and the public chat. Saved URLs stay in settings."""
        self._chat_stop.set()
        self._close_sse()
        self._chat_target = ""
        self._chat_detail = ""
        self._chat_error = ""
        self._chat_level = ""
        thread = self._chat_thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2)

    def tick(self, force: bool = False, public_only: bool = False) -> None:
        if self.store.collection_paused():
            self.rest()
            return
        self._ensure_chat()
        if public_only:
            return
        follow = self.store.get_setting("rumble_follow_live") == "1"
        if follow and not self.store.get_setting("rumble_channel"):
            note = (
                "Paste a channel URL to follow the live stream. "
                "The video URL is kept and is not used while this is on."
            )
            self._chat_error = ""
            self._chat_level = "idle"
            self._chat_detail = note
            if not self.store.get_setting("rumble_api_url"):
                self.store.set_status("rumble", "idle", note)
                return
        if not self.configured():
            self.store.set_status(
                "rumble",
                "unconfigured",
                "Paste a public video URL, or the owner API URL from rumble.com/account/livestream-api.",
            )
            return
        if not self.store.get_setting("rumble_api_url"):
            if self._chat_error:
                self.store.set_status("rumble", "error", self._chat_error, error=self._chat_error)
            elif self._chat_level == "idle":
                self.store.set_status("rumble", "idle", self._chat_detail or "That channel is not live.")
            elif self._chat_detail:
                self.store.set_status("rumble", "ok", self._chat_detail)
            else:
                self.store.set_status("rumble", "idle", "Connecting to the public Rumble chat.")
            return
        now = time.monotonic()
        if not force and now < self._next_poll:
            return
        try:
            inserted = self.poll()
        except (ApiError, OSError, ValueError) as exc:
            message = redact(str(exc))
            if self._chat_detail:
                message = f"{message} Public chat: {self._chat_detail}"
            self.store.set_status("rumble", "error", message, error=message)
            self.store.log("rumble", "error", redact(str(exc)))
            self._next_poll = time.monotonic() + max(15.0, self.interval())
            return
        self._next_poll = time.monotonic() + self.interval()
        if inserted:
            self.store.log("rumble", "info", f"Stored {inserted} Rumble rant, sub, or gift event(s).")

    def poll(self) -> int:
        url = validate_rumble_api_url(self.store.get_setting("rumble_api_url"))
        result = self.transport.request(
            "GET",
            url,
            headers={"Accept": "application/json"},
            allow_hosts=RUMBLE_HOSTS,
            user_agent=RUMBLE_UA,
        )
        if result.status >= 400:
            raise ApiError(api_error_message(result), status=result.status)
        payload = result.json()
        if not isinstance(payload, dict):
            raise ApiError("Rumble API did not return an object")
        donations = rumble_donations(payload)
        inserted = self.store.add_many(donations)
        subscribers = payload.get("subscribers") or {}
        sub_count = subscribers.get("num_subscribers") if isinstance(subscribers, dict) else None
        livestreams = payload.get("livestreams") or []
        live_count = len(livestreams) if isinstance(livestreams, (list, dict)) else 0
        detail = f"Owner API poll ok. {live_count} livestream record(s)."
        if sub_count is not None:
            detail += f" Subscriber count reported by Rumble: {sub_count}."
        detail += " This key only works for a channel you own."
        if self._chat_detail:
            detail += " " + self._chat_detail
        if self._chat_error:
            detail += " Public chat: " + self._chat_error
            self.store.set_status("rumble", "error", detail, error=self._chat_error)
        else:
            self.store.set_status("rumble", "ok", detail)
        return inserted

    def resolve_chat_id(self, target: str) -> str:
        text = validate_rumble_watch(target)
        if re.fullmatch(r"\d{3,20}", text):
            return text
        if re.fullmatch(r"v[0-9a-z]{4,16}", text):
            return embed_to_chat_id(text)
        parsed = urlparse(text)
        match = _CHAT_ID_IN_URL_RE.search(parsed.path)
        if match:
            token = match.group(1)
            if token.isdigit():
                return token
            return embed_to_chat_id(token if token.startswith("v") else "v" + token)
        embed = _EMBED_URL_RE.search(parsed.path)
        if embed:
            return embed_to_chat_id(embed.group(1))
        page = self.transport.request(
            "GET",
            text,
            headers={"Accept": "text/html"},
            allow_hosts=RUMBLE_HOSTS,
            user_agent=RUMBLE_UA,
        )
        if page.status >= 400:
            raise ApiError(api_error_message(page), status=page.status)
        return chat_id_from_html(page.body.decode("utf-8", "replace"))

    def resolve_live_channel(self, channel: str) -> str | None:
        page_url = rumble_channel_page_url(channel)
        page = self.transport.request(
            "GET",
            page_url,
            headers={"Accept": "text/html"},
            allow_hosts=RUMBLE_HOSTS,
            user_agent=RUMBLE_UA,
        )
        if page.status >= 400:
            raise ApiError(api_error_message(page), status=page.status)
        return live_chat_id_from_channel_html(page.body.decode("utf-8", "replace"))

    def _chat_source(self) -> str:
        """Follow-live ignores a saved video URL until it is off."""
        if self.store.get_setting("rumble_follow_live") == "1":
            channel = self.store.get_setting("rumble_channel").strip()
            return f"live\n{channel}" if channel else ""
        watch = self.store.get_setting("rumble_watch").strip()
        return f"watch\n{watch}" if watch else ""

    def _ensure_chat(self) -> None:
        target = self._chat_source()
        self._chat_for = remember_chat(self._chat_feed, "rumble", self._chat_for, target)
        if target == self._chat_target and (not target or (self._chat_thread and self._chat_thread.is_alive())):
            return
        self._chat_stop.set()
        self._close_sse()
        self._chat_stop = threading.Event()
        self._chat_target = target
        self._chat_detail = ""
        self._chat_error = ""
        self._chat_level = ""
        if not target:
            return
        stop = self._chat_stop
        self._chat_thread = threading.Thread(
            target=self._chat_loop, args=(target, stop), name="rumble-chat", daemon=True
        )
        self._chat_thread.start()

    def _chat_loop(self, target: str, stop: threading.Event) -> None:
        mode, source = target.split("\n", 1)
        while not stop.is_set() and not self.store.collection_paused():
            try:
                if mode == "live":
                    chat_id = self.resolve_live_channel(source)
                    if stop.is_set() or self.store.collection_paused():
                        return
                    if not chat_id:
                        self._chat_error = ""
                        self._chat_level = "idle"
                        self._chat_detail = (
                            "That channel is not live. Chat waits until the channel page shows a live video "
                            "or an upcoming one whose chat is open."
                        )
                        self.store.set_status("rumble", "idle", self._chat_detail)
                        stop.wait(30)
                        continue
                else:
                    chat_id = self.resolve_chat_id(source)
                if stop.is_set() or self.store.collection_paused():
                    return
                self._read_chat(chat_id, stop)
                if stop.is_set() or self.store.collection_paused():
                    return
                self._chat_error = ""
                stop.wait(3)
            except TimeoutError:
                continue
            except Exception as exc:
                if stop.is_set() or self.store.collection_paused():
                    return
                self._chat_error = redact(str(exc))
                self._chat_detail = ""
                self._chat_level = "error"
                self.store.log("rumble", "error", self._chat_error)
                self.store.set_status("rumble", "error", self._chat_error, error=self._chat_error)
                stop.wait(15)

    def _read_chat(self, chat_id: str, stop: threading.Event) -> None:
        url = f"{CHAT_API_BASE}/chat/{chat_id}/stream"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": RUMBLE_UA, "Accept": "text/event-stream"},
        )
        try:
            response = urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise ApiError(f"Rumble chat returned HTTP {code} for video {chat_id}.", status=code) from exc
        with self._sse_lock:
            self._sse_response = response
        try:
            if response.status == 204:
                raise ApiError(f"Rumble has no live chat for video {chat_id} right now.")
            if response.status >= 400:
                raise ApiError(f"Rumble chat returned HTTP {response.status} for video {chat_id}.")
            if stop.is_set() or self.store.collection_paused():
                return
            self._chat_error = ""
            self._chat_level = "ok"
            self._chat_detail = (
                f"Public chat {chat_id} is connected. "
                "This does not require owning the channel. Dollar amounts for subs still need the owner API."
            )
            self.store.set_status("rumble", "ok", self._chat_detail)
            while not stop.is_set() and not self.store.collection_paused():
                line = response.readline()
                if stop.is_set() or self.store.collection_paused() or not line:
                    return
                self._consume_sse_line(line, stop)
        finally:
            with self._sse_lock:
                if self._sse_response is response:
                    self._sse_response = None
            try:
                response.close()
            except OSError:
                pass

    def _close_sse(self) -> None:
        with self._sse_lock:
            response = self._sse_response
            self._sse_response = None
        if response is not None:
            try:
                response.close()
            except OSError:
                pass

    def _consume_sse_line(self, line: bytes, stop: threading.Event) -> None:
        if stop.is_set() or self.store.collection_paused():
            return
        text = line.decode("utf-8", "replace").strip()
        if not text.startswith("data:"):
            return
        raw = text[5:].strip()
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        offer_chat(self._chat_feed, "rumble", chat_lines_from_chat_event(payload))
        donations = donations_from_chat_event(payload)
        inserted = self.store.add_many(donations)
        if inserted:
            self.store.log("rumble", "info", f"Stored {inserted} event(s) from the public Rumble chat.")
