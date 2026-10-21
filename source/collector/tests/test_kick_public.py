"""Public Kick chat parsing. No network and no channel ownership."""

import json
import socket
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from streamdonations import kick_public
from streamdonations.kick import KickClient

from streamdonations.kick_public import (
    PublicKickChat,
    donations_from_kick_public,
    interpret_centrifugo,
    interpret_pusher,
    kick_live_status,
    kick_slug,
    public_kick_detail,
    realtime_endpoint,
    validate_kick_watch,
)
from streamdonations.util import ApiError
from streamdonations.money import quote
from streamdonations.transport import HttpResult


class PublicKickTests(unittest.TestCase):
    def test_slug_from_url(self):
        self.assertEqual(kick_slug("xQc"), "xqc")
        self.assertEqual(kick_slug("https://kick.com/xqc"), "xqc")
        self.assertEqual(kick_slug("https://kick.com/xqc/chat"), "xqc")
        self.assertEqual(validate_kick_watch("https://www.kick.com/xqc"), "https://www.kick.com/xqc")
        with self.assertRaises(ValueError):
            validate_kick_watch("https://evil.example/xqc")
        with self.assertRaises(ValueError):
            kick_slug("https://kick.com/settings/developer")

    def test_public_events(self):
        kicks = donations_from_kick_public(
            "KicksGifted",
            {
                "gift_transaction_id": "tx-1",
                "message": "w",
                "sender": {"id": 9, "username": "fan"},
                "gift": {"name": "Rage Quit", "amount": 500, "type": "LEVEL_UP"},
            },
            "creator",
        )[0]
        self.assertEqual(kicks.kind, "kicks")
        self.assertEqual(kicks.author_name, "fan")
        self.assertEqual(kicks.message, "Rage Quit: w")
        self.assertEqual(kicks.amount_value, 500)
        self.assertEqual(kicks.currency, "KICKS")
        self.assertEqual(kicks.channel, "creator")
        self.assertEqual(kicks.dedupe_key, "kick:public:kicks:tx-1")

        sub = donations_from_kick_public(
            r"App\Events\SubscriptionEvent",
            {"username": "fan", "months": 1, "chatroom_id": 10},
            "creator",
        )[0]
        self.assertEqual(sub.kind, "subscription")
        self.assertEqual(sub.message, "Subscribed (1 month)")

        gifted = donations_from_kick_public(
            r"App\Events\GiftedSubscriptionsEvent",
            {"gifter_username": "patron", "gifted_usernames": ["a", "b"]},
            "creator",
        )[0]
        self.assertEqual(gifted.kind, "gifted_subscriptions")
        self.assertEqual(gifted.message, "Gifted 2 subscriptions to a, b")

        self.assertEqual(
            donations_from_kick_public("App\\Events\\ChatMessageEvent", {"content": "hello"}, "creator"),
            [],
        )
        celebrated = donations_from_kick_public(
            "App\\Events\\ChatMessageEvent",
            {
                "id": "msg-15",
                "type": "celebration",
                "content": "Might as well let you know. Been around quite a while it looks like.",
                "created_at": "2026-10-09T02:05:00Z",
                "sender": {"id": 42, "username": "lightferret"},
                "metadata": {
                    "celebration": {
                        "id": "cel-15",
                        "type": "subscription",
                        "total_months": 15,
                    }
                },
            },
            "creator",
        )[0]
        self.assertEqual(celebrated.kind, "member_milestone")
        self.assertEqual(celebrated.author_name, "lightferret")
        self.assertEqual(celebrated.amount_value, 0.0)
        self.assertEqual(celebrated.currency, "USD")
        self.assertEqual(
            celebrated.message,
            "Subscribed for 15 months. Might as well let you know. Been around quite a while it looks like.",
        )
        self.assertEqual(celebrated.dedupe_key, "kick:public:celebration:cel-15")
        priced = quote(
            {
                "platform": celebrated.platform,
                "kind": celebrated.kind,
                "amount_value": celebrated.amount_value,
                "currency": celebrated.currency,
                "message": celebrated.message,
                "raw": celebrated.raw,
            },
            4.99,
            {"USD": 1.0},
        )
        self.assertEqual(priced.amount, 0.0)
        self.assertEqual(priced.usd, 0.0)
        self.assertEqual(priced.currency, "USD")
        self.assertEqual(priced.label, "Super Chat")
        self.assertEqual(
            donations_from_kick_public(
                "App\\Events\\ChatMessageEvent",
                {"id": "msg-1", "type": "message", "content": "celebrates their subscription for 15 months!"},
                "creator",
            ),
            [],
        )
        self.assertEqual(
            donations_from_kick_public("LuckyUsersWhoGotGiftSubscriptionsEvent", {"users": ["a"]}, "creator"),
            [],
        )
        subscribed = donations_from_kick_public(
            r"App\Events\ChannelSubscriptionEvent",
            {"user_ids": [10466179], "username": "MaxResist", "channel_id": 35522671},
            "creator",
        )[0]
        self.assertEqual(subscribed.kind, "subscription")
        self.assertEqual(subscribed.author_name, "MaxResist")
        self.assertEqual(subscribed.message, "Subscribed")
        self.assertIsNone(subscribed.amount_value)
        self.assertIsNone(subscribed.currency)
        # The dollar amount is the membership price from Settings, read when the row is shown.
        for price in (4.99, 7.25):
            priced = quote(
                {
                    "platform": subscribed.platform,
                    "kind": subscribed.kind,
                    "amount_value": subscribed.amount_value,
                    "currency": subscribed.currency,
                    "message": subscribed.message,
                    "raw": subscribed.raw,
                },
                price,
                {},
            )
            self.assertEqual(priced.usd, price)
            self.assertEqual(priced.currency, "USD")
        self.assertEqual(
            donations_from_kick_public(
                r"App\Events\FollowersUpdated",
                {"username": "MaxResist", "followersCount": 12},
                "creator",
            ),
            [],
        )

    def test_livestream_object_reports_live_without_changing_chat(self):
        offline = {"id": 668, "slug": "xqc", "chatroom": {"id": 668}, "livestream": None}
        self.assertEqual(kick_live_status(offline), (False, ""))
        self.assertIsNone(kick_live_status({"chatroom": {"id": 1}}))
        live = {
            "id": 4911,
            "slug": "classybeef",
            "chatroom": {"id": 4910},
            "livestream": {
                "id": 130351980,
                "channel_id": 4911,
                "is_live": True,
                "session_title": "Slot night",
            },
        }
        self.assertEqual(kick_live_status(live), (True, "Slot night"))
        self.assertEqual(kick_live_status({"livestream": {"is_live": False, "id": 9}}), (False, ""))
        quiet = public_kick_detail(
            {"username": "classybeef", "slug": "classybeef", "live": False, "title": ""},
            follow=False,
        )
        self.assertNotIn("not live", quiet)
        shown = public_kick_detail(
            {"username": "classybeef", "slug": "classybeef", "live": True, "title": "Slot night"},
            follow=True,
        )
        self.assertIn("The channel is live: Slot night.", shown)
        self.assertIn("classybeef", shown)
        waiting = public_kick_detail(
            {"username": "xqc", "slug": "xqc", "live": False, "title": ""},
            follow=True,
        )
        self.assertIn("The channel is not live. Chat stays connected.", waiting)

    def test_realtime_negotiation_accepts_the_public_sockets_only(self):
        provider, host, path = realtime_endpoint({
            "data": {
                "mode": "websocket",
                "connections": [{
                    "provider": "centrifugo",
                    "credentials": {
                        "url": "wss://realtime.us-east-1.platform.kick.com/connection/websocket",
                        "token": "do-not-keep",
                    },
                }],
            }
        })
        self.assertEqual(
            (provider, host, path),
            ("centrifugo", "realtime.us-east-1.platform.kick.com", "/connection/websocket"),
        )
        provider, host, path = realtime_endpoint({
            "data": {
                "mode": "websocket",
                "connections": [
                    {"provider": "centrifugo", "credentials": {"url": "wss://evil.example/connection/websocket"}},
                    {"provider": "pusher", "credentials": {"app_key": "abc", "cluster": "us2"}},
                ],
            }
        })
        self.assertEqual(provider, "pusher")
        self.assertEqual(host, "ws-us2.pusher.com")
        self.assertTrue(path.startswith("/app/abc?"))
        with self.assertRaises(ApiError):
            realtime_endpoint({"data": {"mode": "websocket", "connections": []}})
        source = Path(__file__).resolve().parents[1] / "streamdonations" / "kick_public.py"
        self.assertNotIn("32cbd69e4b950bf97679", source.read_text(encoding="utf-8"))

    def test_centrifugo_events_keep_the_public_parsers(self):
        ready = interpret_centrifugo({
            "id": 1,
            "connect": {"expires": True, "ttl": 100, "ping": 25, "pong": True, "token": "do-not-keep"},
        })
        self.assertEqual(ready["op"], "connect")
        self.assertNotIn("token", json.dumps(ready))
        self.assertEqual(ready["result"]["ttl"], 100)
        self.assertEqual(interpret_centrifugo({}), {"op": "ping"})
        chat = interpret_centrifugo({
            "push": {
                "channel": "chatrooms.1.v2",
                "pub": {
                    "data": {
                        "event": "App\\Events\\ChatMessageEvent",
                        "data": {"id": "m1", "content": "hello", "sender": {"username": "fan"}},
                    }
                },
            }
        })
        self.assertEqual(chat["op"], "event")
        self.assertEqual(
            donations_from_kick_public(chat["event"], chat["data"], "creator"),
            [],
        )
        kicks = interpret_centrifugo({
            "push": {
                "pub": {
                    "data": json.dumps({
                        "event": "KicksGifted",
                        "data": {
                            "gift_transaction_id": "tx-1",
                            "message": "w",
                            "sender": {"id": 9, "username": "fan"},
                            "gift": {"name": "Rage Quit", "amount": 500},
                        },
                    })
                }
            }
        })
        donation = donations_from_kick_public(kicks["event"], kicks["data"], "creator")[0]
        self.assertEqual(donation.kind, "kicks")
        self.assertEqual(donation.amount_value, 500)
        self.assertEqual(
            interpret_pusher({"event": "pusher:error", "data": "{\"code\":4001}"})["op"],
            "error",
        )
        self.assertEqual(
            interpret_pusher({"event": "pusher:connection_established", "data": "{}"})["op"],
            "ready",
        )


def _server_frame(text: str) -> bytes:
    """One unmasked text frame, as a server sends it."""
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) < 65536:
        header.append(126)
        header += len(payload).to_bytes(2, "big")
    else:
        header.append(127)
        header += len(payload).to_bytes(8, "big")
    return bytes(header) + payload


def _client_texts(sock: socket.socket) -> list[str]:
    """Every masked text frame the client has written so far."""
    sock.setblocking(False)
    data = b""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    except BlockingIOError:
        pass
    finally:
        sock.setblocking(True)
    return _decode_client_frames(data)


def _decode_client_frames(data: bytes) -> list[str]:
    texts = []
    while data:
        length = data[1] & 0x7F
        offset = 2
        if length == 126:
            length = int.from_bytes(data[2:4], "big")
            offset = 4
        elif length == 127:
            length = int.from_bytes(data[2:10], "big")
            offset = 10
        mask = data[offset:offset + 4]
        body = data[offset + 4:offset + 4 + length]
        texts.append(bytes(b ^ mask[i % 4] for i, b in enumerate(body)).decode("utf-8"))
        data = data[offset + 4 + length:]
    return texts


def _established(activity=None) -> str:
    data = {"socket_id": "1.2"}
    if activity is not None:
        data["activity_timeout"] = activity
    return json.dumps({"event": "pusher:connection_established", "data": json.dumps(data)})


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Ledger:
    def __init__(self, settings=None):
        self.settings = {"kick_watch": "12345", "kick_follow_live": "0"}
        self.settings.update(settings or {})
        self.statuses: list[tuple[str, str, str]] = []
        self.logs: list[str] = []
        self.paused = False

    def get_setting(self, key, default=""):
        return self.settings.get(key, default)

    def collection_paused(self):
        return self.paused

    def set_status(self, platform, state, detail, error=None):
        self.statuses.append((platform, state, detail))

    def log(self, _platform, _level, message):
        self.logs.append(message)

    def add_many(self, _rows):
        return 0


class _Quiet:
    """Stands in for a socket session in status checks."""

    def __init__(self, responsive: bool):
        self._connected = True
        self._responsive = responsive

    def responsive(self) -> bool:
        return self._responsive


class KickSilentSocketTests(unittest.TestCase):
    """A socket that goes silent without closing, as after a network change."""

    def setUp(self):
        self.clock = _Clock()
        patches = [
            mock.patch.object(kick_public, "_now", self.clock),
            mock.patch.object(kick_public, "_MAX_WAIT", 0.05),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.client, self.server = socket.socketpair()
        self.addCleanup(self.client.close)
        self.addCleanup(self.server.close)

    def _session(self, provider: str, first: str) -> kick_public._PublicSocket:
        session = kick_public._PublicSocket(transport=None, channel_id=1)
        session.provider = provider
        session.sock = self.client
        session._buf = kick_public._SocketBuffer(self.client)
        self.server.sendall(_server_frame(first))
        session._pump_until_connected()
        return session

    def test_pusher_pings_after_activity_timeout_and_drops_an_unanswered_socket(self):
        session = self._session("pusher", _established(20))
        self.assertEqual(session._activity, 20.0)
        self.clock.now += 19
        self.assertIsNone(session.read_event())
        self.assertEqual(_client_texts(self.server), [])
        self.clock.now += 1
        self.assertIsNone(session.read_event())
        self.assertEqual(
            [json.loads(text)["event"] for text in _client_texts(self.server)],
            ["pusher:ping"],
        )
        self.assertTrue(session.responsive())
        self.clock.now += kick_public._PUSHER_PONG_SLOW
        self.assertFalse(session.responsive())
        self.clock.now += kick_public._PUSHER_PONG_TIMEOUT - kick_public._PUSHER_PONG_SLOW - 0.1
        self.assertIsNone(session.read_event())
        self.clock.now += 0.1
        with self.assertRaises(ApiError) as caught:
            session.read_event()
        self.assertEqual(str(caught.exception), kick_public.STALE_MESSAGE)

    def test_pusher_pong_keeps_the_socket(self):
        session = self._session("pusher", _established(20))
        self.clock.now += 20
        self.assertIsNone(session.read_event())
        self.assertEqual(len(_client_texts(self.server)), 1)
        self.server.sendall(_server_frame(json.dumps({"event": "pusher:pong", "data": {}})))
        self.clock.now += 1
        self.assertIsNone(session.read_event())
        self.clock.now += 25
        self.assertTrue(session.responsive())
        self.assertIsNone(session.read_event())
        self.clock.now += kick_public._PUSHER_PONG_TIMEOUT - 1
        self.assertIsNone(session.read_event())
        self.assertEqual(
            [json.loads(text)["event"] for text in _client_texts(self.server)],
            ["pusher:ping"],
        )

    def test_pusher_activity_timeout_is_clamped(self):
        self.assertEqual(interpret_pusher(json.loads(_established(120)))["activity_timeout"], 120.0)
        self.assertNotIn("activity_timeout", interpret_pusher(json.loads(_established())))
        self.assertNotIn("activity_timeout", interpret_pusher({"event": "pusher:connection_established", "data": "x"}))
        self.assertEqual(self._session("pusher", _established(1))._activity, kick_public._PUSHER_ACTIVITY_MIN)

    def test_pusher_activity_timeout_has_a_ceiling_and_a_default(self):
        self.assertEqual(self._session("pusher", _established(9999))._activity, kick_public._PUSHER_ACTIVITY_MAX)
        other_client, other_server = socket.socketpair()
        self.addCleanup(other_client.close)
        self.addCleanup(other_server.close)
        self.client, self.server = other_client, other_server
        self.assertEqual(self._session("pusher", _established())._activity, kick_public._PUSHER_ACTIVITY_DEFAULT)

    def test_centrifugo_silence_past_the_server_ping_is_fatal(self):
        session = self._session("centrifugo", json.dumps({"id": 1, "connect": {"ping": 25, "pong": True}}))
        self.assertEqual(session._server_ping, 25.0)
        self.clock.now += 30
        self.server.sendall(_server_frame("{}"))
        self.assertIsNone(session.read_event())
        self.assertEqual(_client_texts(self.server), ["{}"])
        self.clock.now += 25 + kick_public._CENTRIFUGO_PING_GRACE - 0.1
        self.assertTrue(session.responsive())
        self.assertIsNone(session.read_event())
        self.clock.now += 0.1
        self.assertFalse(session.responsive())
        with self.assertRaises(ApiError):
            session.read_event()

    def test_silence_hits_the_idle_cap_without_a_provider_promise(self):
        session = self._session("centrifugo", json.dumps({"id": 1, "connect": {}}))
        self.clock.now += kick_public._IDLE_CAP - 1
        self.assertIsNone(session.read_event())
        self.assertTrue(session.responsive())
        self.clock.now += 1
        self.assertFalse(session.responsive())
        with self.assertRaises(ApiError):
            session.read_event()

    def test_a_frame_split_across_a_read_timeout_stays_in_step(self):
        session = self._session("pusher", _established(120))
        body = json.dumps({"event": "App\\Events\\ChatMessageEvent", "data": {"content": "x" * 300}})
        frame = _server_frame(body)
        self.server.sendall(frame[:5])
        self.assertIsNone(session.read_event())
        self.server.sendall(frame[5:] + _server_frame(json.dumps({"event": "second", "data": {}})))
        self.assertEqual(json.loads(session.read_event())["data"]["content"], "x" * 300)
        self.assertEqual(json.loads(session.read_event())["event"], "second")


class KickKeepaliveTests(unittest.TestCase):
    class _Recorder:
        def __init__(self, fail=False, ioctl=False):
            self.options = []
            self.ioctls = []
            self.fail = fail
            if ioctl:
                self.ioctl = lambda code, value: self.ioctls.append((code, value))

        def setsockopt(self, level, option, value):
            if self.fail:
                raise OSError("not supported")
            self.options.append((level, option, value))

    def test_keepalive_on_this_os(self):
        sock = self._Recorder(ioctl=hasattr(socket, "SIO_KEEPALIVE_VALS"))
        kick_public._keepalive(sock)
        self.assertIn((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1), sock.options)
        if hasattr(socket, "SIO_KEEPALIVE_VALS"):
            self.assertEqual(sock.ioctls, [(socket.SIO_KEEPALIVE_VALS, (1, 30000, 10000))])
        if hasattr(socket, "TCP_KEEPIDLE") and not hasattr(socket, "SIO_KEEPALIVE_VALS"):
            self.assertIn((socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30), sock.options)

    def test_windows_keepalive_values(self):
        sock = self._Recorder(ioctl=True)
        with mock.patch.object(socket, "SIO_KEEPALIVE_VALS", 0x98000004, create=True):
            kick_public._keepalive(sock)
        self.assertEqual(sock.ioctls, [(0x98000004, (1, 30000, 10000))])
        self.assertEqual(sock.options, [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)])

    def test_keepalive_never_breaks_the_connection(self):
        kick_public._keepalive(self._Recorder(fail=True))
        real = socket.socket()
        try:
            kick_public._keepalive(real)
            self.assertNotEqual(real.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE), 0)
        finally:
            real.close()


class KickReconnectLoopTests(unittest.TestCase):
    def setUp(self):
        self.opens = 0
        self.sent: dict[int, bytes] = {}
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.addCleanup(self.listener.close)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()
        patches = [
            mock.patch.object(kick_public, "_open_websocket", self._open),
            mock.patch.object(kick_public._PublicSocket, "_negotiate", lambda _self: [("pusher", "x", "/")]),
            mock.patch.object(kick_public, "_MAX_WAIT", 0.05),
            mock.patch.object(kick_public, "_PUSHER_ACTIVITY_MIN", 0.05),
            mock.patch.object(kick_public, "_PUSHER_PONG_TIMEOUT", 0.2),
            mock.patch.object(kick_public, "_IDLE_CAP", 0.5),
            mock.patch.object(kick_public, "_BACKOFF_START", 0.05),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _serve(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            self.opens += 1
            threading.Thread(target=self._handle, args=(conn, self.opens), daemon=True).start()

    def _handle(self, conn, number):
        # Connects, then goes silent without closing, like a dead network path.
        with conn:
            conn.sendall(_server_frame(_established(0.1)))
            while True:
                try:
                    data = conn.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                self.sent[number] = self.sent.get(number, b"") + data

    def _open(self, _host, _path):
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        return raw, kick_public._SocketBuffer(raw)

    def test_a_silent_socket_reconnects_and_reports_it(self):
        store = _Ledger()
        chat = PublicKickChat(store, transport=None)
        self.addCleanup(chat.rest)
        chat.ensure()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            states = [(state, detail) for _platform, state, detail in store.statuses]
            stale = ("error", kick_public.STALE_MESSAGE)
            if self.opens >= 2 and stale in states and states.index(stale) < len(states) - 1:
                break
            time.sleep(0.02)
        states = [(state, detail) for _platform, state, detail in store.statuses]
        self.assertGreaterEqual(self.opens, 2)
        self.assertIn(("error", kick_public.STALE_MESSAGE), states)
        after = states[states.index(("error", kick_public.STALE_MESSAGE)) + 1:]
        self.assertIn("ok", [state for state, _detail in after])
        self.assertIn(kick_public.STALE_MESSAGE, store.logs)
        first = [json.loads(text).get("event") for text in _decode_client_frames(self.sent.get(1, b""))]
        self.assertIn("pusher:subscribe", first)
        self.assertIn("pusher:ping", first)


class KickStaleStatusTests(unittest.TestCase):
    def _transport(self):
        body = json.dumps({"id": 7, "slug": "creator", "chatroom": {"id": 8}, "user": {"username": "creator"}}).encode()

        class _Transport:
            def request(self, _method, url, **_kwargs):
                return HttpResult(200, {}, body, url)

        return _Transport()

    def test_refresh_live_does_not_report_ok_while_the_socket_is_quiet(self):
        store = _Ledger()
        chat = PublicKickChat(store, self._transport())
        info = {"chatroom_id": 8, "channel_id": 7}
        self.assertIsNotNone(chat._refresh_live("creator", info, _Quiet(False)))
        self.assertEqual(store.statuses, [])
        chat._refresh_live("creator", info, _Quiet(True))
        self.assertEqual([state for _p, state, _d in store.statuses], ["ok"])

    def test_public_status_reports_a_quiet_socket(self):
        store = _Ledger()
        client = KickClient(store, self._transport())
        client._public.detail = "Public chat for creator."
        client._public._session = _Quiet(False)
        client._report_public()
        self.assertEqual(store.statuses[-1], ("kick", "error", kick_public.STALE_MESSAGE))
        client._public._session = _Quiet(True)
        client._report_public()
        self.assertEqual(store.statuses[-1], ("kick", "ok", "Public chat for creator."))

    def test_webhook_status_does_not_hide_a_quiet_chat_socket(self):
        store = _Ledger({
            "kick_client_id": "id",
            "kick_client_secret": "secret",
            "kick_refresh_token": "refresh",
        })
        client = KickClient(store, self._transport())
        client._public.detail = "Public chat for creator."
        client._public._session = _Quiet(False)
        with (
            mock.patch.object(client._public, "ensure"),
            mock.patch.object(client, "refresh_public_key"),
            mock.patch.object(client, "subscription_names", return_value=["kicks.gifted"]),
        ):
            client.tick(force=True)
        platform, state, detail = store.statuses[-1]
        self.assertEqual(state, "error")
        self.assertIn("Public chat: " + kick_public.STALE_MESSAGE, detail)
        self.assertIn("Subscribed to kicks.gifted", detail)


if __name__ == "__main__":
    unittest.main()
