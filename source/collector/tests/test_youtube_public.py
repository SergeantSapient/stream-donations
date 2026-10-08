"""Public YouTube live chat parsing. No network and no channel ownership."""

import unittest

from streamdonations.youtube_public import (
    donations_from_live_chat_actions,
    live_video_from_channel_html,
    parse_display_amount,
    session_from_live_chat_html,
    validate_youtube_channel,
    validate_youtube_watch,
    video_id_from_html,
    video_id_from_target,
    youtube_channel_live_url,
)


CHAT_HTML = """
<script>ytcfg.set({"INNERTUBE_API_KEY":"test-key","INNERTUBE_CLIENT_VERSION":"2.20260101.00.00","INNERTUBE_CONTEXT":{"client":{"clientName":"WEB","clientVersion":"2.20260101.00.00"}}});</script>
<script>ytInitialData = {"contents":{"liveChatRenderer":{"continuations":[{"invalidationContinuationData":{"continuation":"CONT","timeoutMs":4000}}],"actions":[]}}};</script>
"""

ACTIONS = [
    {
        "addChatItemAction": {
            "item": {
                "liveChatTextMessageRenderer": {
                    "id": "plain",
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
                    "timestampUsec": "1700000000000000",
                    "authorName": {"simpleText": "Fan"},
                    "authorExternalChannelId": "UCfan",
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
                    "timestampUsec": "1700000001000000",
                    "authorName": {"simpleText": "Fan"},
                    "purchaseAmountText": {"simpleText": "CA$2.00"},
                    "sticker": {"accessibility": {"accessibilityData": {"label": "fire"}}},
                }
            }
        }
    },
    {
        "addChatItemAction": {
            "item": {
                "liveChatMembershipItemRenderer": {
                    "id": "member-1",
                    "timestampUsec": "1700000002000000",
                    "authorName": {"simpleText": "Member"},
                    "headerSubtext": {"simpleText": "Member for 6 months"},
                    "message": {"simpleText": "still here"},
                }
            }
        }
    },
    {
        "addChatItemAction": {
            "item": {
                "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer": {
                    "id": "gift-1",
                    "timestampUsec": "1700000003000000",
                    "header": {
                        "liveChatSponsorshipsHeaderRenderer": {
                            "authorName": {"simpleText": "Patron"},
                            "primaryText": {"runs": [{"text": "Gifted 3 memberships"}]},
                        }
                    },
                }
            }
        }
    },
]


class PublicYouTubeTests(unittest.TestCase):
    def test_paid_events_from_any_chats_actions(self):
        donations = donations_from_live_chat_actions(ACTIONS, channel="OtherCreator")
        kinds = [item.kind for item in donations]
        self.assertEqual(kinds, ["super_chat", "super_sticker", "member_milestone", "membership_gift"])
        super_chat = donations[0]
        self.assertEqual(super_chat.author_name, "Fan")
        self.assertEqual(super_chat.message, "love the show")
        self.assertEqual(super_chat.amount_value, 5)
        self.assertEqual(super_chat.currency, "USD")
        self.assertEqual(super_chat.channel, "OtherCreator")
        self.assertEqual(super_chat.dedupe_key, "youtube:public:paid-1")
        self.assertEqual(donations[1].message, "fire")
        self.assertEqual(donations[1].amount_value, 2)
        self.assertEqual(donations[1].currency, "CAD")
        self.assertEqual(donations[2].message, "Member for 6 months · still here")
        self.assertEqual(donations[3].author_name, "Patron")
        self.assertEqual(donations[3].message, "Gifted 3 memberships")

    def test_anniversary_header_is_a_milestone_even_without_a_comment(self):
        donations = donations_from_live_chat_actions(
            [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatMembershipItemRenderer": {
                                "id": "anniv-1",
                                "authorName": {"simpleText": "@CFH"},
                                "headerPrimaryText": {"runs": [{"text": "Member for 2 months"}]},
                                "headerSubtext": {"simpleText": "Smashers"},
                            }
                        }
                    }
                }
            ]
        )
        self.assertEqual(len(donations), 1)
        self.assertEqual(donations[0].kind, "member_milestone")
        self.assertEqual(donations[0].message, "Member for 2 months · Smashers")
        self.assertIsNone(donations[0].amount_value)

    def test_amount_formats(self):
        self.assertEqual(parse_display_amount("$5.00"), (5.0, "USD"))
        self.assertEqual(parse_display_amount("€2,50"), (2.5, "EUR"))
        self.assertEqual(parse_display_amount("¥1,000"), (1000.0, "JPY"))
        self.assertEqual(parse_display_amount("₱50.00"), (50.0, "PHP"))
        self.assertEqual(parse_display_amount("PHP 50.00"), (50.0, "PHP"))

    def test_peso_display_is_php_and_micros_win(self):
        shown = donations_from_live_chat_actions(
            [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatPaidMessageRenderer": {
                                "id": "peso-1",
                                "timestampUsec": "1700000000000000",
                                "authorName": {"simpleText": "kayeend"},
                                "purchaseAmountText": {"simpleText": "₱50.00"},
                                "message": {"simpleText": "juuuuuuuuuuuuicy tip"},
                            }
                        }
                    }
                }
            ]
        )
        self.assertEqual(shown[0].amount_value, 50.0)
        self.assertEqual(shown[0].currency, "PHP")
        exact = donations_from_live_chat_actions(
            [
                {
                    "addChatItemAction": {
                        "item": {
                            "liveChatPaidMessageRenderer": {
                                "id": "peso-2",
                                "authorName": {"simpleText": "kayeend"},
                                "purchaseAmountText": {"simpleText": "$0.80"},
                                "amountMicros": "50000000",
                                "currency": "PHP",
                                "message": {"simpleText": "juuuuuuuuuuuuicy tip"},
                            }
                        }
                    }
                }
            ]
        )
        self.assertEqual(exact[0].amount_value, 50.0)
        self.assertEqual(exact[0].currency, "PHP")
        self.assertEqual(exact[0].amount_micros, 50_000_000)

    def test_live_chat_page_session_and_video_id(self):
        session = session_from_live_chat_html(CHAT_HTML)
        self.assertEqual(session["api_key"], "test-key")
        self.assertEqual(session["continuation"], "CONT")
        self.assertEqual(session["timeout_ms"], 4000)
        self.assertEqual(video_id_from_target("dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(video_id_from_target("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(
            video_id_from_target("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(video_id_from_target("https://www.youtube.com/live/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(validate_youtube_watch("https://www.youtube.com/@nasa/live"), "https://www.youtube.com/@nasa/live")
        with self.assertRaises(ValueError):
            validate_youtube_watch("https://evil.example/watch?v=dQw4w9WgXcQ")
        with self.assertRaises(ValueError):
            session_from_live_chat_html("<html>consent.youtube.com</html>")

    def test_channel_live_page_uses_the_broadcast_that_is_on_now(self):
        live = """
        <link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">
        <script>"videoDetails":{"videoId":"abcdefghijk","title":"Desk","lengthSeconds":"0","isLive":true}</script>
        "liveBroadcastDetails":{"isLiveNow":true,"startTimestamp":"2026-10-01T17:00:26+00:00"}
        "videoId":"zzzzzzzzzzz"
        """
        self.assertEqual(live_video_from_channel_html(live), "abcdefghijk")
        offline = """
        <link rel="canonical" href="https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA">
        "videoId":"abcdefghijk"
        """
        self.assertIsNone(live_video_from_channel_html(offline))
        self.assertEqual(video_id_from_html(offline), "abcdefghijk")
        ended = """
        <link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">
        "videoDetails":{"videoId":"abcdefghijk","title":"Desk","isLive":false}
        "isLiveNow":true
        """
        self.assertIsNone(live_video_from_channel_html(ended))
        upcoming = """
        <link rel="canonical" href="https://www.youtube.com/watch?v=tL02RZwytU4">
        "videoDetails":{"videoId":"tL02RZwytU4","title":"Soon","isUpcoming":true,"isLiveContent":true}
        "isLiveNow":false
        "videoId":"othervideo1"
        """
        self.assertEqual(live_video_from_channel_html(upcoming), "tL02RZwytU4")
        other_upcoming = """
        <link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">
        "videoDetails":{"videoId":"abcdefghijk","title":"Old","isLive":false}
        "videoDetails":{"videoId":"zzzzzzzzzzz","isUpcoming":true}
        """
        self.assertIsNone(live_video_from_channel_html(other_upcoming))
        self.assertEqual(
            youtube_channel_live_url("https://www.youtube.com/@NASA/streams"),
            "https://www.youtube.com/@NASA/live",
        )
        self.assertEqual(
            youtube_channel_live_url("https://m.youtube.com/channel/UCLA_DiR1FfKNvjuUpBHmylQ/live"),
            "https://www.youtube.com/channel/UCLA_DiR1FfKNvjuUpBHmylQ/live",
        )
        self.assertEqual(validate_youtube_channel("https://www.youtube.com/@nasa"), "https://www.youtube.com/@nasa")
        for bad in (
            "abcdefghijk",
            "https://youtu.be/abcdefghijk",
            "https://www.youtube.com/watch?v=abcdefghijk",
            "https://www.youtube.com/live/abcdefghijk",
            "https://www.youtube.com/@nasa/playlist",
        ):
            with self.assertRaises(ValueError):
                validate_youtube_channel(bad)


if __name__ == "__main__":
    unittest.main()
