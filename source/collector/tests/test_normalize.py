"""Normalization of the official donation payloads."""

import unittest

from streamdonations.normalize import (
    normalize_kick_event,
    normalize_live_chat_message,
    normalize_super_chat_event,
    rumble_donations,
)
from streamdonations.rumble import (
    chat_id_from_html,
    live_chat_id_from_channel_html,
    stream_title_from_channel_html,
    rumble_channel_page_url,
    validate_rumble_channel,
    donations_from_chat_event,
    embed_to_chat_id,
    validate_rumble_api_url,
    validate_rumble_watch,
)


SUPER_CHAT = {
    "id": "event-1",
    "snippet": {
        "channelId": "UCbroadcaster",
        "supporterDetails": {"channelId": "UCfan", "displayName": "Fan"},
        "commentText": "hello from chat",
        "createdAt": "2024-01-02T03:04:05Z",
        "amountMicros": "2500000",
        "currency": "USD",
        "isSuperStickerEvent": False,
    },
}

RUMBLE = {
    "username": "Creator",
    "followers": {
        "num_followers": 2,
        "recent_followers": [{"username": "Watcher", "followed_on": "2023-09-17T20:43:56-04:00"}],
    },
    "subscribers": {
        "num_subscribers": 1,
        "latest_subscriber": {
            "username": "Sub",
            "amount_cents": 500,
            "amount_dollars": 5,
            "subscribed_on": "2023-09-15T21:03:05+00:00",
        },
        "recent_subscribers": [
            {
                "username": "Sub",
                "amount_cents": 500,
                "amount_dollars": 5,
                "subscribed_on": "2023-09-15T21:03:05+00:00",
            }
        ],
    },
    "latest_gifted_sub": {
        "purchased_by": "Gifter",
        "total_gifts": 5,
        "gift_type": "channel_subscription",
        "remaining_gifts": 2,
        "video_id": 99,
    },
    "recent_gifted_subs": [
        {
            "purchased_by": "Gifter",
            "total_gifts": 5,
            "gift_type": "channel_subscription",
            "remaining_gifts": 0,
            "video_id": 99,
        }
    ],
    "livestreams": [
        {
            "id": "abc",
            "chat": {
                "recent_messages": [
                    {"username": "Chatter", "text": "not a donation", "created_on": "2023-09-18T17:51:08+00:00"}
                ],
                "latest_rant": {
                    "username": "Ranter",
                    "text": "play the song",
                    "created_on": "2023-09-18T17:51:37+00:00",
                    "expires_on": "2023-09-18T17:53:37+00:00",
                    "amount_cents": 100,
                    "amount_dollars": 1,
                },
                "recent_rants": [
                    {
                        "username": "Ranter",
                        "text": "play the song",
                        "created_on": "2023-09-18T17:51:37+00:00",
                        "expires_on": "2023-09-18T17:53:37+00:00",
                        "amount_cents": 100,
                        "amount_dollars": 1,
                    }
                ],
            },
        }
    ],
}


class NormalizeTests(unittest.TestCase):
    def test_super_chat_amount_and_message(self):
        donation = normalize_super_chat_event(SUPER_CHAT)
        self.assertEqual(donation.kind, "super_chat")
        self.assertEqual(donation.author_name, "Fan")
        self.assertEqual(donation.message, "hello from chat")
        self.assertEqual(donation.amount_micros, 2_500_000)
        self.assertEqual(donation.amount_value, 2.5)
        self.assertEqual(donation.currency, "USD")
        self.assertEqual(donation.created_at, "2024-01-02T03:04:05Z")
        self.assertEqual(donation.dedupe_key, "youtube:superChatEvent:event-1")

    def test_super_sticker_uses_alt_text(self):
        item = {
            "id": "sticker-1",
            "snippet": {
                "channelId": "UCbroadcaster",
                "supporterDetails": {"displayName": "Fan", "channelId": "UCfan"},
                "commentText": "",
                "createdAt": "2024-01-02T03:04:05Z",
                "amountMicros": 1000000,
                "currency": "USD",
                "isSuperStickerEvent": True,
                "superStickerMetadata": {"stickerId": "s", "altText": "fire"},
            },
        }
        donation = normalize_super_chat_event(item)
        self.assertEqual(donation.kind, "super_sticker")
        self.assertEqual(donation.message, "fire")
        self.assertEqual(donation.amount_value, 1)

    def test_live_chat_ignores_plain_text_and_super_chats(self):
        self.assertIsNone(
            normalize_live_chat_message(
                {"id": "m", "snippet": {"type": "textMessageEvent", "displayMessage": "hi"}}
            )
        )
        self.assertIsNone(
            normalize_live_chat_message(
                {
                    "id": "m2",
                    "snippet": {
                        "type": "superChatEvent",
                        "superChatDetails": {"amountMicros": "1000000", "userComment": "hi"},
                    },
                }
            )
        )

    def test_new_member_and_gift(self):
        member = normalize_live_chat_message(
            {
                "id": "mem-1",
                "snippet": {
                    "type": "newSponsorEvent",
                    "publishedAt": "2024-05-01T00:00:00Z",
                    "newSponsorDetails": {"memberLevelName": "Gold", "isUpgrade": False},
                },
                "authorDetails": {"displayName": "Member", "channelId": "UCmem"},
            },
            channel_id="UCbroadcaster",
        )
        self.assertEqual(member.kind, "membership")
        self.assertEqual(member.message, "New member · Gold")
        self.assertEqual(member.channel, "UCbroadcaster")
        self.assertEqual(member.author_name, "Member")

        gift = normalize_live_chat_message(
            {
                "id": "gift-1",
                "snippet": {
                    "type": "membershipGiftingEvent",
                    "publishedAt": "2024-05-01T00:00:00Z",
                    "membershipGiftingDetails": {
                        "giftMembershipsCount": 3,
                        "giftMembershipsLevelName": "Gold",
                    },
                },
                "authorDetails": {"displayName": "Patron"},
            }
        )
        self.assertEqual(gift.kind, "membership_gift")
        self.assertEqual(gift.message, "Gifted 3 memberships (Gold)")

    def test_rumble_keeps_paid_events_only_once(self):
        donations = rumble_donations(RUMBLE)
        kinds = sorted(item.kind for item in donations)
        self.assertEqual(kinds, ["gifted_subscriptions", "rant", "subscription"])
        rant = next(item for item in donations if item.kind == "rant")
        self.assertEqual(rant.message, "play the song")
        self.assertEqual(rant.amount_value, 1)
        self.assertEqual(rant.currency, "USD")
        self.assertEqual(rant.channel, "Creator")
        sub = next(item for item in donations if item.kind == "subscription")
        self.assertEqual(sub.amount_value, 5)
        gift = next(item for item in donations if item.kind == "gifted_subscriptions")
        self.assertEqual(gift.author_name, "Gifter")
        self.assertIn("5", gift.message)
        changed = dict(RUMBLE)
        changed["recent_gifted_subs"] = [
            {
                "purchased_by": "Gifter",
                "total_gifts": 5,
                "gift_type": "channel_subscription",
                "remaining_gifts": 1,
                "video_id": 99,
            }
        ]
        again = rumble_donations(changed)
        self.assertEqual(
            {item.dedupe_key for item in donations},
            {item.dedupe_key for item in again},
        )

    def test_kick_events(self):
        kicks = normalize_kick_event(
            "kicks.gifted",
            "01MSG",
            {
                "broadcaster": {"channel_slug": "creator", "username": "creator"},
                "sender": {"user_id": 9, "username": "fan"},
                "gift": {"amount": 500, "name": "Rage Quit", "message": "w"},
                "created_at": "2025-10-20T04:00:08.634Z",
            },
        )
        self.assertEqual(kicks.kind, "kicks")
        self.assertEqual(kicks.message, "Rage Quit: w")
        self.assertEqual(kicks.amount_value, 500)
        self.assertEqual(kicks.currency, "KICKS")
        self.assertEqual(kicks.dedupe_key, "kick:kicks.gifted:01MSG")
        self.assertEqual(kicks.created_at, "2025-10-20T04:00:08Z")

        renewed = normalize_kick_event(
            "channel.subscription.renewal",
            "01SUB",
            {
                "subscriber": {"username": "fan", "user_id": 3},
                "duration": 3,
                "created_at": "2025-01-14T16:08:06Z",
                "broadcaster": {"channel_slug": "creator"},
            },
        )
        self.assertEqual(renewed.kind, "subscription_renewal")
        self.assertEqual(renewed.message, "Renewed (3 months)")

        gifted = normalize_kick_event(
            "channel.subscription.gifts",
            "01GIFT",
            {
                "gifter": {"is_anonymous": True},
                "giftees": [{"username": "a"}, {"username": "b"}],
                "created_at": "2025-01-14T16:08:06Z",
                "broadcaster": {"channel_slug": "creator"},
            },
        )
        self.assertEqual(gifted.author_name, "Anonymous")
        self.assertEqual(gifted.message, "Gifted 2 subscriptions to a, b")
        self.assertIsNone(
            normalize_kick_event("channel.followed", "01F", {"follower": {"username": "x"}})
        )

    def test_public_chat_rant_does_not_require_the_owner_api(self):
        self.assertEqual(embed_to_chat_id("v7ds6x0"), "446413572")
        event = {
            "type": "init",
            "data": {
                "chat": {"id": "446413572"},
                "users": [{"id": "285482160", "username": "ChadFan"}],
                "channels": [{"id": "1", "username": "Creator"}],
                "messages": [
                    {
                        "id": "100",
                        "time": "2026-09-25T11:04:30+00:00",
                        "user_id": "285482160",
                        "text": "go get a coffee",
                        "type": "rant",
                        "rant": {"price_cents": 1000, "duration": 600},
                    },
                    {
                        "id": "101",
                        "time": "2026-09-25T11:07:16+00:00",
                        "user_id": "9",
                        "text": "just chatting",
                        "type": "regular",
                    },
                    {
                        "id": "102",
                        "time": "2026-09-25T11:08:00+00:00",
                        "user_id": "285482160",
                        "text": "",
                        "gift_purchase_notification": {"gift_type": "premium", "total_gifts": 2},
                    },
                    {
                        "id": "103",
                        "time": "2026-09-25T11:09:00+00:00",
                        "user_id": "4",
                        "text": "",
                        "notification": {"text": "is now a monthly supporter!", "badge": "recurring_subscription"},
                    },
                ],
            },
        }
        donations = donations_from_chat_event(event)
        kinds = [item.kind for item in donations]
        self.assertEqual(kinds, ["rant", "gifted_subscriptions", "subscription"])
        rant = donations[0]
        self.assertEqual(rant.author_name, "ChadFan")
        self.assertEqual(rant.message, "go get a coffee")
        self.assertEqual(rant.amount_value, 10)
        self.assertEqual(rant.currency, "USD")
        self.assertEqual(rant.channel, "Creator")
        self.assertEqual(rant.dedupe_key, "rumble:chat:100")
        self.assertEqual(donations[1].message, "Gifted 2 subscriptions (premium)")
        self.assertEqual(donations[2].message, "is now a monthly supporter!")
        again = donations_from_chat_event(event)
        self.assertEqual([item.dedupe_key for item in again], [item.dedupe_key for item in donations])

    def test_watch_page_chat_id_and_rejected_directory(self):
        page = '<div data-video-id="446413572"></div><script>"video":"v7ds6x0"</script>'
        self.assertEqual(chat_id_from_html(page), "446413572")
        self.assertEqual(chat_id_from_html('<div data-video-id="9604851"></div>'), "9604851")
        directory = '<a data-video-id="1"></a><a data-video-id="2"></a>'
        with self.assertRaises(ValueError):
            chat_id_from_html(directory)
        self.assertEqual(validate_rumble_watch("446413572"), "446413572")
        self.assertEqual(
            validate_rumble_watch("https://rumble.com/v5abcde-sample-live-tv.html"),
            "https://rumble.com/v5abcde-sample-live-tv.html",
        )
        with self.assertRaises(ValueError):
            validate_rumble_watch("https://evil.example/watch")

    def test_channel_page_live_badge_is_the_chat_id(self):
        css = "<style>.videostream__status--live{background:red}</style>"
        duration = (
            '<div class="videostream thumbnail__grid--item" data-video-id="111">'
            '<div class="videostream__badge videostream__status videostream__status--duration">1:00</div></div>'
        )
        live = (
            '<div\nclass="videostream thumbnail__grid--item videostream--featured"\n'
            'data-video-id="222"\n>'
            '<div class="thumbnail__thumb thumbnail__thumb--live"></div>'
            '<div class="videostream__badge videostream__status videostream__status--live">LIVE</div></div>'
        )
        self.assertIsNone(live_chat_id_from_channel_html(css + duration))
        self.assertEqual(live_chat_id_from_channel_html(css + duration + live), "222")
        self.assertEqual(stream_title_from_channel_html(css + duration + live), "")
        second = live.replace("222", "333")
        with self.assertRaises(ValueError):
            live_chat_id_from_channel_html(live + second)
        upcoming = """
        <rum-videos-grid><script type="application/json">
        {"items":[
          {"id":111,"title":"Old","live":false,"live_placeholder":false},
          {"id":447215100,"title":"Soon","live":false,"live_placeholder":true}
        ]}
        </script></rum-videos-grid>
        """
        self.assertEqual(live_chat_id_from_channel_html(upcoming), "447215100")
        self.assertEqual(stream_title_from_channel_html(upcoming), "Soon")
        self.assertEqual(
            live_chat_id_from_channel_html(
                upcoming.replace('"live":false,"live_placeholder":true', '"live":true,"live_placeholder":true')
            ),
            "447215100",
        )
        prefers_live = upcoming.replace(
            '{"id":111,"title":"Old","live":false,"live_placeholder":false}',
            '{"id":222,"title":"Now","live":true,"live_placeholder":false}',
        )
        self.assertEqual(live_chat_id_from_channel_html(prefers_live), "222")
        self.assertEqual(stream_title_from_channel_html(prefers_live), "Now")
        two_upcoming = upcoming.replace(
            '{"id":111,"title":"Old","live":false,"live_placeholder":false}',
            '{"id":333,"title":"Later","live":false,"live_placeholder":true}',
        )
        with self.assertRaises(ValueError):
            live_chat_id_from_channel_html(two_upcoming)
        self.assertEqual(stream_title_from_channel_html(live + second), "")
        self.assertEqual(stream_title_from_channel_html(two_upcoming), "")
        self.assertEqual(
            rumble_channel_page_url("https://rumble.com/c/SampleNewsTV/livestreams"),
            "https://rumble.com/c/SampleNewsTV",
        )
        self.assertEqual(validate_rumble_channel("https://www.rumble.com/user/Name"), "https://www.rumble.com/user/Name")
        for bad in (
            "446413572",
            "v7ds6x0",
            "https://rumble.com/v5abcde-sample-live-tv.html",
            "https://rumble.com/-livestream-api/get-data?key=abc",
        ):
            with self.assertRaises(ValueError):
                validate_rumble_channel(bad)

    def test_rumble_url_must_be_the_livestream_endpoint(self):
        good = "https://rumble.com/-livestream-api/get-data?key=abc"
        self.assertEqual(validate_rumble_api_url(good), good)
        for bad in (
            "http://rumble.com/-livestream-api/get-data?key=abc",
            "https://rumble.com.evil.example/-livestream-api/get-data?key=abc",
            "https://evil.com/-livestream-api/get-data?key=abc",
            "https://user:pass@rumble.com/-livestream-api/get-data?key=abc",
            "https://rumble.com/account/livestream-api",
            "https://rumble.com:8443/-livestream-api/get-data?key=abc",
        ):
            with self.assertRaises(ValueError):
                validate_rumble_api_url(bad)


if __name__ == "__main__":
    unittest.main()
