"""BLUE reads one command per line and skips a line it cannot read."""

from datetime import datetime, time, timezone

from streamdonations.blue import BlueEvent, evaluate


def _goal(tier, donor="", name="", minutes=0, platform="", amount=None, amount_text=""):
    values = {"donor": donor, "name": name, "platform": platform}
    numbers = {"tier": tier, "minutes": minutes}
    if amount is not None:
        values["amount"] = amount_text
        numbers["amount"] = amount
    labels = {"youtube": "YouTube", "rumble": "Rumble", "kick": "Kick"}
    return BlueEvent(
        "goal",
        values,
        numbers,
        time(14, 30),
        {
            "tier": str(tier),
            "donor": donor,
            "name": name,
            "time": "14:30",
            "minutes": str(minutes),
            "amount": amount_text,
            "platform": labels.get(platform, platform),
            "type": "",
        },
    )


def _event(**kwargs):
    values = {
        "platform": kwargs.get("platform", "youtube"),
        "donor": kwargs.get("donor", "mclovin"),
        "type": kwargs.get("kind", "Super Chat"),
        "amount": kwargs.get("amount_text", "$20.00"),
    }
    numbers = {"minutes": kwargs.get("minutes", 0)}
    if "amount" in kwargs:
        numbers["amount"] = kwargs["amount"]
    elif "amount" not in kwargs and kwargs.get("name", "donation") == "donation":
        numbers["amount"] = 20
    return BlueEvent(
        kwargs.get("name", "donation"),
        values,
        numbers,
        kwargs.get("clock", time(14, 30)),
        {
            "donor": values["donor"],
            "amount": values["amount"],
            "platform": "YouTube" if values["platform"] == "youtube" else values["platform"],
            "type": values["type"],
            "time": "14:30",
            "minutes": str(numbers["minutes"]),
        },
    )


class BlueLanguageTests:
    def test_examples_from_the_guideline(self):
        donor = evaluate(
            "ON DONATION IF PLATFORM EQUALS 'youtube' AND DONOR EQUALS 'mclovin' "
            'THEN SEND CHAT TO \'youtube\' "@McLovin donated on YouTube."',
            _event(),
        )
        self.assertEqual([(item.target, item.text) for item in donor.actions], [
            ("youtube", "@McLovin donated on YouTube."),
        ])

        sticker = evaluate(
            "ON DONATION IF TYPE EQUALS 'super chat' OR TYPE EQUALS 'super sticker' "
            'THEN SEND CHAT TO RUMBLE "We got a chat or sticker!"',
            _event(kind="Super Sticker"),
        )
        self.assertEqual(sticker.actions[0].target, "rumble")
        self.assertEqual(sticker.actions[0].text, "We got a chat or sticker!")

        missed = evaluate(
            'ON DONATION IF AMOUNT NOT EQUAL 20 THEN SEND CHAT TO YOUTUBE "The amount is not $20."',
            _event(amount=19),
        )
        self.assertEqual(missed.actions[0].text, "The amount is not $20.")
        same = evaluate(
            'ON DONATION IF AMOUNT NOT EQUAL 20 THEN SEND CHAT TO YOUTUBE "The amount is not $20."',
            _event(amount=20),
        )
        self.assertEqual(same.actions, [])

        rude = evaluate(
            "ON DONATION IF AMOUNT ABOVE 19.99 AND DONOR NOT EQUAL 'mclovin' "
            'THEN SEND CHAT TO YOUTUBE AND NOTICE TO LOG "@McLovin sucks!"',
            _event(donor="ada", amount=20),
        )
        self.assertEqual([item.kind for item in rude.actions], ["chat", "notice"])
        self.assertEqual(rude.actions[1].target, "log")

        thanks = evaluate(
            "ON DONATION IF PLATFORM EQUALS 'youtube' AND IF AMOUNT ABOVE 49.99 "
            'THEN SEND CHAT TO ALL "Thanks @{DONOR} for donating {AMOUNT} on {PLATFORM}!"',
            _event(amount=50, amount_text="$50.00"),
        )
        self.assertEqual([item.target for item in thanks.actions], ["youtube", "rumble", "kick"])
        self.assertEqual(thanks.actions[0].text, "Thanks @mclovin for donating $50.00 on YouTube!")

        timer = evaluate(
            'ON TIMER IF MINUTES MODULO 20 EQUALS 0 THEN SEND NOTICE TO APP "It\'s been another 20 minutes!"',
            _event(name="timer", minutes=20),
        )
        self.assertEqual(timer.actions[0].target, "app")
        self.assertEqual(timer.actions[0].text, "It's been another 20 minutes!")
        skipped = evaluate(
            'ON TIMER IF MINUTES MODULUS 20 EQUALS 0 THEN SEND NOTICE TO APP "later"',
            _event(name="timer", minutes=21),
        )
        self.assertEqual(skipped.actions, [])

    def test_a_bad_line_does_not_stop_the_next(self):
        run = evaluate(
            'ON BANANA THEN SEND CHAT TO YOUTUBE "no"\n'
            "thanks for the follow\n"
            'ON DONATION THEN SEND NOTICE TO LOG "yes"\n',
            _event(),
        )
        self.assertEqual(len(run.problems), 1)
        self.assertEqual(run.problems[0].line, 1)
        self.assertEqual([(item.kind, item.text) for item in run.actions], [("notice", "yes")])

    def test_punctuation_case_and_time(self):
        run = evaluate(
            "on donation, if (platform equals 'YouTube') then send chat to youtube! \"Hi.\"",
            _event(),
        )
        self.assertEqual(run.actions[0].text, "Hi.")
        self.assertEqual(run.problems, [])
        later = evaluate(
            "ON TIMER IF TIME ABOVE '14:30' THEN SEND NOTICE TO SYSTEM \"late\"",
            _event(name="timer", clock=time(14, 31)),
        )
        self.assertEqual(later.actions[0].target, "system")
        early = evaluate(
            "ON TIMER IF TIME EQUALS '2:30 PM' THEN SEND NOTICE TO APP \"now\"",
            _event(name="timer", clock=time(14, 30)),
        )
        self.assertEqual(early.actions[0].text, "now")
        excluded = evaluate(
            'ON DONATION THEN SEND CHAT TO ALL AND NOT KICK "wide"',
            _event(),
        )
        self.assertEqual([item.target for item in excluded.actions], ["youtube", "rumble"])

    def test_or_and_keeps_and_inside_the_or(self):
        source = (
            "ON DONATION IF PLATFORM EQUALS 'youtube' OR PLATFORM EQUALS 'kick' AND AMOUNT ABOVE 10 "
            'THEN SEND NOTICE TO LOG "hit"'
        )
        self.assertEqual(evaluate(source, _event(platform="kick", amount=5)).actions, [])
        self.assertTrue(evaluate(source, _event(platform="kick", amount=11)).actions)
        self.assertTrue(evaluate(source, _event(platform="youtube", amount=0)).actions)

    def test_goal_tier_donor_and_name(self):
        start = evaluate(
            'ON GOAL IF TIER EQUALS 0 THEN SEND NOTICE TO APP "[{DONOR}] [{NAME}]"',
            _goal(0),
        )
        self.assertEqual(start.actions[0].text, "[] []")
        self.assertEqual(start.problems, [])
        met = evaluate(
            "ON GOAL IF TIER ABOVE 0 AND NAME EQUALS 'Bronze' "
            'THEN SEND CHAT TO YOUTUBE "{NAME} by {DONOR}, tier {TIER}"',
            _goal(1, donor="Ada", name="Bronze"),
        )
        self.assertEqual(met.actions[0].text, "Bronze by Ada, tier 1")
        skipped = evaluate(
            'ON GOAL IF TIER EQUALS 0 THEN SEND NOTICE TO APP "no"',
            _goal(2, donor="Ada", name="Silver"),
        )
        self.assertEqual(skipped.actions, [])
        early = evaluate(
            'ON GOAL IF MINUTES EQUALS 0 AND TIER EQUALS 0 THEN SEND NOTICE TO LOG "go"',
            _goal(0),
        )
        self.assertEqual(early.actions[0].text, "go")
        kept = evaluate('ON DONATION THEN SEND NOTICE TO APP "{TIER} {NAME}"', _event())
        self.assertEqual(kept.actions[0].text, "{TIER} {NAME}")
        gift = evaluate(
            'ON GOAL IF PLATFORM EQUALS \'youtube\' AND AMOUNT ABOVE 9 '
            'THEN SEND NOTICE TO APP "{PLATFORM} {AMOUNT}"',
            _goal(1, donor="Ada", name="Tip", platform="youtube", amount=9.99, amount_text="$9.99"),
        )
        self.assertEqual(gift.actions[0].text, "YouTube $9.99")

    def test_words_that_are_not_keywords_are_ignored(self):
        source = (
            'On donation on if amount is above 49.98, then send chat to all '
            '"@{donor} LEGEND!".'
        )
        run = evaluate(source, _event(donor="SmashJT", amount=50, amount_text="$50.00"))
        self.assertEqual(run.problems, [])
        self.assertEqual([item.target for item in run.actions], ["youtube", "rumble", "kick"])
        self.assertEqual(run.actions[0].text, "@SmashJT LEGEND!")
        self.assertEqual(evaluate(source, _event(amount=49.98)).actions, [])
        plain = evaluate(
            "ON DONATION IF THE DONOR IS EQUAL mclovin THEN SEND NOTICE TO APP \"yes\"",
            _event(),
        )
        self.assertEqual(plain.actions[0].text, "yes")
        self.assertEqual(
            evaluate(
                "ON DONATION IF THE DONOR IS EQUAL someone THEN SEND NOTICE TO APP \"yes\"",
                _event(),
            ).actions,
            [],
        )

    def test_total_and_tier_can_be_read_on_any_event(self):
        donation = BlueEvent(
            "donation",
            {"platform": "youtube", "donor": "Ada", "type": "Super Chat", "amount": "$30.00"},
            {"amount": 30, "minutes": 4, "tier": 2, "total": 55.5},
            time(14, 30),
            {
                "donor": "Ada",
                "amount": "$30.00",
                "platform": "YouTube",
                "type": "Super Chat",
                "time": "14:30",
                "minutes": "4",
                "tier": "2",
                "total": "$55.50",
            },
        )
        run = evaluate(
            "ON DONATION IF TIER ABOVE 1 AND TOTAL ABOVE 50 "
            'THEN SEND NOTICE TO APP "tier {TIER} total {TOTAL} [{NAME}]"',
            donation,
        )
        self.assertEqual(run.problems, [])
        self.assertEqual(run.actions[0].text, "tier 2 total $55.50 [{NAME}]")
        self.assertEqual(
            evaluate(
                'ON DONATION IF TOTAL MODULO 10 EQUALS 0 THEN SEND NOTICE TO APP "tens"',
                donation,
            ).actions,
            [],
        )
        even = BlueEvent(
            "timer",
            {},
            {"minutes": 20, "tier": 0, "total": 30},
            time(1, 0),
            {"time": "01:00", "minutes": "20", "tier": "0", "total": "$30.00"},
        )
        self.assertEqual(
            evaluate(
                'ON TIMER IF TOTAL MODULO 10 EQUALS 0 AND TIER EQUALS 0 '
                'THEN SEND NOTICE TO APP "{TOTAL} at tier {TIER}"',
                even,
            ).actions[0].text,
            "$30.00 at tier 0",
        )
        self.assertEqual(
            evaluate('ON DONATION IF TOTAL ABOVE 0 THEN SEND NOTICE TO APP "no"', _event()).actions,
            [],
        )


# unittest discovery wants TestCase subclasses. The methods above are the cases.
import unittest


class BlueLanguageTests(BlueLanguageTests, unittest.TestCase):
    pass


class BlueHostTests(unittest.TestCase):
    def test_connected_ledger_runs_and_a_pause_does_not(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        from streamdonations.blue_host import BlueHost
        from streamdonations.normalize import normalize_super_chat_event
        from streamdonations.store import Store
        from streamdonations.util import utc_now_iso

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "donations.db")
        self.addCleanup(store.close)
        host = BlueHost(SimpleNamespace(store=store))
        store.on_fresh_donations = host.on_donations
        saved = store.save_blue_script(
            "Thanks",
            "ON NOT A LINE\n"
            'ON DONATION IF AMOUNT ABOVE 0.50 THEN SEND CHAT TO YOUTUBE "Thanks @{DONOR}."\n'
            'ON DONATION THEN SEND NOTICE TO APP "Hello {DONOR}."\n'
            'ON TIMER IF MINUTES MODULO 20 EQUALS 0 THEN SEND NOTICE TO APP "tick"\n',
        )
        stream_id = store._active_stream_id()
        store.set_stream_paused(None, False)
        store.set_stream_values(stream_id, {
            "blue_script_id": saved["id"],
            "blue_started_at": utc_now_iso(),
            "blue_timer_minute": "0",
        })
        donation = normalize_super_chat_event({
            "id": "blue-1",
            "snippet": {
                "channelId": "UCb",
                "supporterDetails": {"displayName": "@McLovin", "channelId": "UCf"},
                "commentText": "hello",
                "createdAt": "2024-06-01T12:00:00Z",
                "amountMicros": "1000000",
                "currency": "USD",
            },
        })
        self.assertEqual(store.add_many([donation]), 1)
        pending = host.claim()
        self.assertEqual([item["target"] for item in pending], ["youtube", "app"])
        self.assertEqual(pending[0]["text"], "Thanks @McLovin.")
        self.assertEqual(pending[1]["text"], "Hello McLovin.")
        logged = " ".join(row["message"] for row in store.recent_log())
        self.assertIn("BLUE Thanks line 1:", logged)
        self.assertNotIn("Thanks @McLovin", logged)

        store.set_stream_paused(None, True)
        host.on_connection(stream_id, True)
        again = normalize_super_chat_event({
            "id": "blue-2",
            "snippet": {
                "channelId": "UCb",
                "supporterDetails": {"displayName": "Ada", "channelId": "UCf"},
                "commentText": "more",
                "createdAt": "2024-06-01T12:05:00Z",
                "amountMicros": "2000000",
                "currency": "USD",
            },
        })
        self.assertEqual(store.add_many([again]), 1)
        self.assertEqual(host.claim(), [])

        store.set_stream_paused(None, False)
        started = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
        store.set_stream_values(stream_id, {
            "blue_script_id": saved["id"],
            "blue_started_at": "2026-10-09T12:00:00Z",
            "blue_timer_minute": "0",
        })
        host.tick(started)
        self.assertEqual(host.claim(), [])
        host.tick(started.replace(minute=20))
        timer = host.claim()
        self.assertEqual(timer[0]["text"], "tick")
        host.tick(started.replace(minute=20, second=30))
        self.assertEqual(host.claim(), [])
        host.record({"ok": False, "script": "Thanks", "line": 4, "target": "app", "reason": "The window was closed."})
        logged = " ".join(row["message"] for row in store.recent_log())
        self.assertIn("app notice was not shown. The window was closed.", logged)
        self.assertNotIn("tick", logged.split("app notice")[-1])

    def test_goal_fires_at_the_start_and_when_a_tier_is_reached(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        from streamdonations.blue_host import BlueHost
        from streamdonations.goals import GoalTier, dump_tiers
        from streamdonations.normalize import normalize_super_chat_event
        from streamdonations.store import Store

        def texts():
            return [item["text"] for item in host.claim()]

        def stored_tier(stream_id):
            for tab in store.ledger_snapshots():
                if tab["id"] == stream_id:
                    return tab["blue_goal_tier"]
            return None

        def gift(event_id, who, micros, when):
            return normalize_super_chat_event({
                "id": event_id,
                "snippet": {
                    "channelId": "UCb",
                    "supporterDetails": {"displayName": who, "channelId": "UCf"},
                    "commentText": "goal",
                    "createdAt": when,
                    "amountMicros": micros,
                    "currency": "USD",
                },
            })

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "donations.db")
        self.addCleanup(store.close)
        host = BlueHost(SimpleNamespace(store=store))
        store.on_fresh_donations = host.on_donations
        saved = store.save_blue_script(
            "Goals",
            'ON GOAL IF TIER EQUALS 0 THEN SEND NOTICE TO APP "tier 0 [{DONOR}] [{NAME}] [{PLATFORM}] [{AMOUNT}]"\n'
            'ON GOAL IF TIER EQUALS 1 THEN SEND NOTICE TO APP "tier 1 [{DONOR}] [{NAME}] [{PLATFORM}] [{AMOUNT}]"\n'
            'ON GOAL IF TIER EQUALS 2 THEN SEND NOTICE TO APP "tier 2 [{DONOR}] [{NAME}] [{PLATFORM}] [{AMOUNT}]"\n'
            'ON DONATION THEN SEND NOTICE TO APP "gift"\n',
        )
        store.set_setting("tiers_enabled", "1")
        store.set_setting("goal_tiers", dump_tiers([
            GoalTier(True, 25, "Silver", ""),
            GoalTier(True, 10, "Bronze", ""),
        ]))
        first = store._active_stream_id()
        store.set_stream_values(first, {"blue_script_id": saved["id"]})
        store.set_stream_paused(first, False)
        host.on_connection(first, False)
        self.assertEqual(texts(), ["tier 0 [] [] [] []"])
        self.assertEqual(stored_tier(first), "0")

        second = store.add_stream()
        tabs = {item["id"]: item for item in store.ledger_snapshots()}
        self.assertIn("Bronze", tabs[first]["goal_tiers"])
        self.assertEqual(tabs[second]["tiers_enabled"], "0")
        self.assertEqual(store.add_many([gift("jump", "@McLovin", "30000000", "2024-06-01T12:00:00Z")], first), 1)
        self.assertEqual(texts(), [
            "gift",
            "tier 1 [McLovin] [Bronze] [YouTube] [$30.00]",
            "tier 2 [McLovin] [Silver] [YouTube] [$30.00]",
        ])
        self.assertEqual(store.add_many([gift("small", "Ada", "1000000", "2024-06-01T12:05:00Z")], first), 1)
        self.assertEqual(texts(), ["gift"])

        store.set_stream_paused(first, True)
        host.on_connection(first, True)
        self.assertEqual(store.add_many([gift("paused", "Cam", "50000000", "2024-06-01T12:10:00Z")], first), 1)
        self.assertEqual(host.claim(), [])

        store.set_stream_paused(first, False)
        host.on_connection(first, False)
        self.assertEqual(texts(), ["tier 2 [McLovin] [Silver] [YouTube] [$30.00]"])

        store.set_stream_values(first, {"blue_goal_tier": ""})
        self.assertEqual(store.add_many([gift("upgrade", "Bea", "1000000", "2024-06-01T12:15:00Z")], first), 1)
        self.assertEqual(texts(), ["gift"])
        self.assertEqual(stored_tier(first), "2")

        store.set_setting("tiers_enabled", "1")
        store.set_setting("goal_tiers", dump_tiers([GoalTier(True, 10, "Tip", "")]))
        store.set_stream_values(second, {"blue_script_id": saved["id"]})
        store.set_stream_paused(second, False)
        host.on_connection(second, False)
        self.assertEqual(texts(), ["tier 0 [] [] [] []"])
        self.assertEqual(store.add_many([gift("cent", "Ada", "9990000", "2024-06-01T12:20:00Z")], second), 1)
        self.assertEqual(texts(), ["gift", "tier 1 [Ada] [Tip] [YouTube] [$9.99]"])
        self.assertEqual(stored_tier(first), "2")

        store.set_stream_values(first, {"blue_goal_tier": "nope"})
        self.assertEqual(stored_tier(first), "")
        logged = " ".join(row["message"] for row in store.recent_log())
        self.assertNotIn("McLovin", logged)
        self.assertNotIn("Bronze", logged)

    def test_total_and_tier_are_available_on_every_event(self):
        import tempfile
        from datetime import timedelta
        from pathlib import Path
        from types import SimpleNamespace

        from streamdonations.blue_host import BlueHost
        from streamdonations.goals import GoalTier, dump_tiers
        from streamdonations.normalize import normalize_super_chat_event
        from streamdonations.store import Store
        from streamdonations.util import parse_datetime

        def texts():
            return [item["text"] for item in host.claim()]

        def gift(event_id, who, micros, when):
            return normalize_super_chat_event({
                "id": event_id,
                "snippet": {
                    "channelId": "UCb",
                    "supporterDetails": {"displayName": who, "channelId": "UCf"},
                    "commentText": "goal",
                    "createdAt": when,
                    "amountMicros": micros,
                    "currency": "USD",
                },
            })

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "donations.db")
        self.addCleanup(store.close)
        host = BlueHost(SimpleNamespace(store=store))
        store.on_fresh_donations = host.on_donations
        saved = store.save_blue_script(
            "Totals",
            'ON GOAL IF TIER EQUALS 0 THEN SEND NOTICE TO APP "start {TIER} {TOTAL}"\n'
            'ON GOAL IF TIER EQUALS 1 THEN SEND NOTICE TO APP "goal {TIER} {TOTAL}"\n'
            'ON GOAL IF TIER EQUALS 2 THEN SEND NOTICE TO APP "goal {TIER} {TOTAL}"\n'
            'ON DONATION THEN SEND NOTICE TO APP "gift {TIER} {TOTAL} [{NAME}]"\n'
            'ON TIMER IF MINUTES EQUALS 1 THEN SEND NOTICE TO APP "minute {TIER} {TOTAL}"\n',
        )
        store.set_setting("tiers_enabled", "1")
        store.set_setting("goal_tiers", dump_tiers([
            GoalTier(True, 25, "Silver", ""),
            GoalTier(True, 10, "Bronze", ""),
        ]))
        first = store._active_stream_id()
        store.set_stream_values(first, {"blue_script_id": saved["id"]})
        store.set_stream_paused(first, False)
        host.on_connection(first, False)
        self.assertEqual(texts(), ["start 0 $0.00"])

        self.assertEqual(store.add_many([
            gift("ten", "Ada", "10000000", "2024-06-01T12:00:00Z"),
            gift("twenty", "Bea", "20000000", "2024-06-01T12:01:00Z"),
        ], first), 2)
        self.assertEqual(texts(), [
            "gift 1 $10.00 [{NAME}]",
            "goal 1 $10.00",
            "gift 2 $30.00 [{NAME}]",
            "goal 2 $30.00",
        ])
        started = parse_datetime(next(
            tab["blue_started_at"] for tab in store.ledger_snapshots() if tab["id"] == first
        ))
        host.tick(started + timedelta(minutes=1))
        self.assertEqual(texts(), ["minute 2 $30.00"])

        second = store.add_stream()
        store.set_stream_values(second, {"blue_script_id": saved["id"]})
        store.set_stream_paused(second, False)
        host.on_connection(second, False)
        self.assertEqual(texts(), ["start 0 $0.00"])
        self.assertEqual(store.add_many([
            gift("five", "Cam", "5000000", "2024-06-01T13:00:00Z"),
        ], second), 1)
        self.assertEqual(texts(), ["gift 0 $5.00 [{NAME}]"])
