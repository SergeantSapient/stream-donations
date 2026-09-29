"""Public YouTube live chat parsing. No network and no channel ownership."""

import unittest

from streamdonations.youtube_public import (
    donations_from_live_chat_actions,
    parse_display_amount,
    session_from_live_chat_html,
    validate_youtube_watch,
    video_id_from_target,
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

    def test_amount_formats(self):
        self.assertEqual(parse_display_amount("$5.00"), (5.0, "USD"))
        self.assertEqual(parse_display_amount("€2,50"), (2.5, "EUR"))
        self.assertEqual(parse_display_amount("¥1,000"), (1000.0, "JPY"))

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


if __name__ == "__main__":
    unittest.main()
