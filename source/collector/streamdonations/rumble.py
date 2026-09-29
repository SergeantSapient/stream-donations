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
    def __init__(self, store: Store, transport: Transport | None = None):
        self.store = store
        self.transport = transport or Transport()
        self._next_poll = 0.0
        self._chat_target = ""
        self._chat_stop = threading.Event()
        self._chat_thread: threading.Thread | None = None
        self._sse_response = None
        self._sse_lock = threading.Lock()
        self._chat_detail = ""
        self._chat_error = ""

    def configured(self) -> bool:
        return bool(self.store.get_setting("rumble_api_url") or self.store.get_setting("rumble_watch"))

    def interval(self) -> float:
        raw = self.store.get_setting("rumble_poll_seconds", "8")
        try:
            seconds = int(raw)
        except ValueError:
            seconds = 8
        return float(min(120, max(5, seconds)))

    def tick(self, force: bool = False) -> None:
        self._ensure_chat()
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

    def _ensure_chat(self) -> None:
        target = self.store.get_setting("rumble_watch")
        if target == self._chat_target and (not target or (self._chat_thread and self._chat_thread.is_alive())):
            return
        self._chat_stop.set()
        with self._sse_lock:
            response = self._sse_response
            self._sse_response = None
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
        self._chat_stop = threading.Event()
        self._chat_target = target
        self._chat_detail = ""
        self._chat_error = ""
        if not target:
            return
        stop = self._chat_stop
        self._chat_thread = threading.Thread(
            target=self._chat_loop, args=(target, stop), name="rumble-chat", daemon=True
        )
        self._chat_thread.start()

    def _chat_loop(self, target: str, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                chat_id = self.resolve_chat_id(target)
                self._read_chat(chat_id, stop)
                if stop.is_set():
                    return
                self._chat_error = ""
                stop.wait(3)
            except TimeoutError:
                continue
            except Exception as exc:
                self._chat_error = redact(str(exc))
                self._chat_detail = ""
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
            self._chat_error = ""
            self._chat_detail = (
                f"Public chat {chat_id} is connected. "
                "This does not require owning the channel. Dollar amounts for subs still need the owner API."
            )
            self.store.set_status("rumble", "ok", self._chat_detail)
            while not stop.is_set():
                line = response.readline()
                if not line:
                    return
                self._consume_sse_line(line)
        finally:
            with self._sse_lock:
                if self._sse_response is response:
                    self._sse_response = None
            try:
                response.close()
            except OSError:
                pass

    def _consume_sse_line(self, line: bytes) -> None:
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
        donations = donations_from_chat_event(payload)
        inserted = self.store.add_many(donations)
        if inserted:
            self.store.log("rumble", "info", f"Stored {inserted} event(s) from the public Rumble chat.")
