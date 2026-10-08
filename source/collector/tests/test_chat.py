"""Read-only chat lines for the stream panel. No network and no ledger rows."""

import json
import threading
import unittest

from streamdonations.chat_feed import ChatFeed, chat_time, clean_chat_text, remember_chat
from streamdonations.kick_public import (
    PublicKickChat,
    chat_lines_from_kick_public,
    donations_from_kick_public,
)
from streamdonations.rumble import RumbleClient, chat_lines_from_chat_event, donations_from_chat_event
from streamdonations.transport import Transport
from streamdonations.youtube_public import (
    PublicChatFollower,
    chat_lines_from_live_chat_actions,
    donations_from_live_chat_actions,
)


def _actions():
    return [
        {
            "addChatItemAction": {
                "item": {
                    "liveChatTextMessageRenderer": {
                        "id": "plain",
                        "timestampUsec": "1700000000000000",
                        "authorName": {"simpleText": "Viewer"},
                        "message": {"runs": [{"text": "hello"}]},
                    }
                }
            }
        },
        {
            "addChatItemAction": {
                "item": {
                    "liveChatPaidMessageRenderer": {
                        "id": "paid-1",
                        "timestampUsec": "1700000005000000",
                        "authorName": {"simpleText": "Fan"},
                        "purchaseAmountText": {"simpleText": "$5.00"},
                        "message": {"runs": [{"text": "love the show"}]},
                    }
                }
            }
        },
        {
            "addChatItemAction": {
                "item": {
                    "liveChatPaidStickerRenderer": {
                        "id": "sticker-1",
                        "authorName": {"simpleText": "Fan"},
                        "purchaseAmountText": {"simpleText": "$2.00"},
                        "sticker": {"accessibility": {"accessibilityData": {"label": "fire"}}},
                    }
                }
            }
        },
    ]


def _rumble_event():
    return {
        "type": "init",
        "data": {
            "users": [{"id": "7", "username": "ChadFan"}],
            "messages": [
                {
                    "id": "100",
                    "time": "2026-09-25T11:04:30+00:00",
                    "user_id": "7",
                    "text": "go get a coffee",
                    "type": "rant",
                    "rant": {"price_cents": 1000},
                },
                {
                    "id": "101",
                    "time": "2026-09-25T11:07:16+00:00",
                    "user_id": "7",
                    "text": "just chatting",
                    "type": "regular",
                },
                {"id": "102", "user_id": "7", "text": "", "gift_purchase_notification": {"total_gifts": 2}},
                {
                    "id": "103",
                    "user_id": "7",
                    "text": "",
                    "notification": {"text": "is now a monthly supporter!", "badge": "subscription"},
                },
            ],
        },
    }


class _Ledger:
    def __init__(self):
        self.rows = []
        self.logs = []
        self.values = {}
        self.paused = False

    def collection_paused(self):
        return self.paused

    def get_setting(self, key, default=""):
        return self.values.get(key, default)

    def add_many(self, donations):
        self.rows.extend(donations)
        return len(donations)

    def log(self, *parts):
        self.logs.append(" ".join(str(part) for part in parts))

    def set_status(self, *args, **kwargs):
        return None


class _Boom:
    def add(self, _platform, rows):
        raise RuntimeError(rows[0]["text"])


class ChatFeedTests(unittest.TestCase):
    def test_chat_time_reads_platform_clocks(self):
        self.assertEqual(chat_time("2026-09-25T11:04:30+00:00"), "2026-09-25T11:04:30Z")
        self.assertEqual(chat_time("1700000000000000"), "2023-11-14T22:13:20Z")
        self.assertEqual(chat_time(1700000000), "2023-11-14T22:13:20Z")
        self.assertEqual(chat_time(1700000000000), "2023-11-14T22:13:20Z")
        self.assertEqual(chat_time(""), "")
        self.assertEqual(chat_time("not a time"), "")
        feed = ChatFeed()
        feed.add("kick", [{"key": "d", "author": "Pat", "text": "stamped", "time": "2026-09-25T11:04:30+00:00"}])
        self.assertEqual(feed.since(0, 0, False)["lines"][0]["time"], "2026-09-25T11:04:30Z")

    def test_late_poll_stays_in_clock_order(self):
        feed = ChatFeed()
        heard = []
        feed.on_lines = heard.extend
        feed.add("rumble", [{"key": "r", "author": "A", "text": "rumble", "time": "2026-10-05T21:57:53Z"}])
        cursor = feed.since(0, 0, False)["cursor"]
        feed.add("youtube", [{"key": "y", "author": "B", "text": "youtube", "time": "2026-10-05T21:57:47Z"}])
        feed.add("kick", [{"key": "k", "author": "C", "text": "kick", "time": "2026-10-05T21:57:59Z"}])
        ordered = feed.since(0, 0, False)
        self.assertEqual([line["text"] for line in ordered["lines"]], ["youtube", "rumble", "kick"])
        fresh = feed.since(cursor, ordered["generation"], False)
        self.assertEqual([line["text"] for line in fresh["lines"]], ["youtube", "kick"])
        self.assertEqual([line["text"] for line in heard], ["rumble", "youtube", "kick"])

        same = ChatFeed()
        same.add("youtube", [{"key": "a", "author": "A", "text": "first", "time": "2026-10-05T21:57:59Z"}])
        same.add("kick", [{"key": "b", "author": "B", "text": "second", "time": "2026-10-05T21:57:59Z"}])
        self.assertEqual(
            [line["text"] for line in same.since(0, 0, False)["lines"]],
            ["first", "second"],
        )

        short = ChatFeed(limit=2)
        short.add("rumble", [{"key": "n", "author": "A", "text": "mid", "time": "2026-10-05T21:57:53Z"}])
        short.add("kick", [{"key": "z", "author": "B", "text": "new", "time": "2026-10-05T21:57:59Z"}])
        short.add("youtube", [{"key": "o", "author": "C", "text": "old", "time": "2026-10-05T21:57:47Z"}])
        self.assertEqual([line["text"] for line in short.since(0, 0, False)["lines"]], ["mid", "new"])

    def test_feed_keeps_a_short_buffer_and_drops_one_platform(self):
        self.assertEqual(clean_chat_text(" hi\x00 there ", 20), "hi there")
        feed = ChatFeed(limit=2)
        feed.add("twitch", [{"key": "x", "author": "A", "text": "nope"}])
        feed.add("youtube", [{"key": "a", "author": "  ", "text": "one"}])
        feed.add("youtube", [{"key": "a", "author": "Viewer", "text": "one again"}])
        feed.add("rumble", [{"key": "b", "author": "Fan", "text": "two"}])
        feed.add("kick", [{"key": "c", "author": "Pat", "text": "three"}])
        view = feed.since(0, 0, False)
        self.assertEqual([line["text"] for line in view["lines"]], ["two", "three"])
        self.assertEqual(view["lines"][0]["author"], "Fan")
        self.assertNotIn("key", view["lines"][0])
        self.assertRegex(view["lines"][0]["time"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        feed.add("youtube", [{"key": "a", "author": "Viewer", "text": "one returns"}])
        self.assertEqual(feed.since(view["cursor"], view["generation"], False)["lines"][0]["text"], "one returns")
        generation = feed.since(0, 0, False)["generation"]
        feed.clear("rumble")
        again = feed.since(0, generation, True)
        self.assertNotEqual(again["generation"], generation)
        self.assertTrue(again["paused"])
        self.assertNotIn("two", [line["text"] for line in again["lines"]])
        self.assertIn("three", [line["text"] for line in again["lines"]])
        held = again["generation"]
        feed.clear("twitch")
        self.assertEqual(feed.since(0, 0, False)["generation"], held)
        self.assertEqual(remember_chat(feed, "kick", "same", "same"), "same")
        self.assertEqual(feed.since(0, 0, False)["generation"], held)

    def test_youtube_plain_text_is_chat_and_not_a_donation(self):
        lines = chat_lines_from_live_chat_actions(_actions())
        self.assertEqual([line["text"] for line in lines], ["hello", "love the show"])
        self.assertEqual([line["time"] for line in lines], ["2023-11-14T22:13:20Z", "2023-11-14T22:13:25Z"])
        donations = donations_from_live_chat_actions(_actions(), "Creator")
        self.assertEqual([item.kind for item in donations], ["super_chat", "super_sticker"])
        self.assertNotIn("hello", [item.message for item in donations])

        store = _Ledger()
        feed = ChatFeed()
        follower = PublicChatFollower(store, Transport(), chat_feed=feed)
        follower._store(_actions(), "Creator", threading.Event())
        self.assertEqual([line["text"] for line in feed.since(0, 0, False)["lines"]], ["hello", "love the show"])
        self.assertEqual([item.message for item in store.rows], ["love the show", "fire"])
        self.assertNotIn("hello", " ".join(store.logs))

        store.paused = True
        follower._store(_actions(), "Creator", threading.Event())
        self.assertEqual(len(feed.since(0, 0, False)["lines"]), 2)
        self.assertEqual(len(store.rows), 2)

        broken = _Ledger()
        follower = PublicChatFollower(broken, Transport(), chat_feed=_Boom())
        follower._store(_actions(), "Creator", threading.Event())
        self.assertEqual([item.kind for item in broken.rows], ["super_chat", "super_sticker"])
        self.assertNotIn("hello", " ".join(broken.logs))

    def test_youtube_chat_clears_only_when_the_target_changes(self):
        store = _Ledger()
        feed = ChatFeed()
        follower = PublicChatFollower(store, Transport(), chat_feed=feed)
        follower._chat_for = "watch\nold-video"
        feed.add("youtube", [{"key": "a", "author": "A", "text": "old line"}])
        feed.add("kick", [{"key": "b", "author": "B", "text": "kick stays"}])
        follower.ensure()
        texts = [line["text"] for line in feed.since(0, -1, False)["lines"]]
        self.assertEqual(texts, ["kick stays"])
        self.assertEqual(follower._chat_for, "")
        self.assertIsNone(follower._thread)
        generation = feed.since(0, 0, False)["generation"]
        follower.ensure()
        self.assertEqual(feed.since(0, 0, False)["generation"], generation)
        follower._chat_for = "watch\nstill"
        feed.add("youtube", [{"key": "c", "author": "A", "text": "stay"}])
        follower.rest()
        self.assertEqual(follower._chat_for, "watch\nstill")
        self.assertEqual(feed.since(0, -1, False)["lines"][-1]["text"], "stay")

    def test_rumble_regular_and_rant_text_are_chat(self):
        event = _rumble_event()
        lines = chat_lines_from_chat_event(event)
        self.assertEqual([line["text"] for line in lines], ["go get a coffee", "just chatting"])
        self.assertEqual([line["author"] for line in lines], ["ChadFan", "ChadFan"])
        self.assertEqual([line["time"] for line in lines], ["2026-09-25T11:04:30Z", "2026-09-25T11:07:16Z"])
        donations = donations_from_chat_event(event)
        self.assertEqual([item.kind for item in donations], ["rant", "gifted_subscriptions", "subscription"])
        self.assertNotIn("just chatting", [item.message for item in donations])

        store = _Ledger()
        feed = ChatFeed()
        client = RumbleClient(store, Transport(), chat_feed=feed)
        client._consume_sse_line(b"data: " + json.dumps(event).encode(), threading.Event())
        self.assertEqual(
            [line["text"] for line in feed.since(0, 0, False)["lines"]],
            ["go get a coffee", "just chatting"],
        )
        self.assertNotIn("just chatting", " ".join(store.logs))
        client._chat_for = "watch\nold"
        feed.add("rumble", [{"key": "z", "author": "A", "text": "gone soon"}])
        client._ensure_chat()
        self.assertNotIn("gone soon", [line["text"] for line in feed.since(0, -1, False)["lines"]])
        self.assertIsNone(client._chat_thread)

    def test_kick_hello_is_chat_and_not_a_donation(self):
        self.assertEqual(
            donations_from_kick_public("App\\Events\\ChatMessageEvent", {"content": "hello"}, "creator"),
            [],
        )
        lines = chat_lines_from_kick_public(
            "App\\Events\\ChatMessageEvent",
            {"id": "m1", "content": "hello", "sender": {"username": "fan"}},
        )
        self.assertEqual(lines, [{"key": "m1", "author": "fan", "text": "hello", "time": ""}])
        stamped = chat_lines_from_kick_public(
            "App\\Events\\ChatMessageEvent",
            {"id": "m2", "content": "later", "created_at": 1700000000, "sender": {"username": "fan"}},
        )
        self.assertEqual(stamped[0]["time"], "2023-11-14T22:13:20Z")
        store = _Ledger()
        feed = ChatFeed()
        chat = PublicKickChat(store, Transport(), chat_feed=feed)
        stop = threading.Event()
        chat._on_message(
            None,
            json.dumps({
                "event": "App\\Events\\ChatMessageEvent",
                "data": {"id": "m1", "content": "hello", "sender": {"username": "fan"}},
            }).encode(),
            "creator",
            stop,
        )
        chat._on_message(
            None,
            json.dumps({
                "event": "KicksGifted",
                "data": {
                    "gift_transaction_id": "tx-1",
                    "sender": {"username": "fan"},
                    "gift": {"name": "Rage Quit", "amount": 500},
                },
            }).encode(),
            "creator",
            stop,
        )
        self.assertEqual([line["text"] for line in feed.since(0, 0, False)["lines"]], ["hello"])
        self.assertEqual([item.kind for item in store.rows], ["kicks"])
        self.assertNotIn("hello", " ".join(store.logs))
        chat._chat_for = "0\nhttps://kick.com/someone"
        feed.add("kick", [{"key": "z", "author": "A", "text": "old kick"}])
        chat.ensure()
        self.assertNotIn("old kick", [line["text"] for line in feed.since(0, -1, False)["lines"]])
        self.assertIsNone(chat._thread)


if __name__ == "__main__":
    unittest.main()
