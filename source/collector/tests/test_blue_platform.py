"""BLUE 1.10: PLATFORM, raw amount, currency, mentions, blurbs, and the chat guards."""

import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from streamdonations.blue import BlueEvent, evaluate, inspect_script
from streamdonations.blue_host import CHAT_LINES_PER_MINUTE, BlueHost
from streamdonations.commands import LIMITS, blurb, mention, total_reply
from streamdonations.money import currency_decimals, format_raw_amount, raw_amount
from streamdonations.normalize import Donation
from streamdonations.store import Store
from streamdonations.util import utc_now_iso


def _event(name="donation", platform="youtube", donor="Ada", amount=20.0, raw=None, currency="USD",
           lookup=None, extra=None):
    values = {"platform": platform, "donor": donor, "type": "Super Chat", "amount": "", "currency": currency}
    numbers = {"minutes": 0, "tier": 0}
    if amount is not None:
        numbers["amount"] = amount
    if raw is not None:
        numbers["rawamount"] = raw
    placeholders = {
        "donor": donor,
        "mention": mention(donor),
        "rawamount": "" if raw is None else format_raw_amount(raw, currency),
        "currency": currency,
        "platform": platform,
    }
    placeholders.update(extra or {})
    return BlueEvent(name, values, numbers, time(12, 0), placeholders, lookup)


def _targets(source, event):
    return [(item.target, item.via_platform) for item in evaluate(source, event).actions]


class PlatformTargetTests(unittest.TestCase):
    def test_platform_is_the_gift_platform(self):
        source = 'ON DONATION IF AMOUNT IS ABOVE 49.89 THEN SEND CHAT TO PLATFORM "Thank you {MENTION}!"'
        for platform in ("youtube", "rumble", "kick"):
            run = evaluate(source, _event(platform=platform, amount=49.90))
            self.assertEqual([(a.kind, a.target, a.via_platform) for a in run.actions], [("chat", platform, True)])
            self.assertEqual(run.actions[0].text, "Thank you @Ada!")
        self.assertEqual(evaluate(source, _event(amount=49.89)).actions, [])

    def test_other_platform_resolves_to_no_chat(self):
        self.assertEqual(_targets('ON DONATION THEN SEND CHAT TO PLATFORM "x"', _event(platform="other")), [("", True)])

    def test_named_target_wins_and_is_sent_once(self):
        self.assertEqual(
            _targets('ON DONATION THEN SEND CHAT TO PLATFORM AND YOUTUBE "x"', _event(platform="youtube")),
            [("youtube", False)],
        )
        self.assertEqual(
            _targets('ON DONATION THEN SEND CHAT TO YOUTUBE AND PLATFORM "x"', _event(platform="kick")),
            [("youtube", False), ("kick", True)],
        )

    def test_not_platform_and_exclusions(self):
        self.assertEqual(
            _targets('ON DONATION THEN SEND CHAT TO ALL AND NOT PLATFORM "x"', _event(platform="kick")),
            [("youtube", False), ("rumble", False)],
        )
        self.assertEqual(_targets('ON DONATION THEN SEND CHAT TO PLATFORM AND NOT KICK "x"', _event(platform="kick")), [])
        self.assertEqual(
            _targets('ON DONATION THEN SEND CHAT TO PLATFORM AND NOT KICK "x"', _event(platform="rumble")),
            [("rumble", True)],
        )
        self.assertEqual(_targets('ON DONATION THEN SEND CHAT TO PLATFORM AND NOT PLATFORM "x"', _event()), [])

    def test_platform_on_a_timer_is_a_syntax_error(self):
        problems = inspect_script('ON TIMER IF MINUTES MODULO 5 EQUALS 0 THEN SEND CHAT TO PLATFORM "x"')
        self.assertEqual(problems, [{"line": 1, "message": "A timer has no PLATFORM to send to."}])
        self.assertEqual(inspect_script('ON GOAL THEN SEND CHAT TO PLATFORM "x"'), [])

    def test_goal_platform_and_tier_zero(self):
        source = 'ON GOAL THEN SEND CHAT TO PLATFORM "Tier {TIER} {MENTION}"'
        reached = _event(name="goal", platform="rumble", extra={"tier": "1"})
        self.assertEqual(_targets(source, reached), [("rumble", True)])
        start = _event(name="goal", platform="", donor="", extra={"tier": "0"})
        self.assertEqual(_targets(source, start), [("", True)])

    def test_platform_is_not_a_notice_target(self):
        self.assertTrue(inspect_script('ON DONATION THEN SEND NOTICE TO PLATFORM "x"'))

    def test_existing_targets_are_unchanged(self):
        self.assertEqual(
            _targets('ON DONATION THEN SEND CHAT TO ALL AND NOT RUMBLE "x"', _event()),
            [("youtube", False), ("kick", False)],
        )


class RawAmountAndCurrencyTests(unittest.TestCase):
    def test_raw_amount_field_and_aliases(self):
        yen = _event(amount=6.70, raw=1000.0, currency="JPY")
        for field in ("RAW AMOUNT", "raw amount", "RAWAMOUNT", "Raw_Amount", "raw-amount"):
            source = f'ON DONATION IF {field} IS ABOVE 999 THEN SEND NOTICE TO LOG "{{RAW_AMOUNT}} {{CURRENCY}}"'
            run = evaluate(source, yen)
            self.assertEqual(run.problems, [], field)
            self.assertEqual([a.text for a in run.actions], ["1000 JPY"], field)
        self.assertEqual(evaluate('ON DONATION IF AMOUNT ABOVE 999 THEN SEND NOTICE TO LOG "x"', yen).actions, [])

    def test_raw_amount_compares_the_unrounded_number(self):
        event = _event(raw=1.2345, currency="KWD")
        self.assertTrue(evaluate('ON DONATION IF RAW AMOUNT ABOVE 1.234 THEN SEND NOTICE TO LOG "x"', event).actions)
        self.assertEqual(evaluate('ON DONATION IF RAWAMOUNT EQUALS 1.23 THEN SEND NOTICE TO LOG "x"', event).actions, [])
        none = _event(raw=None, currency="")
        self.assertEqual(evaluate('ON DONATION IF RAW AMOUNT ABOVE 0 THEN SEND NOTICE TO LOG "x"', none).actions, [])
        self.assertEqual(evaluate('ON DONATION THEN SEND NOTICE TO LOG "[{RAWAMOUNT}]"', none).actions[0].text, "[]")

    def test_raw_is_only_valid_before_amount(self):
        for line in (
            'ON DONATION IF RAW DONOR EQUALS x THEN SEND NOTICE TO LOG "x"',
            'ON DONATION IF RAW THEN SEND NOTICE TO LOG "x"',
            'ON RAW DONATION THEN SEND NOTICE TO LOG "x"',
            'ON DONATION THEN SEND RAW CHAT TO YOUTUBE "x"',
        ):
            self.assertEqual(inspect_script(line), [{"line": 1, "message": "RAW only goes before AMOUNT."}], line)
        self.assertEqual(inspect_script('ON DONATION THEN SEND NOTICE TO LOG "raw words are fine here"'), [])

    def test_currency_field(self):
        yen = _event(raw=500.0, currency="JPY")
        self.assertTrue(evaluate('ON DONATION IF CURRENCY IS JPY THEN SEND NOTICE TO LOG "x"', yen).actions)
        self.assertTrue(evaluate('ON DONATION IF CURRENCY EQUALS jpy THEN SEND NOTICE TO LOG "x"', yen).actions)
        self.assertEqual(evaluate('ON DONATION IF CURRENCY IS NOT JPY THEN SEND NOTICE TO LOG "x"', yen).actions, [])
        self.assertTrue(evaluate(
            'ON DONATION IF CURRENCY IS JPY AND RAW AMOUNT ABOVE 499 THEN SEND NOTICE TO LOG "x"', yen
        ).actions)
        kicks = _event(platform="kick", raw=100.0, currency="KICKS")
        for value in ("KICKS", "kick", "Kicks"):
            self.assertTrue(evaluate(f'ON DONATION IF CURRENCY IS {value} THEN SEND NOTICE TO LOG "x"', kicks).actions)
        blank = _event(raw=None, currency="")
        self.assertEqual(evaluate('ON DONATION IF CURRENCY EQUALS USD THEN SEND NOTICE TO LOG "x"', blank).actions, [])

    def test_is_still_reads_as_filler_before_an_operator(self):
        event = _event(platform="kick", amount=10.0)
        self.assertTrue(evaluate('ON DONATION IF AMOUNT IS ABOVE 5 THEN SEND NOTICE TO LOG "x"', event).actions)
        self.assertTrue(evaluate('ON DONATION IF AMOUNT IS GREATER ABOVE 5 THEN SEND NOTICE TO LOG "x"', event).actions)
        self.assertTrue(evaluate('ON DONATION IF PLATFORM IS KICK THEN SEND NOTICE TO LOG "x"', event).actions)
        self.assertTrue(evaluate('ON DONATION IF PLATFORM IS EQUAL KICK THEN SEND NOTICE TO LOG "x"', event).actions)
        # A word where AMOUNT needs a number is reported (1.9.2), with IS as well.
        self.assertEqual(
            inspect_script('ON DONATION IF AMOUNT IS GREAT THEN SEND NOTICE TO LOG "x"'),
            [{"line": 1, "message": "AMOUNT needs a number."}],
        )

    def test_money_helpers(self):
        self.assertEqual(currency_decimals("JPY"), 0)
        self.assertEqual(currency_decimals("krw"), 0)
        self.assertEqual(currency_decimals("KWD"), 3)
        self.assertEqual(currency_decimals("USD"), 2)
        self.assertEqual(currency_decimals(None), 2)
        self.assertEqual(format_raw_amount(1500, "JPY"), "1500")
        self.assertEqual(format_raw_amount(1500.4, "JPY"), "1500")
        self.assertEqual(format_raw_amount(12345.5, "USD"), "12345.50")
        self.assertEqual(format_raw_amount(1.2345, "KWD"), "1.234")
        self.assertEqual(format_raw_amount(250, "KICKS"), "250")
        self.assertEqual(format_raw_amount(None, "USD"), "")
        self.assertEqual(raw_amount({"kind": "super_chat", "amount_value": 500, "currency": "JPY", "platform": "youtube"}),
                         (500.0, "JPY"))
        self.assertEqual(raw_amount({"kind": "kicks", "amount_value": 250, "currency": "KICKS", "platform": "kick"}),
                         (250.0, "KICKS"))
        self.assertEqual(raw_amount({"kind": "membership", "amount_value": None, "currency": None,
                                     "platform": "youtube"}), (None, None))
        self.assertEqual(raw_amount({"kind": "gift_membership_received", "amount_value": 5, "currency": "USD"}),
                         (None, None))
        self.assertEqual(raw_amount({"kind": "rant", "amount_value": 2.0, "currency": "USD", "platform": "rumble"}),
                         (2.0, "USD"))


class PlaceholderTests(unittest.TestCase):
    def test_mention(self):
        self.assertEqual(mention("Ada"), "@Ada")
        self.assertEqual(mention("@Ada"), "@Ada")
        for name in ("", "Anonymous", "anonymous", "Someone", "—"):
            self.assertEqual(mention(name), "", name)
        anon = _event(donor="Anonymous")
        self.assertEqual(evaluate('ON DONATION THEN SEND NOTICE TO LOG "Thanks {MENTION}!"', anon).actions[0].text,
                         "Thanks !")

    def test_underscores_and_unknown_names(self):
        event = _event(raw=5.0)
        run = evaluate('ON DONATION THEN SEND NOTICE TO LOG "{Raw_Amount} {RAWAMOUNT} {NOT_A_THING} {DONOR}"', event)
        self.assertEqual(run.actions[0].text, "5.00 5.00 {NOT_A_THING} Ada")

    def test_lookup_is_filled_per_target_after_the_rest(self):
        seen = []

        def total(target, others, count):
            seen.append((target, others, count))
            return f"T-{target}"

        lookup = {"totalline": total, "topline": lambda target, others, count: f"P-{target}"}
        run = evaluate(
            'ON DONATION THEN SEND CHAT TO YOUTUBE AND KICK "Hi {DONOR}. {TOTALLINE} {TOP_BLURB}"',
            _event(lookup=lookup),
        )
        self.assertEqual([a.text for a in run.actions], ["Hi Ada. T-youtube P-youtube", "Hi Ada. T-kick P-kick"])
        self.assertEqual(seen[0], ("youtube", len("Hi Ada."), 2))
        self.assertEqual(evaluate('ON DONATION THEN SEND NOTICE TO LOG "{TOTAL_LINE}"', _event(lookup=lookup))
                         .actions[0].text, "T-log")

    def test_blurb_builder_is_shared(self):
        tab = {"tiers_enabled": "0", "goal_tiers": "", "chat_extra": "", "show_top_donors": "1"}
        rows = [{"author_name": "Ada", "amount_usd": 10.0, "platform": "youtube", "platform_label": "YouTube"}]
        self.assertEqual(blurb("total", tab, rows, 200), total_reply(tab, rows, 200))
        self.assertIn("Ada", blurb("top", tab, rows, 200))
        self.assertEqual(blurb("top", dict(tab, show_top_donors="0"), rows, 200), "")


def _gift(key, platform="youtube", donor="Ada", amount=10.0, currency="USD", kind=None, channel="",
          created=None, raw=None):
    return Donation(
        dedupe_key=key, platform=platform,
        kind=kind or {"youtube": "super_chat", "kick": "kicks", "rumble": "rant"}[platform],
        external_id=key, channel=channel, author_name=donor, author_id="", message="",
        amount_value=amount, amount_micros=None if amount is None else int(amount * 1_000_000),
        currency=currency, created_at=created or utc_now_iso(), raw=raw or {},
    )


class HostTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "donations.db")
        self.addCleanup(self.store.close)
        self.allowed = {"youtube", "kick", "rumble"}
        self.service = SimpleNamespace(
            store=self.store,
            commands=SimpleNamespace(can_send=lambda platform: platform in self.allowed),
            youtube=SimpleNamespace(_chats=[]),
        )
        self.host = BlueHost(self.service)
        self.now = 1000.0
        self.host._clock = lambda: self.now
        self.store.on_fresh_donations = self.host.on_donations
        self.stream = self.store._active_stream_id()
        self.store.set_stream_paused(None, False)

    def script(self, source, started=None):
        saved = self.store.save_blue_script("Thanks", source)
        self.store.set_stream_values(self.stream, {
            "blue_script_id": saved["id"],
            "blue_started_at": started or (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "blue_timer_minute": "0",
        })

    def logged(self):
        return [row["message"] for row in self.store.recent_log()]

    def test_age_guard_skips_old_and_pre_start_gifts(self):
        self.script('ON DONATION THEN SEND NOTICE TO APP "hi {DONOR}"')
        old = (datetime.now(timezone.utc) - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.store.add_many([_gift("youtube:public:old", created=old), _gift("youtube:public:old2", created=old)])
        self.assertEqual(self.host.claim(), [])
        self.assertEqual(sum("older than 2 minutes" in line for line in self.logged()), 1)
        self.store.add_many([_gift("youtube:public:new", donor="Bea")])
        self.assertEqual([item["text"] for item in self.host.claim()], ["hi Bea"])

    def test_gift_from_before_the_script_started_is_skipped(self):
        self.script('ON DONATION THEN SEND NOTICE TO APP "hi"', started=utc_now_iso())
        before = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.store.add_many([_gift("youtube:public:early", created=before)])
        self.assertEqual(self.host.claim(), [])

    def test_platform_answers_a_public_gift_and_trims(self):
        self.script('ON DONATION IF AMOUNT IS ABOVE 49.89 THEN SEND CHAT TO PLATFORM "Thank you {MENTION}! ' + "x" * 300 + '"')
        self.store.add_many([_gift("youtube:public:1", amount=49.89)])
        self.assertEqual(self.host.claim(), [])
        self.store.add_many([_gift("youtube:public:2", amount=50.0)])
        self.store.add_many([_gift("kick:public:kicks:3", platform="kick", amount=6000.0, currency="KICKS")])
        pending = self.host.claim()
        self.assertEqual([item["target"] for item in pending], ["youtube", "kick"])
        self.assertTrue(pending[0]["text"].startswith("Thank you @Ada! "))
        self.assertEqual(len(pending[0]["text"]), LIMITS["youtube"])
        self.assertEqual(len(pending[1]["text"]), len("Thank you @Ada! ") + 300)

    def test_platform_skips_manual_entries_once_logged(self):
        self.script('ON DONATION THEN SEND CHAT TO PLATFORM "thanks"\nON DONATION THEN SEND NOTICE TO APP "seen"')
        for _ in range(2):
            self.store.add_manual_entry(author_name="Ada", platform="youtube", kind="super_chat", amount=5)
        self.assertEqual([item["target"] for item in self.host.claim()], ["app", "app"])
        self.assertEqual(sum("manual or pasted" in line for line in self.logged()), 1)

    def test_platform_skips_when_the_chat_cannot_send(self):
        self.allowed = set()
        self.script('ON DONATION THEN SEND CHAT TO PLATFORM "thanks"')
        self.store.add_many([_gift("youtube:public:a"), _gift("youtube:public:b", donor="Bea")])
        self.assertEqual(self.host.claim(), [])
        self.assertEqual(sum("cannot be sent from here" in line for line in self.logged()), 1)

    def test_named_targets_do_not_get_the_platform_checks(self):
        self.allowed = set()
        self.script('ON DONATION THEN SEND CHAT TO YOUTUBE "thanks"')
        self.store.add_manual_entry(author_name="Ada", platform="youtube", kind="super_chat", amount=5)
        self.assertEqual([item["target"] for item in self.host.claim()], ["youtube"])

    def test_signed_in_youtube_gift_needs_a_matching_chat(self):
        self.script('ON DONATION THEN SEND CHAT TO PLATFORM "thanks {DONOR}"')
        self.store.set_setting("youtube_watch", "https://www.youtube.com/watch?v=abcdefghijk")
        self.store.add_many([_gift("youtube:superChatEvent:1", channel="UCowner")])
        self.assertEqual(self.host.claim(), [])
        self.assertTrue(any("could not confirm" in line for line in self.logged()))
        self.service.youtube._chats = [{"id": "chat", "channel": "UCowner", "title": "", "video": "abcdefghijk"}]
        self.store.add_many([_gift("youtube:superChatEvent:2", donor="Bea", channel="UCowner")])
        self.assertEqual([item["text"] for item in self.host.claim()], ["thanks Bea"])

    def test_signed_in_youtube_gift_matches_a_channel_id(self):
        channel = "UC" + "a" * 22
        self.script('ON DONATION THEN SEND CHAT TO PLATFORM "thanks"')
        self.store.set_setting("youtube_channel", "https://www.youtube.com/channel/" + channel)
        self.store.add_many([_gift("youtube:liveChat:1", kind="membership", amount=None, currency=None,
                                   channel=channel)])
        self.assertEqual([item["target"] for item in self.host.claim()], ["youtube"])

    def test_kick_webhook_gift_needs_the_ledger_channel(self):
        self.script('ON DONATION THEN SEND CHAT TO PLATFORM "{RAWAMOUNT} {CURRENCY}"')
        self.store.set_setting("kick_watch", "streamer")
        self.store.add_many([_gift("kick:kicks.gifted:1", platform="kick", amount=100.0, currency="KICKS",
                                   channel="someone-else")])
        self.assertEqual(self.host.claim(), [])
        self.store.add_many([_gift("kick:kicks.gifted:2", platform="kick", donor="Bea", amount=100.0,
                                   currency="KICKS", channel="Streamer")])
        self.assertEqual([item["text"] for item in self.host.claim()], ["100 KICKS"])

    def test_rumble_owner_gift_needs_the_ledger_channel(self):
        self.script('ON DONATION THEN SEND CHAT TO PLATFORM "thanks"')
        self.store.add_many([_gift("rumble:rant:1", platform="rumble", channel="MyShow")])
        self.assertEqual(self.host.claim(), [])
        self.store.set_setting("rumble_channel", "https://rumble.com/c/MyShow")
        self.store.add_many([_gift("rumble:rant:2", platform="rumble", donor="Bea", channel="MyShow")])
        self.assertEqual([item["target"] for item in self.host.claim()], ["rumble"])

    def test_rumble_channel_tab_and_query_still_match(self):
        self.script('ON DONATION THEN SEND CHAT TO PLATFORM "thanks {DONOR}"')
        for index, saved in enumerate((
            "https://rumble.com/c/MyShow/livestreams",
            "https://rumble.com/c/MyShow/videos/",
            "https://rumble.com/user/MyShow/live",
            "https://rumble.com/c/MyShow?ref=1",
        )):
            self.store.set_setting("rumble_channel", saved)
            self.store.add_many([_gift(f"rumble:rant:t{index}", platform="rumble", donor=f"D{index}", channel="myshow")])
            self.assertEqual([item["text"] for item in self.host.claim()], [f"thanks D{index}"], saved)
        self.store.set_setting("rumble_channel", "https://rumble.com/c/Other/livestreams")
        self.store.add_many([_gift("rumble:rant:no", platform="rumble", donor="No", channel="MyShow")])
        self.assertEqual(self.host.claim(), [])

    def test_other_platform_rows_are_logged_once(self):
        self.script('ON DONATION THEN SEND CHAT TO PLATFORM "thanks"\nON DONATION THEN SEND NOTICE TO APP "seen"')
        for index in range(2):
            self.store.add_many([_gift(f"other:{index}", platform="other", kind="donation", donor=f"D{index}")])
        self.assertEqual([item["target"] for item in self.host.claim()], ["app", "app"])
        self.assertEqual(sum("Other-platform" in line for line in self.logged()), 1)

    def test_tier_zero_goal_platform_stays_quiet(self):
        self.script('ON GOAL THEN SEND CHAT TO PLATFORM "x"')
        self.host._start_body(self.stream)
        self.assertEqual(self.host.claim(), [])
        self.assertFalse(any("PLATFORM" in line for line in self.logged()), self.logged())

    def _start_goal(self, gift):
        from streamdonations.goals import GoalTier, dump_tiers

        self.store.set_setting("tiers_enabled", "1")
        self.store.set_setting("goal_tiers", dump_tiers([GoalTier(True, 10, "Bronze", "")]))
        self.store.add_many([gift])
        self.script('ON GOAL IF TIER ABOVE 0 THEN SEND CHAT TO PLATFORM "{NAME} {MENTION}"')
        self.host._start_body(self.stream)
        return self.host.claim()

    def test_start_goal_platform_uses_the_stored_channel(self):
        channel = "UC" + "b" * 22
        self.store.set_setting("youtube_channel", "https://www.youtube.com/channel/" + channel)
        pending = self._start_goal(_gift("youtube:superChatEvent:9", donor="Cam", amount=20.0, channel=channel))
        self.assertEqual([(item["target"], item["text"]) for item in pending], [("youtube", "Bronze @Cam")])

    def test_start_goal_platform_still_refuses_another_chat(self):
        self.store.set_setting("youtube_channel", "https://www.youtube.com/channel/UC" + "b" * 22)
        pending = self._start_goal(_gift("youtube:superChatEvent:9", donor="Cam", amount=20.0, channel="UC" + "c" * 22))
        self.assertEqual(pending, [])
        self.assertTrue(any("could not confirm" in line for line in self.logged()))

    def test_start_goal_platform_skips_a_manual_gift(self):
        from streamdonations.goals import GoalTier, dump_tiers

        self.store.set_setting("tiers_enabled", "1")
        self.store.set_setting("goal_tiers", dump_tiers([GoalTier(True, 10, "Bronze", "")]))
        self.store.add_manual_entry(author_name="Ada", platform="youtube", kind="super_chat", amount=20)
        self.script('ON GOAL IF TIER ABOVE 0 THEN SEND CHAT TO PLATFORM "x"')
        self.host._start_body(self.stream)
        self.assertEqual(self.host.claim(), [])
        self.assertTrue(any("manual or pasted" in line for line in self.logged()))

    def test_the_same_gift_is_thanked_once_in_two_minutes(self):
        self.script('ON DONATION THEN SEND CHAT TO PLATFORM "thanks {DONOR}"\nON DONATION THEN SEND NOTICE TO APP "n"')
        self.store.add_many([_gift("youtube:public:1", amount=500.0, currency="JPY")])
        self.store.add_many([_gift("youtube:public:dupe", amount=500.0, currency="JPY")])
        self.store.add_many([_gift("youtube:public:other", amount=600.0, currency="JPY")])
        pending = self.host.claim()
        self.assertEqual([item["target"] for item in pending], ["youtube", "app", "app", "youtube", "app"])
        self.assertTrue(any("already thanked" in line for line in self.logged()))
        self.now += 121
        self.store.add_many([_gift("youtube:public:later", amount=500.0, currency="JPY")])
        self.assertEqual([item["target"] for item in self.host.claim()], ["youtube", "app"])

    def restart(self, now):
        """A new SuperBerry process on the same ledger file, with the wall clock at now."""
        self.now = now
        self.host = BlueHost(self.service)
        self.host._clock = lambda: self.now
        self.store.on_fresh_donations = self.host.on_donations

    def guard(self):
        tab = next(tab for tab in self.store.ledger_snapshots() if tab["id"] == self.stream)
        return tab["blue_chat_guard"]

    def test_a_restart_keeps_the_thank_you_guard(self):
        self.script('ON DONATION THEN SEND CHAT TO PLATFORM "thanks {DONOR}"')
        self.store.add_many([_gift("youtube:public:r1", amount=500.0, currency="JPY")])
        self.assertEqual([item["target"] for item in self.host.claim()], ["youtube"])
        self.assertNotIn("Ada", self.guard())
        self.restart(self.now + 30)
        self.store.add_many([_gift("youtube:superChatEvent:r1", amount=500.0, currency="JPY", channel="")])
        self.store.add_many([_gift("youtube:public:r1-copy", amount=500.0, currency="JPY")])
        self.assertEqual(self.host.claim(), [])
        self.assertTrue(any("already thanked" in line for line in self.logged()))
        self.restart(self.now + 91)
        self.store.add_many([_gift("youtube:public:r1-later", amount=500.0, currency="JPY")])
        self.assertEqual([item["text"] for item in self.host.claim()], ["thanks Ada"])

    def test_a_restart_keeps_the_chat_cap(self):
        self.script('ON DONATION THEN SEND CHAT TO YOUTUBE "thanks {DONOR}"')
        for index in range(4):
            self.store.add_many([_gift(f"youtube:public:c{index}", donor=f"D{index}")])
        self.assertEqual(len(self.host.claim()), 4)
        self.restart(self.now + 10)
        for index in range(4, 8):
            self.store.add_many([_gift(f"youtube:public:c{index}", donor=f"D{index}")])
        self.assertEqual([item["text"] for item in self.host.claim()], ["thanks D4", "thanks D5"])
        self.assertTrue(any("in the last minute" in line for line in self.logged()))
        self.restart(self.now + 51)
        self.store.add_many([_gift("youtube:public:c9", donor="D9")])
        self.assertEqual([item["text"] for item in self.host.claim()], ["thanks D9"])

    def test_a_clock_that_goes_back_holds_nothing_past_its_window(self):
        self.script('ON DONATION THEN SEND CHAT TO YOUTUBE "thanks {DONOR}"')
        for index in range(CHAT_LINES_PER_MINUTE):
            self.store.add_many([_gift(f"youtube:public:b{index}", donor=f"D{index}", amount=7.0)])
        self.assertEqual(len(self.host.claim()), CHAT_LINES_PER_MINUTE)
        # The wall clock jumps back an hour, then the app restarts.
        self.restart(self.now - 3600)
        self.store.add_many([_gift("youtube:public:b-dupe", donor="D0", amount=7.0)])
        self.assertEqual(self.host.claim(), [])
        self.restart(self.now + 61)
        self.store.add_many([_gift("youtube:public:b-new", donor="New", amount=7.0)])
        self.assertEqual([item["text"] for item in self.host.claim()], ["thanks New"])
        self.restart(self.now + 60)
        self.store.add_many([_gift("youtube:public:b-dupe2", donor="D1", amount=7.0)])
        self.assertEqual([item["text"] for item in self.host.claim()], ["thanks D1"])

    def test_the_guard_is_bounded_and_survives_a_soft_disconnect(self):
        import json

        from streamdonations.blue_host import _MAX_THANKED, _load_guard, _prune_guard
        from streamdonations.settings_file import TAB_KEYS

        self.assertNotIn("blue_chat_guard", TAB_KEYS)
        self.script('ON DONATION THEN SEND CHAT TO KICK "thanks {DONOR}"')
        for index in range(3):
            self.now += 21
            self.store.add_many([_gift(f"kick:public:kicks:g{index}", platform="kick", donor=f"D{index}",
                                       amount=100.0, currency="KICKS")])
        self.host.claim()
        saved = json.loads(self.guard())
        self.assertEqual(len(saved["thanked"]), 3)
        self.assertEqual(len(saved["sent"]["kick"]), 3)
        self.host._stop_body(self.stream)
        self.assertEqual(json.loads(self.guard()), saved)
        guard = _load_guard(json.dumps({
            "thanked": {f"k{index}": 5000.0 + index for index in range(_MAX_THANKED + 50)},
            "sent": {"kick": [4990.0 + index for index in range(20)], "nope": [1.0]},
        }))
        _prune_guard(guard, 5000.0)
        self.assertEqual(len(guard["thanked"]), _MAX_THANKED)
        self.assertNotIn("k0", guard["thanked"])
        self.assertEqual(len(guard["sent"]["kick"]), CHAT_LINES_PER_MINUTE)
        self.assertNotIn("nope", guard["sent"])
        self.assertEqual(_load_guard("not json"), {"thanked": {}, "sent": {}})
        self.store.set_stream_values(self.stream, {"blue_chat_guard": "{" + "x" * 20000})
        self.assertEqual(self.guard(), "")

    def test_chat_lines_are_capped_per_chat_per_minute(self):
        self.script('ON DONATION THEN SEND CHAT TO YOUTUBE "thanks {DONOR}"\nON DONATION THEN SEND NOTICE TO APP "n"')
        for index in range(CHAT_LINES_PER_MINUTE + 1):
            self.store.add_many([_gift(f"youtube:public:{index}", donor=f"D{index}")])
        pending = self.host.claim()
        self.assertEqual(sum(item["kind"] == "chat" for item in pending), CHAT_LINES_PER_MINUTE)
        self.assertEqual(sum(item["kind"] == "notice" for item in pending), CHAT_LINES_PER_MINUTE + 1)
        self.assertTrue(any("in the last minute" in line for line in self.logged()))
        self.now += 60
        self.store.add_many([_gift("youtube:public:late", donor="Late")])
        self.assertEqual([item["text"] for item in self.host.claim() if item["kind"] == "chat"], ["thanks Late"])

    def test_blurbs_fit_each_chat_and_topline_respects_hidden_donors(self):
        self.script('ON DONATION THEN SEND CHAT TO YOUTUBE AND KICK "{TOTALLINE} | {TOPLINE}"')
        self.store.add_many([_gift("youtube:public:1", donor="Ada", amount=12.0)])
        pending = self.host.claim()
        self.assertEqual([item["target"] for item in pending], ["youtube", "kick"])
        for item in pending:
            self.assertLessEqual(len(item["text"]), LIMITS[item["target"]])
            self.assertIn("$12.00", item["text"])
            self.assertIn("Ada", item["text"].split("|", 1)[1])
        self.store.set_setting("show_top_donors", "0")
        self.store.add_many([_gift("youtube:public:2", donor="Bea", amount=1.0)])
        pending = self.host.claim()
        self.assertTrue(all(item["text"].endswith("|") for item in pending), pending)

    def test_goal_platform_uses_the_gift_that_reached_the_tier(self):
        from streamdonations.goals import GoalTier, dump_tiers

        self.store.set_setting("tiers_enabled", "1")
        self.store.set_setting("goal_tiers", dump_tiers([GoalTier(True, 10, "Bronze", "")]))
        self.script('ON GOAL IF TIER ABOVE 0 THEN SEND CHAT TO PLATFORM "{NAME} {MENTION}"')
        self.store.add_many([_gift("kick:public:kicks:1", platform="kick", donor="Cam", amount=1500.0,
                                   currency="KICKS")])
        self.assertEqual([(item["target"], item["text"]) for item in self.host.claim()], [("kick", "Bronze @Cam")])


class ReadmeExampleTests(unittest.TestCase):
    def test_readme_examples_parse(self):
        readme = (Path(__file__).resolve().parents[3] / "README.md").read_text(encoding="utf-8")
        block = readme.split("### PLATFORM, raw amounts, and chat lines", 1)[1].split("```", 2)[1]
        lines = [line for line in block.splitlines() if line.strip()]
        self.assertEqual(len(lines), 5)
        self.assertEqual(inspect_script("\n".join(lines)), [])
        self.assertIn('ON DONATION IF AMOUNT IS ABOVE 49.89 THEN SEND CHAT TO PLATFORM "Thank you {MENTION}!"', lines)


class YouTubeChatsTests(unittest.TestCase):
    def test_active_chats_remember_the_video(self):
        from streamdonations.youtube import YouTubeClient

        client = YouTubeClient.__new__(YouTubeClient)
        client._api = lambda path, query, token: {
            "items": [{"id": "abcdefghijk", "snippet": {"liveChatId": "c1", "channelId": "UC1", "title": "Live"}}]
        }
        self.assertEqual(client._active_chats("token")[0]["video"], "abcdefghijk")


if __name__ == "__main__":
    unittest.main()
