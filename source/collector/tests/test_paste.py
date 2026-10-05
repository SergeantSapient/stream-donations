"""Live Till paste cases, plus this app's membership price and dollar conversion."""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from streamdonations.fx import reset_rate_cache
from streamdonations.paste import parse_entry, paste_for_form
from streamdonations.server import App, Handler, ThreadingServer
from streamdonations.store import Store


class YouTubeSuperChatTests(unittest.TestCase):
    def test_reads_donor_usd_amount_and_message(self):
        parsed = parse_entry("predawncorpses\n$0.99\nKeep going brother")
        self.assertEqual(parsed.donor, "predawncorpses")
        self.assertEqual(parsed.amount, "0.99")
        self.assertEqual(parsed.currency, "USD")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.platform, "YouTube")
        self.assertEqual(parsed.message, "Keep going brother")

    def test_strips_a_leading_at_from_the_donor(self):
        parsed = parse_entry("@MapleFan\n$5.00\nhello")
        self.assertEqual(parsed.donor, "MapleFan")
        self.assertEqual(parsed.amount, "5.00")

    def test_maps_canadian_dollars(self):
        parsed = parse_entry("MapleFan\nCA$10.00\nFrom the north")
        self.assertEqual(parsed.amount, "10.00")
        self.assertEqual(parsed.currency, "CAD")
        self.assertEqual(parsed.donation_type, "Super Chat")

    def test_maps_rupees(self):
        parsed = parse_entry("RupeeFan\n₹777.77\nLove from India")
        self.assertEqual(parsed.donor, "RupeeFan")
        self.assertEqual(parsed.amount, "777.77")
        self.assertEqual(parsed.currency, "INR")
        self.assertEqual(parsed.message, "Love from India")

    def test_maps_pounds_on_a_live_youtube_paste(self):
        parsed = parse_entry("PoundFan\n£5.00\nFrom the UK")
        self.assertEqual(parsed.donor, "PoundFan")
        self.assertEqual(parsed.amount, "5.00")
        self.assertEqual(parsed.currency, "GBP")
        self.assertEqual(parsed.message, "From the UK")

    def test_skips_reply_like_and_smashers_chrome(self):
        parsed = parse_entry("Donor\nSmashers\nReply\nLike\n$2.00\nhi")
        self.assertEqual(parsed.donor, "Donor")
        self.assertEqual(parsed.amount, "2.00")
        self.assertEqual(parsed.message, "hi")

    def test_drops_badges_handles_and_timestamps(self):
        parsed = parse_entry("\n".join([
            "MapleFan",
            "@maplesyrup",
            "Member (12 months)",
            "$5.00",
            "0:12",
            "hello from canada",
        ]))
        self.assertEqual(parsed.donor, "MapleFan")
        self.assertEqual(parsed.amount, "5.00")
        self.assertEqual(parsed.message, "hello from canada")

    def test_super_sticker_label_sets_the_type(self):
        parsed = parse_entry("StickerFan\nSuper Sticker\n$2.00")
        self.assertEqual(parsed.donation_type, "Super Sticker")
        self.assertEqual(parsed.amount, "2.00")
        self.assertEqual(parsed.message, "")

    def test_blank_line_splits_the_youtube_header_from_the_message(self):
        parsed = parse_entry("\n".join([
            "@sergeantsapient",
            "Member (6 months)",
            "Member for 11 months",
            "Somebody",
            "",
            "Hello and I'm testing a parser so this is a test.",
        ]))
        self.assertEqual(parsed.donor, "sergeantsapient")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.platform, "YouTube")
        self.assertEqual(parsed.amount, "")
        self.assertEqual(parsed.message, "Hello and I'm testing a parser so this is a test.")

    def test_keeps_the_amount_when_a_blank_line_precedes_the_message(self):
        parsed = parse_entry("Donor\n$5.00\nSomebody\n\nhello there")
        self.assertEqual(parsed.donor, "Donor")
        self.assertEqual(parsed.amount, "5.00")
        self.assertEqual(parsed.message, "hello there")

    def test_message_is_one_line_and_later_like_is_chrome(self):
        parsed = parse_entry("\n".join([
            "@Proctophile",
            "Member (6 months)",
            "",
            "#2",
            "$2.00",
            "",
            "look at the chat dummy",
            "",
            "Like",
        ]))
        self.assertEqual(parsed.donor, "Proctophile")
        self.assertEqual(parsed.amount, "2.00")
        self.assertEqual(parsed.currency, "USD")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.platform, "YouTube")
        self.assertEqual(parsed.message, "look at the chat dummy")
        self.assertNotIn("\n", parsed.message)
        self.assertNotIn("Like", parsed.message)
        self.assertNotIn("#2", parsed.message)

    def test_drops_reply_and_trailing_counts(self):
        parsed = parse_entry("\n".join([
            "Donor", "$2.00", "", "hello from chat", "", "Like", "3", "Reply", "1",
        ]))
        self.assertEqual(parsed.amount, "2.00")
        self.assertEqual(parsed.message, "hello from chat")

    def test_keeps_a_message_that_includes_a_truncated_url(self):
        parsed = parse_entry("\n".join([
            "@predawncorpses",
            "Member (1 year)",
            "$6.66",
            "",
            'SMASH! "What a horrible night to have a CURSE!” PLOX PLAY (IT AGAIN) SUFFAH MR. MORROW!: https://youtu.be/J4tThOaeH34?si=XuH-O...',
            "",
            "Like",
            "",
            "Reply",
        ]))
        self.assertEqual(parsed.donor, "predawncorpses")
        self.assertEqual(parsed.amount, "6.66")
        self.assertEqual(parsed.platform, "YouTube")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertIn("SMASH!", parsed.message)
        self.assertIn("https://youtu.be/J4tThOaeH34", parsed.message)
        self.assertNotIn("Like", parsed.message)
        self.assertNotIn("Reply", parsed.message)


class MembershipTests(unittest.TestCase):
    def test_youtube_gifts_use_price_times_count(self):
        parsed = parse_entry("GenerousFan\nSent 5 Smashers gift memberships")
        self.assertEqual(parsed.donation_type, "Membership")
        self.assertEqual(parsed.platform, "YouTube")
        self.assertEqual(parsed.amount, "24.95")
        self.assertEqual(parsed.currency, "USD")
        self.assertIsNotNone(parsed.membership)
        self.assertEqual(parsed.membership.count, 5)

    def test_paste_example_prices_five_smash_jt_memberships(self):
        text = "@twilightdragon3\nSent 5 Smash JT gift memberships\n"
        parsed = parse_entry(text, 4.99)
        self.assertEqual(parsed.donor, "twilightdragon3")
        self.assertEqual(parsed.platform, "YouTube")
        self.assertEqual(parsed.donation_type, "Membership")
        self.assertEqual(parsed.amount, "24.95")
        self.assertEqual(parsed.membership.count, 5)
        alone = parse_entry("Sent 5 Smash JT gift memberships", 4.99)
        self.assertEqual(alone.donation_type, "Membership")
        self.assertEqual(alone.amount, "24.95")
        self.assertEqual(alone.donor, "")
        capital = parse_entry("Fan\nSent 5 Smash JT Memberships", 4.99)
        self.assertEqual(capital.amount, "24.95")
        wrapped = parse_entry("@twilightdragon3\nSent 5 Smash JT\ngift memberships", 4.99)
        self.assertEqual(wrapped.donor, "twilightdragon3")
        self.assertEqual(wrapped.amount, "24.95")
        self.assertEqual(wrapped.membership.count, 5)

    def test_kick_gifts_use_price_times_count(self):
        parsed = parse_entry("KickChatter\nGifted 2 subscriptions to the community!")
        self.assertEqual(parsed.platform, "Kick")
        self.assertEqual(parsed.donation_type, "Membership")
        self.assertEqual(parsed.amount, "9.98")

    def test_rumble_gift_ladder_at_the_default_price(self):
        parsed = parse_entry("RumbleFan\nGifted 3 subscriptions to SmashJT")
        self.assertEqual(parsed.platform, "Rumble")
        self.assertEqual(parsed.donation_type, "Membership")
        self.assertEqual(parsed.amount, "14.99")

    def test_single_rumble_gift_uses_the_membership_price(self):
        parsed = parse_entry("SergeantSapient\nGifted 1 subscription to SmashJT")
        self.assertEqual(parsed.donor, "SergeantSapient")
        self.assertEqual(parsed.platform, "Rumble")
        self.assertEqual(parsed.donation_type, "Membership")
        self.assertEqual(parsed.amount, "4.99")
        self.assertEqual(parsed.currency, "USD")
        self.assertIsNotNone(parsed.membership)
        self.assertEqual(parsed.membership.type, "gift")
        self.assertEqual(parsed.membership.count, 1)
        self.assertEqual(parsed.message, "")

    def test_welcome_is_a_new_youtube_membership(self):
        parsed = parse_entry("@PhantomGB\nWelcome to Somebody!")
        self.assertEqual(parsed.donor, "PhantomGB")
        self.assertEqual(parsed.donation_type, "Membership")
        self.assertEqual(parsed.platform, "YouTube")
        self.assertEqual(parsed.amount, "4.99")
        self.assertEqual(parsed.currency, "USD")
        self.assertEqual(parsed.message, "")
        self.assertIsNotNone(parsed.membership)
        self.assertEqual(parsed.membership.type, "new")
        self.assertEqual(parsed.membership.count, 1)

    def test_new_member_is_a_single_membership(self):
        parsed = parse_entry("PhantomGB\nNew member")
        self.assertEqual(parsed.donation_type, "Membership")
        self.assertEqual(parsed.amount, "4.99")
        self.assertIsNotNone(parsed.membership)
        self.assertEqual(parsed.membership.type, "new")

    def test_member_badge_between_donor_and_welcome(self):
        parsed = parse_entry("@PhantomGB\nMember\nWelcome to Somebody!")
        self.assertEqual(parsed.donor, "PhantomGB")
        self.assertEqual(parsed.donation_type, "Membership")
        self.assertEqual(parsed.amount, "4.99")
        self.assertEqual(parsed.message, "")

    def test_welcome_does_not_overwrite_a_super_chat_amount(self):
        parsed = parse_entry("Donor\n$5.00\nWelcome to Somebody!")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.amount, "5.00")
        self.assertEqual(parsed.message, "")

    def test_amount_below_welcome_stays_a_super_chat(self):
        parsed = parse_entry("\n".join([
            "@Murvoth",
            "Member (6 months)",
            "Welcome to Somebody!",
            "$5.00",
            "",
            "How u know getting old,sleep like that sounds good",
        ]))
        self.assertEqual(parsed.donor, "Murvoth")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.amount, "5.00")
        self.assertEqual(parsed.currency, "USD")
        self.assertEqual(parsed.platform, "YouTube")
        self.assertEqual(parsed.message, "How u know getting old,sleep like that sounds good")
        self.assertIsNone(parsed.membership)

    def test_a_real_sentence_is_not_priced_as_a_membership(self):
        parsed = parse_entry("\n".join([
            "@Murvoth",
            "Member (6 months)",
            "Welcome to Somebody!",
            "",
            "How u know getting old,sleep like that sounds good",
        ]))
        self.assertEqual(parsed.donor, "Murvoth")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.amount, "")
        self.assertEqual(parsed.message, "How u know getting old,sleep like that sounds good")
        self.assertIsNone(parsed.membership)

    def test_one_word_channel_leftover_stays_a_membership(self):
        parsed = parse_entry("@PhantomGB\nWelcome to Somebody!\nSomebody")
        self.assertEqual(parsed.donation_type, "Membership")
        self.assertEqual(parsed.amount, "4.99")
        self.assertEqual(parsed.message, "")

    def test_configured_price_follows_the_ledger_formula(self):
        youtube = parse_entry("GenerousFan\nSent 3 Smashers gift memberships", membership_price=6)
        self.assertEqual(youtube.amount, "18.00")
        rumble = parse_entry("RumbleFan\nGifted 3 subscriptions to SmashJT", membership_price=6)
        self.assertEqual(rumble.amount, "16.00")
        single = parse_entry("SergeantSapient\nGifted 1 subscription to SmashJT", membership_price=6)
        self.assertEqual(single.amount, "6.00")
        kick = parse_entry("KickChatter\nGifted 2 subscriptions to the community!", membership_price=6)
        self.assertEqual(kick.amount, "12.00")

    def test_blank_membership_price_leaves_the_amount_blank(self):
        gift = parse_entry("GenerousFan\nSent 5 Smashers gift memberships", membership_price=None)
        self.assertEqual(gift.donation_type, "Membership")
        self.assertEqual(gift.amount, "")
        new = parse_entry("@PhantomGB\nWelcome to Somebody!", membership_price=None)
        self.assertEqual(new.amount, "")
        rumble = parse_entry("RumbleFan\nGifted 3 subscriptions to SmashJT", membership_price=None)
        self.assertEqual(rumble.amount, "")
        kick = parse_entry("KickChatter\nGifted 2 subscriptions to the community!", membership_price=None)
        self.assertEqual(kick.amount, "")


class KickCoinTests(unittest.TestCase):
    def test_five_hundred_coins_are_a_super_chat(self):
        parsed = parse_entry("KickChatter\nsent Cat Jam\n500")
        self.assertEqual(parsed.platform, "Kick")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.amount, "5.00")
        self.assertEqual(parsed.currency, "USD")
        self.assertEqual(parsed.message, "Cat Jam")

    def test_coins_below_five_hundred_are_a_super_sticker(self):
        parsed = parse_entry("KickChatter\nSubscriber\nsent Cat Jam\n250")
        self.assertEqual(parsed.platform, "Kick")
        self.assertEqual(parsed.donation_type, "Super Sticker")
        self.assertEqual(parsed.amount, "2.50")
        self.assertEqual(parsed.message, "Cat Jam")

    def test_chat_line_between_sent_and_coins_is_the_message(self):
        parsed = parse_entry("\n".join([
            "predawncorpse",
            " sent Rage Quit",
            "@SmashJT you should co-stream with Channel 404 News when doing the MMN segment! Also...YOU'RE GAY!",
            "500",
        ]))
        self.assertEqual(parsed.platform, "Kick")
        self.assertEqual(parsed.donor, "predawncorpse")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.amount, "5.00")
        self.assertEqual(parsed.currency, "USD")
        self.assertEqual(
            parsed.message,
            "@SmashJT you should co-stream with Channel 404 News when doing the MMN segment! Also...YOU'RE GAY!",
        )

    def test_blank_line_between_sent_and_coins(self):
        parsed = parse_entry("KickChatter\nsent Cat Jam\n\n250")
        self.assertEqual(parsed.platform, "Kick")
        self.assertEqual(parsed.donation_type, "Super Sticker")
        self.assertEqual(parsed.amount, "2.50")
        self.assertEqual(parsed.message, "Cat Jam")

    def test_four_hundred_ninety_nine_coins_are_a_super_sticker(self):
        parsed = parse_entry("KickChatter\nsent Cat Jam\n499")
        self.assertEqual(parsed.platform, "Kick")
        self.assertEqual(parsed.donation_type, "Super Sticker")
        self.assertEqual(parsed.amount, "4.99")

    def test_comma_grouped_coins(self):
        parsed = parse_entry("\n".join([
            "manikantan_r",
            " sent YAP",
            "MEMECOPIUM IS BEING A BLIZZARD REEEETARUDURO. SO HERE FOR YOUR STRUGGLEB US",
            "2,000",
        ]))
        self.assertEqual(parsed.platform, "Kick")
        self.assertEqual(parsed.donor, "manikantan_r")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.amount, "20.00")
        self.assertEqual(parsed.currency, "USD")
        self.assertEqual(
            parsed.message,
            "MEMECOPIUM IS BEING A BLIZZARD REEEETARUDURO. SO HERE FOR YOUR STRUGGLEB US",
        )


class ExtensionTests(unittest.TestCase):
    def test_youtube_one_liner(self):
        parsed = parse_entry(
            "[YouTube] @Wild_Man_of_Yavin: $5.00 - Here is five for the chat. "
            "Spend a lil bit of time to caramelize onions. Just on low. Sit on low with some butter. "
            "Add that to your burger anyways, o7"
        )
        self.assertEqual(parsed.platform, "YouTube")
        self.assertEqual(parsed.donor, "Wild_Man_of_Yavin")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.amount, "5.00")
        self.assertEqual(parsed.currency, "USD")
        self.assertEqual(
            parsed.message,
            "Here is five for the chat. Spend a lil bit of time to caramelize onions. "
            "Just on low. Sit on low with some butter. Add that to your burger anyways, o7",
        )

    def test_username_without_at_keeps_text_after_the_first_dash(self):
        parsed = parse_entry("[Kick] maple: CA$10.00 - raid - now")
        self.assertEqual(parsed.platform, "Kick")
        self.assertEqual(parsed.donor, "maple")
        self.assertEqual(parsed.amount, "10.00")
        self.assertEqual(parsed.currency, "CAD")
        self.assertEqual(parsed.message, "raid - now")

    def test_gbp_extension_line(self):
        parsed = parse_entry(
            "[YouTube] @tamamotakamori5025: £5.00 - I know some elderly gay couples and my lord "
            "they are the most chill people ever, they are so funny and have never once said they were"
        )
        self.assertEqual(parsed.platform, "YouTube")
        self.assertEqual(parsed.donor, "tamamotakamori5025")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.amount, "5.00")
        self.assertEqual(parsed.currency, "GBP")
        self.assertEqual(
            parsed.message,
            "I know some elderly gay couples and my lord they are the most chill people ever, "
            "they are so funny and have never once said they were",
        )

    def test_wrapped_one_liner_is_joined(self):
        parsed = parse_entry("\n".join([
            "[YouTube] @tamamotakamori5025: £5.00 - I know some elderly gay couples and my lord they are the most chill people ever, they",
            "are so funny and have never once said they were",
        ]))
        self.assertEqual(parsed.donor, "tamamotakamori5025")
        self.assertEqual(parsed.amount, "5.00")
        self.assertEqual(parsed.currency, "GBP")
        self.assertIn("elderly gay couples", parsed.message)
        self.assertIn("never once said they were", parsed.message)

    def test_fullwidth_and_lira_pounds_map_to_gbp(self):
        for pound in ("\u00a3", "\uffe1", "\u20a4"):
            parsed = parse_entry(f"[YouTube] @poundfan: {pound}5.00 - hello")
            self.assertEqual(parsed.donor, "poundfan")
            self.assertEqual(parsed.amount, "5.00")
            self.assertEqual(parsed.currency, "GBP")
            self.assertEqual(parsed.message, "hello")

    def test_bracketed_other_is_not_other(self):
        parsed = parse_entry("[Other] Anon: $1.00 - cash")
        self.assertNotEqual(parsed.platform, "Other")
        self.assertNotEqual(parsed.donation_type, "Donation")
        self.assertNotEqual(parsed.donation_type, "Digest")


class DefaultTests(unittest.TestCase):
    def test_blank_input(self):
        parsed = parse_entry("   ")
        self.assertEqual(parsed.donor, "")
        self.assertEqual(parsed.amount, "")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.platform, "YouTube")

    def test_rumble_rant_with_a_blank_line_after_the_name(self):
        parsed = parse_entry("\n".join([
            "SergeantSapient",
            "",
            "$1",
            "I'm personally happy modern Christians are as moderate as they are.",
        ]))
        self.assertEqual(parsed.platform, "Rumble")
        self.assertEqual(parsed.donation_type, "Super Chat")
        self.assertEqual(parsed.amount, "1.00")
        self.assertEqual(parsed.currency, "USD")
        self.assertEqual(parsed.message, "I'm personally happy modern Christians are as moderate as they are.")

    def test_paste_never_returns_other_donation_or_digest(self):
        samples = [
            "",
            "predawncorpses\n$0.99\nKeep going brother",
            "@MapleFan\nWelcome to Somebody!",
            "KickChatter\nGifted 1 subscription to the community!\n1",
            "KickChatter\nsent Cat Jam\n500",
            "predawncorpse\n sent Rage Quit\n@SmashJT you should co-stream with Channel 404 News when doing the MMN segment! Also...YOU'RE GAY!\n500",
            "manikantan_r\n sent YAP\nMEMECOPIUM IS BEING A BLIZZARD REEEETARUDURO. SO HERE FOR YOUR STRUGGLEB US\n2,000",
            "RumbleFan\n\n$5.00 this is a rant",
            "just some leftover text",
            "[YouTube] @tamamotakamori5025: £5.00 - I know some elderly gay couples",
        ]
        for body in samples:
            parsed = parse_entry(body)
            self.assertNotEqual(parsed.platform, "Other", body)
            self.assertNotEqual(parsed.donation_type, "Donation", body)
            self.assertNotEqual(parsed.donation_type, "Digest", body)
        compact = parse_entry("[YouTube] @Wild_Man_of_Yavin: $5.00 - Here is five for the chat.")
        self.assertNotEqual(compact.platform, "Other")
        self.assertNotEqual(compact.donation_type, "Donation")
        self.assertNotEqual(compact.donation_type, "Digest")


class FormFieldTests(unittest.TestCase):
    def test_usd_amount_is_copied_through(self):
        fields = paste_for_form("predawncorpses\n$0.99\nKeep going brother", 4.99, {"USD": 1.0})
        self.assertEqual(fields["donor"], "predawncorpses")
        self.assertEqual(fields["platform"], "youtube")
        self.assertEqual(fields["kind"], "super_chat")
        self.assertEqual(fields["amount"], "0.99")
        self.assertEqual(fields["currency"], "USD")
        self.assertEqual(fields["amount_usd"], "0.99")
        self.assertEqual(fields["message"], "Keep going brother")

    def test_peso_paste_keeps_php_and_converts_to_usd(self):
        fields = paste_for_form(
            "@kayeend\n₱50.00\n\njuuuuuuuuuuuuicy tip\n\n1",
            4.99,
            {"USD": 1.0, "PHP": 62.5},
        )
        self.assertEqual(fields["donor"], "kayeend")
        self.assertEqual(fields["amount"], "50.00")
        self.assertEqual(fields["currency"], "PHP")
        self.assertEqual(fields["amount_usd"], "0.80")
        self.assertEqual(fields["message"], "juuuuuuuuuuuuicy tip")

    def test_foreign_amount_converts_when_a_rate_exists(self):
        fields = paste_for_form("RupeeFan\n₹777.77\nLove from India", 4.99, {"USD": 1.0, "INR": 90.0})
        self.assertEqual(fields["amount"], "777.77")
        self.assertEqual(fields["currency"], "INR")
        self.assertEqual(fields["amount_usd"], "8.64")

    def test_foreign_amount_stays_blank_without_a_rate(self):
        fields = paste_for_form("RupeeFan\n₹777.77\nLove from India", 4.99, {"USD": 1.0})
        self.assertEqual(fields["amount"], "777.77")
        self.assertIsNone(fields["amount_usd"])

    def test_other_tag_stays_on_youtube_super_chat(self):
        fields = paste_for_form("[Other] Anon: $1.00 - cash", 4.99, {"USD": 1.0})
        self.assertEqual(fields["platform"], "youtube")
        self.assertEqual(fields["kind"], "super_chat")
        self.assertNotEqual(fields["kind"], "donation")


class PasteApiTests(unittest.TestCase):
    def setUp(self):
        reset_rate_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        now = time.time()
        self.store.set_setting("fx_attempt_at", str(now))
        self.store.set_setting("fx_checked_at", str(now))
        self.store.set_setting("fx_response_json", json.dumps({
            "result": "success",
            "base_code": "USD",
            "rates": {"USD": 1, "INR": 90, "CAD": 1.36, "GBP": 0.75},
        }))
        self.token = "b" * 32
        self.app = App(self.store, self.token, 1)
        self.server = ThreadingServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.app.ui_port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.app.ui_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.stop_webhook()
        self.store.close()
        self.tmp.cleanup()
        reset_rate_cache()

    def request(self, path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Cookie": f"sd_session={self.token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            finally:
                exc.close()

    def test_paste_fills_fields_and_does_not_insert_a_row(self):
        self.store.set_setting("membership_price", "4.99")
        status, body = self.request("/api/paste", "POST", {
            "text": "predawncorpses\n$0.99\nKeep going brother",
        })
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["donor"], "predawncorpses")
        self.assertEqual(payload["amount_usd"], "0.99")
        self.assertEqual(payload["platform"], "youtube")
        self.assertEqual(payload["kind"], "super_chat")
        status, body = self.request("/api/donations")
        self.assertEqual(json.loads(body)["total"], 0)
        logged = " ".join(str(row.get("message") or "") for row in self.store.recent_log())
        self.assertNotIn("Keep going brother", logged)

    def test_saved_membership_price_and_a_higher_rumble_price(self):
        self.store.set_setting("membership_price", "6.00")
        status, body = self.request("/api/paste", "POST", {
            "text": "RumbleFan\nGifted 3 subscriptions to SmashJT",
        })
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["kind"], "membership")
        self.assertEqual(payload["platform"], "rumble")
        self.assertEqual(payload["amount_usd"], "16.00")
        status, body = self.request("/api/paste", "POST", {
            "text": "GenerousFan\nSent 3 Smashers gift memberships",
        })
        self.assertEqual(json.loads(body)["amount_usd"], "18.00")

    def test_empty_membership_price_leaves_dollars_blank(self):
        self.store.set_setting("membership_price", "")
        status, body = self.request("/api/paste", "POST", {
            "text": "GenerousFan\nSent 5 Smashers gift memberships",
        })
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["kind"], "membership")
        self.assertEqual(payload["amount"], "")
        self.assertIsNone(payload["amount_usd"])
        self.assertEqual(payload["membership_count"], 5)

    def test_saved_rate_converts_rupees(self):
        status, body = self.request("/api/paste", "POST", {
            "text": "RupeeFan\n₹777.77\nLove from India",
        })
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["amount"], "777.77")
        self.assertEqual(payload["currency"], "INR")
        self.assertEqual(payload["amount_usd"], "8.64")

    def test_rejects_a_paste_that_is_too_long_or_not_text(self):
        status, body = self.request("/api/paste", "POST", {"text": "x" * 8001})
        self.assertEqual(status, 400)
        self.assertIn(b"8000", body)
        status, body = self.request("/api/paste", "POST", {"text": 5})
        self.assertEqual(status, 400)
        status, body = self.request("/api/donations")
        self.assertEqual(json.loads(body)["total"], 0)


if __name__ == "__main__":
    unittest.main()
