"""Public Kick chat parsing. No network and no channel ownership."""

import unittest

from streamdonations.kick_public import donations_from_kick_public, kick_slug, validate_kick_watch


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
        self.assertEqual(
            donations_from_kick_public("LuckyUsersWhoGotGiftSubscriptionsEvent", {"users": ["a"]}, "creator"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
