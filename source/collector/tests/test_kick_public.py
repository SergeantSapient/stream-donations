"""Public Kick chat parsing. No network and no channel ownership."""

import json
import unittest
from pathlib import Path

from streamdonations.kick_public import (
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


if __name__ == "__main__":
    unittest.main()
