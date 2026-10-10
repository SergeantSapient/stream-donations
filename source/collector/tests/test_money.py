"""Smashcast CSV layout, normalized types, and the USD tally."""

import csv
import io
import tempfile
import unittest
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from streamdonations.fx import rate_status, refresh_rates, reset_rate_cache, usd_rates
from streamdonations.money import quote
from streamdonations.normalize import Donation
from streamdonations.service import Service
from streamdonations.store import Store
from streamdonations.transport import HttpResult

RATES = {"USD": 1.0, "AUD": 1.425788, "PHP": 62.449889}
EASTERN = ZoneInfo("America/New_York")


def row(dedupe, platform, kind, author, message, amount, currency, created, raw=None):
    return Donation(
        dedupe_key=dedupe,
        platform=platform,
        kind=kind,
        external_id=dedupe,
        channel="channel",
        author_name=author,
        author_id="author",
        message=message,
        amount_value=amount,
        amount_micros=None if amount is None else int(round(amount * 1_000_000)),
        currency=currency,
        created_at=created,
        raw=raw or {},
    )


class MoneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.store.add_many(
            [
                row(
                    "yt-chat",
                    "youtube",
                    "super_chat",
                    "BakedBuddhist",
                    "hello",
                    2,
                    "USD",
                    "2026-09-15T00:21:43Z",
                ),
                row(
                    "yt-sticker",
                    "youtube",
                    "super_sticker",
                    "predawncorpses",
                    "poop",
                    0.99,
                    "USD",
                    "2026-09-15T07:42:54Z",
                ),
                row(
                    "yt-aud",
                    "youtube",
                    "super_chat",
                    "Shadowalker13-w1r",
                    "censor",
                    10,
                    "AUD",
                    "2026-09-15T04:34:28Z",
                ),
                row(
                    "kick-small",
                    "kick",
                    "kicks",
                    "manikantan_r",
                    "Hell Yeah",
                    1,
                    "KICKS",
                    "2026-09-15T03:19:18Z",
                ),
                row(
                    "kick-five",
                    "kick",
                    "kicks",
                    "predawncorpse",
                    "five",
                    500,
                    "KICKS",
                    "2026-09-15T04:34:12Z",
                ),
                row(
                    "kick-under",
                    "kick",
                    "kicks",
                    "edge",
                    "four",
                    499,
                    "KICKS",
                    "2026-09-15T04:34:13Z",
                ),
                row(
                    "rumble-rant",
                    "rumble",
                    "rant",
                    "SergeantSapient",
                    "coin",
                    1,
                    "USD",
                    "2026-09-15T02:34:07Z",
                ),
                row(
                    "yt-member",
                    "youtube",
                    "membership",
                    "peastri7804",
                    "New member",
                    None,
                    None,
                    "2026-09-15T05:33:04Z",
                ),
                row(
                    "rumble-gifts",
                    "rumble",
                    "gifted_subscriptions",
                    "gifter",
                    "Gifted 3 subscriptions",
                    None,
                    None,
                    "2026-09-15T05:40:00Z",
                    {"gift_purchase_notification": {"total_gifts": 3}},
                ),
                row(
                    "yt-receipt",
                    "youtube",
                    "gift_membership_received",
                    "giftee",
                    "Received a gift membership",
                    None,
                    None,
                    "2026-09-15T05:41:00Z",
                ),
                row(
                    "formula",
                    "youtube",
                    "super_chat",
                    "evil",
                    '=HYPERLINK("http://evil")',
                    1,
                    "USD",
                    "2026-09-15T06:00:00Z",
                ),
            ]
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_csv_matches_the_smashcast_columns(self):
        text = self.store.export_csv(rates=RATES, membership_price=4.99, zone=EASTERN)
        parsed = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(
            list(parsed[0].keys()),
            ["Date", "Time", "Donor", "Amount", "Platform", "Type", "Currency", "Amount USD", "Message"],
        )
        first = parsed[0]
        self.assertEqual(first["Date"], "2026-09-14")
        self.assertEqual(first["Time"], "20:21:43")
        self.assertEqual(first["Donor"], "BakedBuddhist")
        self.assertEqual(first["Amount"], "2.00")
        self.assertEqual(first["Platform"], "YouTube")
        self.assertEqual(first["Type"], "Super Chat")
        self.assertEqual(first["Currency"], "USD")
        self.assertEqual(first["Amount USD"], "2.00")
        self.assertEqual(first["Message"], "hello")
        by_donor = {item["Donor"]: item for item in parsed}
        self.assertEqual(by_donor["manikantan_r"]["Type"], "Super Sticker")
        self.assertEqual(by_donor["manikantan_r"]["Amount"], "0.01")
        self.assertEqual(by_donor["manikantan_r"]["Currency"], "USD")
        self.assertEqual(by_donor["predawncorpse"]["Type"], "Super Chat")
        self.assertEqual(by_donor["predawncorpse"]["Amount"], "5.00")
        self.assertEqual(by_donor["edge"]["Type"], "Super Sticker")
        self.assertEqual(by_donor["edge"]["Amount"], "4.99")
        self.assertEqual(by_donor["SergeantSapient"]["Platform"], "Rumble")
        self.assertEqual(by_donor["SergeantSapient"]["Type"], "Super Chat")
        self.assertEqual(by_donor["predawncorpses"]["Type"], "Super Sticker")
        self.assertEqual(by_donor["Shadowalker13-w1r"]["Currency"], "AUD")
        self.assertEqual(by_donor["Shadowalker13-w1r"]["Amount"], "10.00")
        self.assertEqual(by_donor["Shadowalker13-w1r"]["Amount USD"], "7.01")
        self.assertEqual(by_donor["peastri7804"]["Type"], "Membership")
        self.assertEqual(by_donor["peastri7804"]["Amount"], "4.99")
        self.assertEqual(by_donor["peastri7804"]["Amount USD"], "4.99")
        self.assertEqual(by_donor["gifter"]["Amount"], "14.99")
        self.assertIn("'=HYPERLINK", text)
        donors = [item["Donor"] for item in parsed]
        self.assertLess(donors.index("BakedBuddhist"), donors.index("evil"))

    def test_blank_membership_price_leaves_the_amount_empty(self):
        text = self.store.export_csv(rates=RATES, membership_price=None, zone=timezone.utc)
        parsed = list(csv.DictReader(io.StringIO(text)))
        member = next(item for item in parsed if item["Donor"] == "peastri7804")
        self.assertEqual(member["Amount"], "")
        self.assertEqual(member["Amount USD"], "")

    def test_anniversary_card_is_a_free_super_chat(self):
        self.store.add_many([
            row(
                "yt-anniversary",
                "youtube",
                "member_milestone",
                "samfisher2141",
                "Member for 2 months · Smashers · HAIL Smash",
                None,
                None,
                "2026-09-30T05:47:36Z",
                {"headerPrimaryText": {"simpleText": "Member for 2 months"}},
            )
        ])
        chats, chat_total = self.store.list_donations(kind="super_chat", membership_price=4.99, rates=RATES)
        members, _member_total = self.store.list_donations(kind="membership", membership_price=4.99, rates=RATES)
        self.assertEqual(chat_total, 6)
        shown = next(item for item in chats if item["author_name"] == "samfisher2141")
        self.assertEqual(shown["kind_label"], "Super Chat")
        self.assertIsNone(shown["amount_usd"])
        self.assertIsNone(shown["quoted_amount"])
        self.assertNotIn("samfisher2141", {item["author_name"] for item in members})
        self.assertEqual(self.store.total_donated(RATES, 4.99), 41.98)
        report = self.store.stream_report(RATES, 4.99)
        self.assertEqual(report["total_usd"], 41.98)
        self.assertIn("Super Chat", {item["type"] for item in report["by_type"]})

    def test_type_filter_groups_platforms(self):
        chats, chat_total = self.store.list_donations(kind="super_chat")
        stickers, sticker_total = self.store.list_donations(kind="super_sticker")
        members, member_total = self.store.list_donations(kind="membership")
        self.assertEqual(chat_total, 5)
        self.assertEqual({row["author_name"] for row in chats}, {"BakedBuddhist", "Shadowalker13-w1r", "predawncorpse", "SergeantSapient", "evil"})
        self.assertEqual(sticker_total, 3)
        self.assertEqual(member_total, 3)

    def test_tally_is_usd_and_skips_gift_receipts(self):
        total = self.store.total_donated(RATES, 4.99)
        # 2 + 0.99 + 7.01 + 0.01 + 5 + 4.99 + 1 + 4.99 + 14.99 + 1 = 41.98
        # The Rumble gift is 4.99 + 2×5.00. The gift receipt is not included.
        self.assertEqual(total, 41.98)
        five = quote(
            {
                "platform": "rumble",
                "kind": "gifted_subscriptions",
                "amount_value": None,
                "currency": None,
                "message": "Gifted 5 subscriptions",
                "raw": {"gift_purchase_notification": {"total_gifts": 5}},
            },
            4.99,
            RATES,
        )
        self.assertEqual(five.usd, 24.99)
        one = quote(
            {
                "platform": "rumble",
                "kind": "subscription",
                "amount_value": None,
                "currency": None,
                "message": "Subscribed",
                "raw": {},
            },
            4.99,
            RATES,
        )
        self.assertEqual(one.usd, 4.99)
        youtube_gift = quote(
            {
                "platform": "youtube",
                "kind": "membership_gift",
                "amount_value": None,
                "currency": None,
                "message": "Sent 5 Smash JT gift memberships",
                "raw": {
                    "header": {
                        "liveChatSponsorshipsHeaderRenderer": {
                            "primaryText": {
                                "runs": [
                                    {"text": "Sent "},
                                    {"text": "5"},
                                    {"text": " Smash JT gift memberships"},
                                ]
                            }
                        }
                    }
                },
            },
            4.99,
            RATES,
        )
        self.assertEqual(youtube_gift.usd, 24.95)
        priced = quote(
            {"kind": "membership", "amount_value": None, "currency": None, "message": "", "raw": {}},
            None,
            RATES,
        )
        self.assertIsNone(priced.usd)

    def test_stored_peso_without_currency_uses_the_payload(self):
        shown = quote(
            {
                "platform": "youtube",
                "kind": "super_chat",
                "amount_value": 50,
                "currency": None,
                "message": "juuuuuuuuuuuuicy tip",
                "raw": {"purchaseAmountText": {"simpleText": "₱50.00"}},
            },
            None,
            RATES,
        )
        self.assertEqual(shown.amount, 50.0)
        self.assertEqual(shown.currency, "PHP")
        self.assertEqual(shown.usd, round(50 / RATES["PHP"], 2))
        exact = quote(
            {
                "platform": "youtube",
                "kind": "super_chat",
                "amount_value": 0.8,
                "currency": None,
                "raw": {
                    "purchaseAmountText": {"simpleText": "$0.80"},
                    "amountMicros": "50000000",
                    "currency": "PHP",
                },
            },
            None,
            RATES,
        )
        self.assertEqual(exact.amount, 50.0)
        self.assertEqual(exact.currency, "PHP")
        self.assertEqual(exact.usd, round(50 / RATES["PHP"], 2))

    def test_anniversary_membership_adds_nothing(self):
        anniversary = quote(
            {
                "platform": "youtube",
                "kind": "membership",
                "amount_value": None,
                "currency": None,
                "message": "Smashers",
                "raw": {
                    "headerPrimaryText": {"runs": [{"text": "Member for 2 months"}]},
                    "headerSubtext": {"simpleText": "Smashers"},
                },
            },
            4.99,
            RATES,
        )
        self.assertIsNone(anniversary.amount)
        self.assertIsNone(anniversary.usd)
        self.assertEqual(anniversary.label, "Super Chat")
        year = quote(
            {
                "platform": "youtube",
                "kind": "member_milestone",
                "amount_value": None,
                "currency": None,
                "message": "Member for 1 year",
                "raw": {},
            },
            4.99,
            RATES,
        )
        self.assertIsNone(year.usd)
        self.assertEqual(year.label, "Super Chat")
        kick_celebration = quote(
            {
                "platform": "kick",
                "kind": "member_milestone",
                "amount_value": 0,
                "currency": "USD",
                "message": "Subscribed for 15 months",
                "raw": {},
            },
            4.99,
            RATES,
        )
        self.assertEqual(kick_celebration.amount, 0.0)
        self.assertEqual(kick_celebration.usd, 0.0)
        self.assertEqual(kick_celebration.currency, "USD")
        self.assertEqual(kick_celebration.label, "Super Chat")
        fresh = quote(
            {
                "platform": "youtube",
                "kind": "membership",
                "amount_value": None,
                "currency": None,
                "message": "Smashers",
                "raw": {"headerSubtext": {"simpleText": "Smashers"}},
            },
            4.99,
            RATES,
        )
        self.assertEqual(fresh.usd, 4.99)

    def test_membership_price_setting(self):
        service = Service(self.store)
        service.apply_settings({"membership_price": "24.9"})
        self.assertEqual(self.store.get_setting("membership_price"), "24.90")
        service.apply_settings({"membership_price": ""})
        self.assertEqual(self.store.get_setting("membership_price"), "")
        with self.assertRaises(ValueError):
            service.apply_settings({"membership_price": "nope"})


class RateCacheTests(unittest.TestCase):
    def setUp(self):
        reset_rate_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.calls = 0

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
        reset_rate_cache()

    def _transport(self, responses):
        class Scripted:
            def request(inner, method, url, **_kwargs):
                self.calls += 1
                status, body = responses.pop(0)
                return HttpResult(status, {}, body, url)

        return Scripted()

    def test_full_response_is_cached_for_an_hour(self):
        body = b'{"result":"success","base_code":"USD","time_next_update_unix":5000,"rates":{"USD":1,"AUD":2}}'
        transport = self._transport([(200, body), (200, body)])
        rates = usd_rates(self.store, transport, now=1_000)
        self.assertEqual(rates["AUD"], 2)
        self.assertEqual(usd_rates(self.store, transport, now=1_000 + 3599)["AUD"], 2)
        self.assertEqual(self.calls, 1)
        stored = self.store.get_setting("fx_response_json")
        self.assertIn("time_next_update_unix", stored)
        self.assertEqual(rate_status(self.store, now=1_000)["next_update_at"], 5000)
        later = b'{"result":"success","base_code":"USD","rates":{"USD":1,"AUD":3}}'
        transport = self._transport([(200, later)])
        self.assertEqual(usd_rates(self.store, transport, now=1_000 + 3601)["AUD"], 3)
        self.assertEqual(self.calls, 2)

    def test_manual_refresh_fetches_unless_the_provider_said_to_wait(self):
        body = b'{"result":"success","base_code":"USD","rates":{"USD":1,"AUD":2}}'
        transport = self._transport([(200, body), (200, body), (429, b"{}")])
        usd_rates(self.store, transport, now=1_000)
        early = refresh_rates(self.store, transport, now=1_010)
        self.assertEqual(early["outcome"], "cached")
        self.assertEqual(self.calls, 1)
        updated = refresh_rates(self.store, transport, now=1_030)
        self.assertEqual(updated["outcome"], "fetched")
        self.assertEqual(self.calls, 2)
        limited = refresh_rates(self.store, transport, now=1_060)
        self.assertEqual(limited["outcome"], "limited")
        self.assertIn("20 minutes", limited["error"])
        refresh_rates(self.store, transport, now=1_060 + 60)
        self.assertEqual(self.calls, 3)


if __name__ == "__main__":
    unittest.main()
