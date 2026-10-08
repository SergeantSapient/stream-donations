"""Public Kick chat, for a channel the user does not own.

Owner webhooks only fire for the signed-in channel. Subscriptions, gifted
subscriptions, and Kicks that appear in chat are broadcast on Kick's public
Pusher socket. No OAuth and no webhook URL are required.

    GET https://kick.com/api/v2/channels/{slug}
    WSS wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679
        channels: chatrooms.{id}.v2, channel.{id}, channel_{id}
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import threading
import time
from urllib.parse import urlparse

from .chat_feed import chat_time, offer_chat, remember_chat
from .normalize import Donation
from .store import Store
from .transport import Transport, api_error_message
from .util import ApiError, as_int, redact, stable_key, to_utc_iso

KICK_HOSTS = {"kick.com"}
PUSHER_HOST = "ws-us2.pusher.com"
PUSHER_PATH = "/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0-rc2&flash=false"
KICK_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 StreamDonations/1.0"
)
_RESERVED = {"api", "settings", "dashboard", "browse", "categories", "following"}


def validate_kick_watch(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("Paste a Kick channel URL or slug.")
    slug = kick_slug(text)
    if not slug:
        raise ValueError("Paste a Kick channel URL, such as https://kick.com/channel, or the channel slug.")
    return text


def kick_slug(value: str) -> str:
    text = (value or "").strip()
    if text.isdigit():
        return text
    if not text.lower().startswith("http"):
        if _slug_ok(text):
            return text.lower()
        return ""
    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise ValueError("A Kick link must start with https://")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("That Kick link is not a channel URL.")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in KICK_HOSTS:
        raise ValueError("Kick link must be on kick.com")
    parts = [part for part in parsed.path.split("/") if part]
    if not parts or parts[0].lower() in _RESERVED or not _slug_ok(parts[0]):
        raise ValueError("Paste a Kick channel URL or slug.")
    return parts[0].lower()


def _slug_ok(value: str) -> bool:
    return bool(value) and all(ch.isalnum() or ch in "_-" for ch in value) and len(value) <= 50


def kick_live_status(payload: dict) -> tuple[bool, str] | None:
    """Whether this channel payload says a broadcast is on.

    None means the payload did not say. The chat room id is unchanged either way.
    """
    if not isinstance(payload, dict) or "livestream" not in payload:
        return None
    live = payload.get("livestream")
    if not isinstance(live, dict) or live.get("is_live") is not True:
        return False, ""
    title = " ".join(str(live.get("session_title") or "").split())
    return True, title[:80]


def public_kick_detail(info: dict, follow: bool) -> str:
    label = info.get("username") or info.get("slug") or "this channel"
    sentence = ""
    if follow and info.get("live") is not None:
        if info.get("live"):
            title = str(info.get("title") or "")
            sentence = f" The channel is live: {title}." if title else " The channel is live."
        else:
            sentence = " The channel is not live. Chat stays connected."
    return (
        f"Public chat for {label}.{sentence} "
        "New subs, gifted subs, and Kicks do not need you to own the channel."
    )


def chat_lines_from_kick_public(event_name: str, data: object) -> list[dict]:
    """A public chat message. Donations stay on the other parser. Nothing is fetched."""
    leaf = (event_name or "").strip().split("\\")[-1].lower().replace("_", "")
    if leaf not in {"chatmessage", "chatmessageevent", "chatmessagesentevent"}:
        return []
    payload = _object(data)
    if payload is None:
        return []
    text = " ".join(str(payload.get("content") or payload.get("message") or "").split())
    if not text:
        return []
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    author = str(sender.get("username") or payload.get("username") or payload.get("sender_username") or "")
    key = str(payload.get("id") or "")
    if not key:
        key = stable_key("kick:chat:", [author, text, payload.get("created_at")])
    return [{"key": key, "author": author, "text": text, "time": chat_time(payload.get("created_at"))}]


def donations_from_kick_public(event_name: str, data: object, channel_slug: str) -> list[Donation]:
    payload = _object(data)
    if payload is None:
        return []
    kind = _event_kind(event_name) or _event_kind(str(payload.get("type") or ""))
    if kind == "kicks":
        donation = _kicks(payload, channel_slug)
    elif kind == "subscription":
        donation = _subscription(payload, channel_slug, renewal=False)
    elif kind == "subscription_renewal":
        donation = _subscription(payload, channel_slug, renewal=True)
    elif kind == "gifted_subscriptions":
        donation = _gifts(payload, channel_slug)
    else:
        donation = None
    return [donation] if donation is not None else []


def _event_kind(name: str) -> str | None:
    leaf = name.strip().split("\\")[-1].lower().replace("_", "")
    if not leaf or "lucky" in leaf or leaf in {"chatmessage", "chatmessagesentevent", "messagedeleted"}:
        return None
    # Follower notices are not a payment. A subscription is a membership,
    # priced later from the membership price saved in Settings.
    if "follow" in leaf:
        return None
    if "kick" in leaf and "gift" in leaf:
        return "kicks"
    if "gift" in leaf and "sub" in leaf:
        return "gifted_subscriptions"
    if "renew" in leaf and "sub" in leaf:
        return "subscription_renewal"
    if leaf in {"subscription", "subscriptionevent"} or leaf.endswith("subscriptionevent"):
        return "subscription"
    return None


def _kicks(payload: dict, channel_slug: str) -> Donation | None:
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    author = str(sender.get("username") or payload.get("username") or payload.get("sender_username") or "")
    author_id = str(sender.get("id") or sender.get("user_id") or "")
    gift = payload.get("gift") if isinstance(payload.get("gift"), dict) else {}
    gift_name = str(gift.get("name") or "")
    text = str(payload.get("message") or gift.get("message") or "")
    if gift_name and text and text != gift_name:
        message = f"{gift_name}: {text}"
    else:
        message = text or gift_name
    amount = as_int(gift.get("amount") if gift else None)
    if amount is None:
        amount = as_int(payload.get("amount"))
    external = str(payload.get("gift_transaction_id") or payload.get("id") or "")
    dedupe = f"kick:public:kicks:{external}" if external else stable_key(
        "kick:public:kicks:", [channel_slug, author, amount, message]
    )
    broadcaster = payload.get("broadcaster") if isinstance(payload.get("broadcaster"), dict) else {}
    channel = str(broadcaster.get("channel_slug") or broadcaster.get("username") or channel_slug)
    created = to_utc_iso(payload.get("created_at"))
    return Donation(
        dedupe_key=dedupe,
        platform="kick",
        kind="kicks",
        external_id=external,
        channel=channel,
        author_name=author,
        author_id=author_id,
        message=message,
        amount_value=None if amount is None else float(amount),
        amount_micros=None,
        currency="KICKS" if amount is not None else None,
        created_at=created,
        raw=payload,
    )


def _subscription(payload: dict, channel_slug: str, renewal: bool) -> Donation | None:
    user = payload.get("subscriber") if isinstance(payload.get("subscriber"), dict) else {}
    nested = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    author = str(payload.get("username") or user.get("username") or nested.get("username") or "")
    author_id = str(user.get("user_id") or user.get("id") or nested.get("id") or "")
    months = as_int(payload.get("months") if payload.get("months") is not None else payload.get("duration"))
    prefix = "Renewed" if renewal else "Subscribed"
    if months:
        unit = "month" if months == 1 else "months"
        message = f"{prefix} ({months} {unit})"
    else:
        message = prefix
    created = to_utc_iso(payload.get("created_at"))
    external = str(payload.get("id") or "")
    dedupe = f"kick:public:sub:{external}" if external else stable_key(
        "kick:public:sub:", [channel_slug, author, months, payload.get("created_at"), prefix]
    )
    return Donation(
        dedupe_key=dedupe,
        platform="kick",
        kind="subscription_renewal" if renewal else "subscription",
        external_id=external,
        channel=channel_slug,
        author_name=author,
        author_id=author_id,
        message=message,
        amount_value=None,
        amount_micros=None,
        currency=None,
        created_at=created,
        raw=payload,
    )


def _gifts(payload: dict, channel_slug: str) -> Donation | None:
    gifter = payload.get("gifter") if isinstance(payload.get("gifter"), dict) else {}
    author = str(payload.get("gifter_username") or gifter.get("username") or "")
    if not author and gifter.get("is_anonymous"):
        author = "Anonymous"
    author_id = str(gifter.get("user_id") or gifter.get("id") or "")
    names: list[str] = []
    gifted = payload.get("gifted_usernames")
    if isinstance(gifted, list):
        names = [str(name) for name in gifted if name]
    else:
        for person in payload.get("giftees") or []:
            if isinstance(person, dict) and person.get("username"):
                names.append(str(person["username"]))
            elif isinstance(person, str):
                names.append(person)
    count = len(names) or as_int(payload.get("count")) or as_int(payload.get("gifted_count")) or 1
    noun = "subscription" if count == 1 else "subscriptions"
    message = f"Gifted {count} {noun}"
    if names:
        message += " to " + ", ".join(names)
    external = str(payload.get("id") or "")
    dedupe = f"kick:public:gifts:{external}" if external else stable_key(
        "kick:public:gifts:", [channel_slug, author, count, ",".join(names)]
    )
    return Donation(
        dedupe_key=dedupe,
        platform="kick",
        kind="gifted_subscriptions",
        external_id=external,
        channel=channel_slug,
        author_name=author or "Anonymous",
        author_id=author_id,
        message=message,
        amount_value=None,
        amount_micros=None,
        currency=None,
        created_at=to_utc_iso(payload.get("created_at")),
        raw=payload,
    )


def _object(data: object) -> dict | None:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    inner = data.get("data")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except json.JSONDecodeError:
            inner = None
    if isinstance(inner, dict):
        return inner
    return data


class _SocketBuffer:
    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._buf = b""

    def read_exact(self, size: int) -> bytes:
        while len(self._buf) < size:
            chunk = self._sock.recv(max(4096, size - len(self._buf)))
            if not chunk:
                raise ConnectionError("Kick chat socket closed")
            self._buf += chunk
        out, self._buf = self._buf[:size], self._buf[size:]
        return out

    def feed(self, data: bytes) -> None:
        self._buf += data


def _send_control(sock: socket.socket, opcode: int, payload: bytes) -> None:
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    header.append(0x80 | len(payload))
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _send_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += length.to_bytes(2, "big")
    else:
        header.append(0x80 | 127)
        header += length.to_bytes(8, "big")
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _read_frame(buf: _SocketBuffer) -> tuple[int, bytes]:
    first, second = buf.read_exact(2)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = int.from_bytes(buf.read_exact(2), "big")
    elif length == 127:
        length = int.from_bytes(buf.read_exact(8), "big")
    if length > 2_000_000:
        raise ApiError("Kick chat frame was too large")
    mask = buf.read_exact(4) if masked else b""
    payload = buf.read_exact(length)
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def _open_pusher() -> socket.socket:
    raw = socket.create_connection((PUSHER_HOST, 443), timeout=20)
    context = ssl.create_default_context()
    sock = context.wrap_socket(raw, server_hostname=PUSHER_HOST)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {PUSHER_PATH} HTTP/1.1\r\n"
        f"Host: {PUSHER_HOST}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Origin: https://kick.com\r\n"
        f"User-Agent: {KICK_UA}\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    buffered = b""
    while b"\r\n\r\n" not in buffered:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise ApiError("Kick chat handshake closed before a response")
        buffered += chunk
        if len(buffered) > 65536:
            sock.close()
            raise ApiError("Kick chat handshake was too large")
    head, rest = buffered.split(b"\r\n\r\n", 1)
    status = head.split(b"\r\n", 1)[0]
    if b" 101 " not in status:
        sock.close()
        raise ApiError("Kick chat refused the websocket connection")
    holder = _SocketBuffer(sock)
    holder.feed(rest)
    sock._stream_donations_buffer = holder  # type: ignore[attr-defined]
    return sock


class PublicKickChat:
    def __init__(self, store: Store, transport: Transport, chat_feed=None):
        self.store = store
        self.transport = transport
        self._chat_feed = chat_feed
        self._chat_for = ""
        self.detail = ""
        self.error = ""
        self._target = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._sock_lock = threading.Lock()

    def rest(self) -> None:
        """Stop reading chat. The saved channel stays in settings."""
        self._stop.set()
        self._target = ""
        self.detail = ""
        self.error = ""
        with self._sock_lock:
            sock = self._sock
            self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2)

    def source(self) -> str:
        watch = self.store.get_setting("kick_watch").strip()
        if not watch:
            return ""
        follow = "1" if self.store.get_setting("kick_follow_live") == "1" else "0"
        return f"{follow}\n{watch}"

    def ensure(self) -> None:
        target = self.source()
        self._chat_for = remember_chat(self._chat_feed, "kick", self._chat_for, target)
        if target == self._target and (not target or (self._thread and self._thread.is_alive())):
            return
        self._stop.set()
        self._stop = threading.Event()
        self._target = target
        self.detail = ""
        self.error = ""
        if not target:
            return
        stop = self._stop
        self._thread = threading.Thread(target=self._loop, args=(target, stop), name="kick-public-chat", daemon=True)
        self._thread.start()

    def _loop(self, target: str, stop: threading.Event) -> None:
        follow_flag, source = target.split("\n", 1)
        follow = follow_flag == "1"
        while not stop.is_set() and not self.store.collection_paused():
            sock = None
            try:
                slug = kick_slug(source)
                if stop.is_set() or self.store.collection_paused():
                    return
                info = self._lookup(slug)
                if stop.is_set() or self.store.collection_paused():
                    return
                sock = _open_pusher()
                with self._sock_lock:
                    if stop.is_set() or self.store.collection_paused():
                        sock.close()
                        return
                    self._sock = sock
                buf: _SocketBuffer = sock._stream_donations_buffer  # type: ignore[attr-defined]
                self._subscribe(sock, info)
                if stop.is_set() or self.store.collection_paused():
                    return
                self.error = ""
                self.detail = public_kick_detail(info, follow)
                self.store.set_status("kick", "ok", self.detail)
                sock.settimeout(50)
                next_live = time.monotonic() + 60
                while not stop.is_set() and not self.store.collection_paused():
                    if follow and time.monotonic() >= next_live:
                        next_live = time.monotonic() + 60
                        fresh = self._refresh_live(slug, info)
                        if stop.is_set() or self.store.collection_paused():
                            return
                        if fresh is not None and (
                            fresh["chatroom_id"] != info["chatroom_id"]
                            or fresh["channel_id"] != info["channel_id"]
                        ):
                            break
                        if fresh is not None:
                            info = fresh
                    try:
                        opcode, payload = _read_frame(buf)
                    except TimeoutError:
                        continue
                    if stop.is_set() or self.store.collection_paused():
                        return
                    if opcode == 8:
                        break
                    if opcode == 9:
                        _send_control(sock, 0xA, payload)
                        continue
                    if opcode != 1 or not payload:
                        continue
                    self._on_message(sock, payload, info["slug"], stop)
            except TimeoutError:
                continue
            except Exception as exc:
                if stop.is_set() or self.store.collection_paused():
                    return
                self.error = redact(str(exc))
                self.detail = ""
                self.store.log("kick", "error", self.error)
                self.store.set_status("kick", "error", self.error, error=self.error)
                stop.wait(15)
            finally:
                with self._sock_lock:
                    if self._sock is sock:
                        self._sock = None
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

    def _lookup(self, slug: str) -> dict:
        if slug.isdigit():
            return {
                "chatroom_id": int(slug),
                "channel_id": int(slug),
                "slug": slug,
                "username": slug,
                "live": None,
                "title": "",
            }
        result = self.transport.request(
            "GET",
            f"https://kick.com/api/v2/channels/{slug}",
            headers={"Accept": "application/json"},
            allow_hosts=KICK_HOSTS,
            user_agent=KICK_UA,
        )
        if result.status == 404:
            raise ApiError(f"Kick channel {slug} was not found.", status=404)
        if result.status >= 400:
            raise ApiError(api_error_message(result), status=result.status)
        payload = result.json()
        if not isinstance(payload, dict):
            raise ApiError("Kick channel lookup did not return an object")
        chatroom = payload.get("chatroom") if isinstance(payload.get("chatroom"), dict) else {}
        chatroom_id = as_int(chatroom.get("id"))
        channel_id = as_int(payload.get("id") or chatroom.get("channel_id"))
        if chatroom_id is None:
            raise ApiError(f"Kick did not return a chatroom for {slug}")
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        status = kick_live_status(payload)
        live: bool | None
        title = ""
        if status is None:
            live = None
        else:
            live, title = status
        return {
            "chatroom_id": chatroom_id,
            "channel_id": channel_id or chatroom_id,
            "slug": str(payload.get("slug") or slug),
            "username": str(user.get("username") or payload.get("slug") or slug),
            "live": live,
            "title": title,
        }

    def _refresh_live(self, slug: str, info: dict) -> dict | None:
        """Re-read live or offline from the same channel lookup. Chat stays up."""
        try:
            fresh = self._lookup(slug)
        except (ApiError, OSError, TimeoutError, ValueError):
            return None
        if fresh["chatroom_id"] != info["chatroom_id"] or fresh["channel_id"] != info["channel_id"]:
            return fresh
        self.detail = public_kick_detail(fresh, True)
        self.store.set_status("kick", "ok", self.detail)
        return fresh

    def _subscribe(self, sock: socket.socket, info: dict) -> None:
        chatroom_id = info["chatroom_id"]
        channel_id = info["channel_id"]
        names = [
            f"chatrooms.{chatroom_id}.v2",
            f"chatrooms.{chatroom_id}",
            f"chatroom_{chatroom_id}",
            f"channel.{channel_id}",
            f"channel_{channel_id}",
        ]
        for name in dict.fromkeys(names):
            _send_text(sock, json.dumps({
                "event": "pusher:subscribe",
                "data": {"auth": "", "channel": name},
            }))

    def _on_message(self, sock: socket.socket, payload: bytes, channel_slug: str, stop: threading.Event) -> None:
        if stop.is_set() or self.store.collection_paused():
            return
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(message, dict):
            return
        event = str(message.get("event") or "")
        if event == "pusher:ping":
            _send_text(sock, json.dumps({"event": "pusher:pong", "data": {}}))
            return
        if event.startswith("pusher:"):
            return
        offer_chat(self._chat_feed, "kick", chat_lines_from_kick_public(event, message.get("data")))
        inserted = self.store.add_many(donations_from_kick_public(event, message.get("data"), channel_slug))
        if inserted:
            self.store.log("kick", "info", f"Stored {inserted} event(s) from the public Kick chat.")
