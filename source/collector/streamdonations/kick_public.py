"""Public Kick chat, for a channel the user does not own.

Owner webhooks only fire for the signed-in channel. Subscriptions, gifted
subscriptions, and Kicks that appear in chat are broadcast on the public
connection Kick's website offers. No OAuth and no webhook URL are required.

    GET https://kick.com/api/v2/channels/{slug}
    POST https://web.kick.com/api/v1/realtime/connection
    POST https://web.kick.com/api/v1/realtime/channels/{channel_id}/chat/connection
    POST https://web.kick.com/api/v1/realtime/auth/connection
        channels: chatrooms.{id}.v2, channel.{id}, channel_{id}

The connection token from that last call is sent on the socket and is not
logged or stored.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import threading
import time
import uuid
from urllib.parse import urlparse

from .chat_feed import chat_time, offer_chat, remember_chat
from .normalize import Donation
from .store import Store
from .transport import Transport, api_error_message
from .util import ApiError, as_int, ledger_title, redact, stable_key, to_utc_iso

KICK_HOSTS = {"kick.com"}
# Observed Kick website login. The window loads this fixed page.
# The renderer does not choose the address.
KICK_LOGIN_URL = "https://kick.com/login"
_REALTIME = "https://web.kick.com/api/v1/realtime"
KICK_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 StreamDonations/1.0"
)
_RESERVED = {"api", "settings", "dashboard", "browse", "categories", "following"}


def realtime_endpoints(payload: object) -> list[tuple[str, str, str]]:
    """Public sockets from one negotiation response, in the order Kick sent."""
    data = payload.get("data") if isinstance(payload, dict) else None
    connections = data.get("connections") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("mode") != "websocket" or not isinstance(connections, list):
        raise ApiError("Kick did not offer a public chat connection")
    found: list[tuple[str, str, str]] = []
    for item in connections:
        if not isinstance(item, dict):
            continue
        credentials = item.get("credentials") if isinstance(item.get("credentials"), dict) else {}
        provider = item.get("provider")
        if provider == "pusher":
            endpoint = _pusher_endpoint(credentials.get("app_key"), credentials.get("cluster"))
        elif provider == "centrifugo":
            endpoint = _centrifugo_endpoint(credentials.get("url"))
        else:
            endpoint = None
        if endpoint is not None:
            found.append((str(provider), endpoint[0], endpoint[1]))
    if not found:
        raise ApiError("Kick did not offer a public chat connection")
    return found


def realtime_endpoint(payload: object) -> tuple[str, str, str]:
    """The first public socket in one negotiation response."""
    return realtime_endpoints(payload)[0]


def _pusher_endpoint(key: object, cluster: object) -> tuple[str, str] | None:
    text = str(key or "")
    name = str(cluster or "")
    if (
        not text.isalnum()
        or not name.isalnum()
        or not text.isascii()
        or not name.isascii()
        or len(text) > 128
        or len(name) > 32
    ):
        return None
    return (
        f"ws-{name}.pusher.com",
        f"/app/{text}?protocol=7&client=js&version=8.4.0-rc2&flash=false",
    )


def _centrifugo_endpoint(url: object) -> tuple[str, str] | None:
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return None
    host = parsed.hostname or ""
    if (
        parsed.scheme == "wss"
        and host.startswith("realtime.")
        and host.endswith(".platform.kick.com")
        and parsed.netloc == host
        and parsed.path == "/connection/websocket"
        and not parsed.query
        and not parsed.fragment
        and host.isascii()
    ):
        return host, parsed.path
    return None


def interpret_pusher(message: object) -> dict:
    """One Pusher frame. An error means this app key is not the live one."""
    if not isinstance(message, dict):
        return {"op": "ignore"}
    event = str(message.get("event") or "")
    if event == "pusher:error":
        return {"op": "error"}
    if event == "pusher:ping":
        return {"op": "ping"}
    if event == "pusher:connection_established":
        return {"op": "ready"}
    if event.startswith("pusher:") or event.startswith("pusher_internal:"):
        return {"op": "ignore"}
    return {"op": "event"}


def interpret_centrifugo(item: object) -> dict:
    """One Centrifugo object, without the connection token or other secrets."""
    if item == {}:
        return {"op": "ping"}
    if not isinstance(item, dict):
        return {"op": "ignore"}
    command_id = item.get("id")
    if isinstance(command_id, int) and not isinstance(command_id, bool) and command_id > 0:
        if item.get("error"):
            return {"op": "error"}
        for kind in ("connect", "subscribe", "refresh"):
            result = item.get(kind)
            if isinstance(result, dict):
                kept = {key: result[key] for key in ("expires", "ttl", "ping", "pong") if key in result}
                return {"op": kind, "result": kept}
        return {"op": "ignore"}
    push = item.get("push")
    if not isinstance(push, dict):
        return {"op": "ignore"}
    if "disconnect" in push or "unsubscribe" in push:
        return {"op": "closed"}
    publication = push.get("pub") if isinstance(push.get("pub"), dict) else {}
    data = publication.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {"op": "ignore"}
    if isinstance(data, dict) and data.get("event"):
        return {"op": "event", "event": str(data.get("event")), "data": data.get("data")}
    return {"op": "ignore"}


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


def kick_popup_chat_url(slug: str) -> str:
    """Kick's own pop-out chat for one channel slug."""
    text = str(slug or "").strip().lower()
    if not _slug_ok(text):
        raise ValueError("That Kick chat could not be opened.")
    return f"https://kick.com/popout/{text}/chat"


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
    celebration = _subscription_celebration(payload, channel_slug)
    if celebration is not None:
        return [celebration]
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


def _metadata_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, ValueError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _subscription_celebration(payload: dict, channel_slug: str) -> Donation | None:
    """Kick chat banner: someone celebrates a subscription month count.

    That notice is not a payment. It is a complimentary Super Chat at $0.00.
    Ordinary chat, and any celebration that is not a subscription, stays out.
    """
    if str(payload.get("type") or "").strip().lower() != "celebration":
        return None
    metadata = _metadata_object(payload.get("metadata"))
    celebration = _metadata_object(metadata.get("celebration"))
    label = str(celebration.get("type") or "").strip().lower()
    if label and "sub" not in label:
        return None
    months = as_int(
        celebration.get("total_months")
        if celebration.get("total_months") is not None
        else celebration.get("months")
    )
    if not months or months < 1:
        return None
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    author = str(sender.get("username") or payload.get("username") or "")
    author_id = str(sender.get("id") or sender.get("user_id") or "")
    unit = "month" if months == 1 else "months"
    message = f"Subscribed for {months} {unit}"
    content = " ".join(str(payload.get("content") or payload.get("message") or "").split())
    if content:
        message = f"{message}. {content}"
    external = str(celebration.get("id") or payload.get("id") or "")
    dedupe = f"kick:public:celebration:{external}" if external else stable_key(
        "kick:public:celebration:", [channel_slug, author, months, payload.get("created_at")]
    )
    created = to_utc_iso(celebration.get("created_at") or payload.get("created_at"))
    return Donation(
        dedupe_key=dedupe,
        platform="kick",
        kind="member_milestone",
        external_id=external,
        channel=channel_slug,
        author_name=author,
        author_id=author_id,
        message=message,
        amount_value=0.0,
        amount_micros=None,
        currency="USD",
        created_at=created,
        raw=payload,
    )


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


def _open_websocket(host: str, path: str) -> tuple[socket.socket, _SocketBuffer]:
    if any(ord(char) < 33 or ord(char) == 127 for char in f"{host}{path}"):
        raise ApiError("Kick did not offer a public chat connection")
    raw = socket.create_connection((host, 443), timeout=20)
    context = ssl.create_default_context()
    sock = context.wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Origin: https://kick.com\r\n"
        f"User-Agent: {KICK_UA}\r\n"
        "\r\n"
    )
    try:
        sock.sendall(request.encode("ascii"))
        buffered = b""
        while b"\r\n\r\n" not in buffered:
            chunk = sock.recv(4096)
            if not chunk:
                raise ApiError("Kick chat handshake closed before a response")
            buffered += chunk
            if len(buffered) > 65536:
                raise ApiError("Kick chat handshake was too large")
        head, rest = buffered.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise ApiError("Kick chat refused the websocket connection")
    except Exception:
        sock.close()
        raise
    holder = _SocketBuffer(sock)
    holder.feed(rest)
    return sock, holder


class _PublicSocket:
    """One negotiated Kick chat socket. The connection token is not kept."""

    def __init__(self, transport: Transport, channel_id: int):
        self.transport = transport
        self.channel_id = int(channel_id)
        self.sock: socket.socket | None = None
        self.provider = ""
        self._buf: _SocketBuffer | None = None
        self._client_id = ""
        self._next_id = 0
        self._queue: list[bytes] = []
        self._connected = False
        self._refresh_at = float("inf")
        self._pong = False

    def close(self) -> None:
        sock = self.sock
        self.sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def start(self) -> None:
        self._client_id = str(uuid.uuid4())
        last_error: ApiError | None = None
        for provider, host, path in self._negotiate():
            self.close()
            self.provider = provider
            self._connected = False
            self._queue.clear()
            self._next_id = 0
            self._refresh_at = float("inf")
            self._pong = False
            try:
                sock, buf = _open_websocket(host, path)
                self.sock = sock
                self._buf = buf
                if provider == "centrifugo":
                    self._command("connect", {
                        "token": self._connection_token(),
                        "name": "web",
                        "version": "1",
                    })
                self._pump_until_connected()
                return
            except ApiError as exc:
                last_error = exc
                self.close()
        raise last_error or ApiError("Kick did not offer a public chat connection")

    def subscribe(self, names: list[str]) -> None:
        for name in dict.fromkeys(names):
            if self.provider == "pusher":
                _send_text(self.sock, json.dumps({
                    "event": "pusher:subscribe",
                    "data": {"auth": "", "channel": name},
                }))
            else:
                self._command("subscribe", {"channel": name})

    def read_event(self) -> bytes | None:
        """The next public event, or None when the socket is quiet."""
        if self._queue:
            return self._queue.pop(0)
        self._maybe_refresh()
        assert self.sock is not None
        timeout = 50.0
        if self._refresh_at < float("inf"):
            timeout = min(timeout, max(1.0, self._refresh_at - time.monotonic()))
        self.sock.settimeout(timeout)
        try:
            self._read_once()
        except TimeoutError:
            return None
        if self._queue:
            return self._queue.pop(0)
        return None

    def _negotiate(self) -> list[tuple[str, str, str]]:
        body = {
            "client": {"id": self._client_id, "type": "web"},
            "capabilities": {
                "accepted_providers": [
                    {"provider": "centrifugo"},
                    {"provider": "pusher"},
                ]
            },
        }
        last_error: ApiError | None = None
        found: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for path in (f"/channels/{self.channel_id}/chat/connection", "/connection"):
            try:
                links = realtime_endpoints(self._post(path, body).json())
            except (ApiError, OSError, TimeoutError, ValueError) as exc:
                last_error = exc if isinstance(exc, ApiError) else ApiError(
                    "Kick did not offer a public chat connection"
                )
                continue
            for link in links:
                if link in seen:
                    continue
                seen.add(link)
                found.append(link)
        if not found:
            raise last_error or ApiError("Kick did not offer a public chat connection")
        return found

    def _post(self, path: str, body: dict):
        result = self.transport.request(
            "POST",
            _REALTIME + path,
            json_body=body,
            headers={"Origin": "https://kick.com", "x-app-platform": "web"},
            allow_hosts=KICK_HOSTS,
            user_agent=KICK_UA,
        )
        if result.status >= 400:
            raise ApiError(api_error_message(result), status=result.status)
        return result

    def _connection_token(self) -> str:
        result = self._post("/auth/connection", {"client_id": self._client_id})
        payload = result.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        token = data.get("token") if isinstance(data, dict) else None
        if (
            not isinstance(token, str)
            or not 8 <= len(token) <= 4096
            or not token.isascii()
            or any(char.isspace() for char in token)
        ):
            raise ApiError("Kick did not offer a public chat connection")
        return token

    def _command(self, kind: str, data: dict) -> None:
        self._next_id += 1
        assert self.sock is not None
        _send_text(self.sock, json.dumps({"id": self._next_id, kind: data}, separators=(",", ":")))

    def _pump_until_connected(self) -> None:
        deadline = time.monotonic() + 15
        while not self._connected:
            if time.monotonic() >= deadline:
                raise ApiError("Kick chat did not finish opening")
            assert self.sock is not None
            self.sock.settimeout(max(1.0, deadline - time.monotonic()))
            try:
                self._read_once()
            except TimeoutError as exc:
                raise ApiError("Kick chat did not finish opening") from exc

    def _read_once(self) -> None:
        assert self._buf is not None and self.sock is not None
        opcode, payload = _read_frame(self._buf)
        if opcode == 8:
            raise ApiError("Kick closed the public chat connection")
        if opcode == 9:
            _send_control(self.sock, 0xA, payload)
            return
        if opcode != 1 or not payload:
            return
        if self.provider == "pusher":
            self._take_pusher(payload)
        else:
            self._take_centrifugo(payload)

    def _take_pusher(self, payload: bytes) -> None:
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        action = interpret_pusher(message)
        if action["op"] == "error":
            raise ApiError("Kick closed the public chat connection")
        if action["op"] == "ping":
            assert self.sock is not None
            _send_text(self.sock, json.dumps({"event": "pusher:pong", "data": {}}))
            return
        if action["op"] == "ready":
            self._connected = True
            return
        if action["op"] == "event":
            self._queue.append(payload)

    def _take_centrifugo(self, payload: bytes) -> None:
        for line in payload.decode("utf-8", "replace").splitlines() or [""]:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            action = interpret_centrifugo(item)
            op = action["op"]
            if op == "ping":
                if self._pong and self.sock is not None:
                    _send_text(self.sock, "{}")
                continue
            if op in {"error", "closed"}:
                raise ApiError("Kick closed the public chat connection")
            if op == "connect":
                self._connected = True
                self._lease(action.get("result") or {})
                continue
            if op == "refresh":
                self._lease(action.get("result") or {})
                continue
            if op == "event":
                encoded = json.dumps(
                    {"event": action["event"], "data": action.get("data")},
                    separators=(",", ":"),
                ).encode("utf-8")
                self._queue.append(encoded)

    def _lease(self, result: dict) -> None:
        if result.get("expires") is True:
            ttl = as_int(result.get("ttl")) or 0
            if ttl < 1:
                raise ApiError("Kick closed the public chat connection")
            self._refresh_at = time.monotonic() + max(ttl * 0.8, 1.0)
        if result.get("pong") is True:
            self._pong = True

    def _maybe_refresh(self) -> None:
        if self.provider != "centrifugo" or time.monotonic() < self._refresh_at:
            return
        self._refresh_at = float("inf")
        self._command("refresh", {"token": self._connection_token()})


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
            session: _PublicSocket | None = None
            try:
                slug = kick_slug(source)
                if stop.is_set() or self.store.collection_paused():
                    return
                info = self._lookup(slug)
                if stop.is_set() or self.store.collection_paused():
                    return
                session = _PublicSocket(self.transport, info["channel_id"])
                session.start()
                with self._sock_lock:
                    if stop.is_set() or self.store.collection_paused():
                        return
                    self._sock = session.sock
                session.subscribe(self._channel_names(info))
                if stop.is_set() or self.store.collection_paused():
                    return
                self.error = ""
                self.detail = public_kick_detail(info, follow)
                self.store.set_status("kick", "ok", self.detail)
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
                    payload = session.read_event()
                    if stop.is_set() or self.store.collection_paused():
                        return
                    if payload and session.sock is not None:
                        self._on_message(session.sock, payload, info["slug"], stop)
            except Exception as exc:
                if stop.is_set() or self.store.collection_paused():
                    return
                self.error = redact(str(exc))
                self.detail = ""
                self.store.log("kick", "error", self.error)
                self.store.set_status("kick", "error", self.error, error=self.error)
                stop.wait(15)
            finally:
                sock = session.sock if session is not None else None
                with self._sock_lock:
                    if self._sock is sock:
                        self._sock = None
                if session is not None:
                    session.close()

    def stream_title(self, watch: str) -> str:
        """Title of the broadcast on this channel. Offline, or a bare chat id, has none."""
        slug = kick_slug(watch)
        if not slug or slug.isdigit():
            return ""
        info = self._lookup(slug)
        if info.get("live") is not True:
            return ""
        return ledger_title(info.get("title"))

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

    def _channel_names(self, info: dict) -> list[str]:
        chatroom_id = info["chatroom_id"]
        channel_id = info["channel_id"]
        names = [
            f"chatrooms.{chatroom_id}.v2",
            f"chatrooms.{chatroom_id}",
            f"chatroom_{chatroom_id}",
            f"channel.{channel_id}",
            f"channel_{channel_id}",
        ]
        return list(dict.fromkeys(names))

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
        offer_chat(
            self._chat_feed,
            "kick",
            chat_lines_from_kick_public(event, message.get("data")),
            chat=channel_slug,
        )
        inserted = self.store.add_many(donations_from_kick_public(event, message.get("data"), channel_slug))
        if inserted:
            self.store.log("kick", "info", f"Stored {inserted} event(s) from the public Kick chat.")
