"""Stream total chat line, tiers, and top donors."""

import unittest

from streamdonations.goals import (
    GoalTier,
    donor_bar_line,
    donor_leaderboard,
    empty_tiers,
    is_tier_reached,
    stream_chat_line,
    tier_breaches,
    tier_label,
    tier_thank_you_line,
)


def tier(amount, name, bonus="", enabled=True):
    return GoalTier(enabled, amount, name, bonus)


def line(total, tiers, extra="", enabled=True):
    return stream_chat_line(total, enabled=enabled, tiers=tiers, extra=extra)


class ChatLineTests(unittest.TestCase):
    def test_total_only_when_tiers_are_off_or_empty(self):
        self.assertEqual(line(12.3, []), "$12.30 total for stream.")
        self.assertEqual(line(12.3, [tier(25, "Bronze")], enabled=False), "$12.30 total for stream.")
        self.assertEqual(line(0, empty_tiers()), "$0.00 total for stream.")

    def test_extra_note(self):
        self.assertEqual(line(8, [], "Thanks for the support."), "$8.00 total for stream. Thanks for the support.")

    def test_remaining_and_bonus(self):
        tiers = [tier(25, "Bronze"), tier(50, "Silver", "emotes"), tier(100, "Gold")]
        self.assertEqual(line(10, tiers), "$10.00 total for stream. $15.00 from Bronze")
        self.assertEqual(line(25, tiers), "$25.00 total for stream. $25.00 from Silver and emotes")

    def test_cent_short_counts_as_reached(self):
        tiers = [tier(25, "Bronze"), tier(50, "Silver", "emotes")]
        self.assertEqual(line(24.99, tiers), "$24.99 total for stream. $25.01 from Silver and emotes")
        self.assertEqual(line(24.98, tiers), "$24.98 total for stream. $0.02 from Bronze")

    def test_over_the_last_tier(self):
        tiers = [tier(25, "Bronze"), tier(500, "Diamond", "sub giveaway")]
        self.assertEqual(line(500, tiers), "$500.00 total for stream. $0.00 over Diamond and sub giveaway")
        self.assertEqual(line(512.5, tiers), "$512.50 total for stream. $12.50 over Diamond and sub giveaway")

    def test_skips_disabled_unnamed_and_zero(self):
        tiers = [
            tier(10, "Skip", enabled=False),
            tier(0, "Zero"),
            GoalTier(True, 40, "", ""),
            tier(80, "Gold", "raid"),
        ]
        self.assertEqual(line(20, tiers), "$20.00 total for stream. $60.00 from Gold and raid")

    def test_thousands_separator(self):
        self.assertEqual(line(1234.5, [tier(2000, "Final")]), "$1,234.50 total for stream. $765.50 from Final")

    def test_extra_is_one_line(self):
        self.assertEqual(line(1, [], "line one\nline two"), "$1.00 total for stream. line one line two")


class TierAndDonorTests(unittest.TestCase):
    def test_reach_and_labels(self):
        self.assertTrue(is_tier_reached(300, 300))
        self.assertTrue(is_tier_reached(299.99, 300))
        self.assertFalse(is_tier_reached(299.98, 300))
        self.assertEqual(tier_label(tier(300, "Bronze")), "Bronze")
        self.assertEqual(tier_label(GoalTier(True, 300, "  ", "")), "$300.00")
        self.assertEqual(
            tier_thank_you_line("Bronze", "MapleFan"),
            "Thank you for helping us reach Bronze, MapleFan!",
        )

    def test_donor_bar_and_board(self):
        rows = [
            {"author_name": "Ada", "amount_usd": 9.87, "platform_label": "YouTube"},
            {"author_name": "ada", "amount_usd": 0.13, "platform_label": "Kick"},
            {"author_name": "Ben", "amount_usd": 6.54, "platform_label": "Rumble"},
            {"author_name": "Cam", "amount_usd": 3.21, "platform_label": "Other"},
        ]
        board = donor_leaderboard(rows)
        self.assertEqual(board["total"], 3)
        self.assertEqual(board["rows"][0]["donor"], "Ada")
        self.assertEqual(board["rows"][0]["usd"], 10.0)
        self.assertEqual(board["rows"][0]["count"], 2)
        self.assertEqual(board["rows"][0]["platforms"], ["YouTube", "Kick"])
        self.assertEqual(
            donor_bar_line(board["rows"]),
            "Top Donors: Ada: $10.00, Ben: $6.54, Cam: $3.21. Thank you for the support!",
        )
        self.assertEqual(donor_bar_line([]), "Top Donors: Thank you for the support!")

    def test_first_gift_that_reaches_a_tier(self):
        rows = [
            {"id": 1, "author_name": "Ada", "amount_usd": 10},
            {"id": 2, "author_name": "Ben", "amount_usd": 20},
        ]
        found = tier_breaches(rows, True, [tier(25, "Bronze")])
        self.assertEqual(found[0]["donor"], "Ben")
        self.assertEqual(found[0]["line"], "Thank you for helping us reach Bronze, Ben!")
        self.assertEqual(tier_breaches(rows, False, [tier(25, "Bronze")]), [])
