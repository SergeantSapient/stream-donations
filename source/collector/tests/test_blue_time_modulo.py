"""TIME MODULO, MOD and %, reported values, and TIME lines once per clock minute."""

import random
import tempfile
import unittest
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from streamdonations.blue import BlueEvent, evaluate, inspect_script

USER_LINE = 'On timer if time modulo 20 equals 1 and total is above 99.99, then send chat to all "{total} total for the stream!"'


def _timer(clock, minutes=5, total=None):
    numbers = {"minutes": minutes}
    placeholders = {"time": clock.strftime("%H:%M"), "minutes": str(minutes)}
    if total is not None:
        numbers["total"] = total
        placeholders["total"] = f"${total:,.2f}"
    return BlueEvent("timer", {}, numbers, clock, placeholders)


def _donation(clock, amount=20.0):
    return BlueEvent(
        "donation",
        {"platform": "youtube", "donor": "Ada", "type": "Super Chat", "amount": f"${amount:.2f}"},
        {"amount": amount, "minutes": 3},
        clock,
        {"donor": "Ada", "amount": f"${amount:.2f}", "time": clock.strftime("%H:%M")},
    )


def _texts(source, event, timer=""):
    return [action.text for action in evaluate(source, event, timer).actions]


def _problems(source):
    return [problem["message"] for problem in inspect_script(source)]


def _line(condition, event="TIMER"):
    return f'ON {event} IF {condition} THEN SEND NOTICE TO APP "hit"'


class TimeModuloTests(unittest.TestCase):
    def test_the_users_line(self):
        self.assertEqual(_problems(USER_LINE), [])
        for clock in (time(0, 1), time(0, 21), time(12, 41), time(23, 41)):
            self.assertEqual(set(_texts(USER_LINE, _timer(clock, total=150.0))), {"$150.00 total for the stream!"}, clock)
        run = evaluate(USER_LINE, _timer(time(12, 21), total=150.0))
        self.assertEqual({action.kind for action in run.actions}, {"chat"})
        self.assertEqual({action.target for action in run.actions}, {"youtube", "rumble", "kick"})
        for clock in (time(0, 0), time(0, 2), time(12, 20), time(13, 1, 59).replace(minute=2)):
            self.assertEqual(_texts(USER_LINE, _timer(clock, total=150.0)), [], clock)
        self.assertEqual(_texts(USER_LINE, _timer(time(12, 21), total=99.99)), [])
        self.assertEqual(_texts(USER_LINE, _timer(time(12, 21))), [])

    def test_time_modulo_counts_minutes_since_midnight(self):
        source = _line("TIME MODULO 60 EQUALS 30")
        self.assertEqual(_texts(source, _timer(time(9, 30))), ["hit"])
        self.assertEqual(_texts(source, _timer(time(0, 30))), ["hit"])
        self.assertEqual(_texts(source, _timer(time(9, 31))), [])
        self.assertEqual(_texts(_line("TIME MODULO 90 EQUALS 0"), _timer(time(1, 30))), ["hit"])
        self.assertEqual(_texts(_line("TIME MODULO 90 EQUALS 0"), _timer(time(1, 0))), [])
        self.assertEqual(_texts(_line("TIME MODULO 1440 EQUALS 870"), _timer(time(14, 30))), ["hit"])
        self.assertEqual(_texts(_line("TIME MODULO 20 ABOVE 17"), _timer(time(10, 18))), ["hit"])
        self.assertEqual(_texts(_line("TIME MODULO 20 BELOW 2"), _timer(time(10, 41))), ["hit"])
        self.assertEqual(_texts(_line("TIME MODULO 20 BELOW 2"), _timer(time(10, 42))), [])
        self.assertEqual(_texts(_line("NOT TIME MODULO 2 EQUALS 0"), _timer(time(10, 43))), ["hit"])
        # Seconds on the clock do not change the minute.
        self.assertEqual(_texts(_line("TIME MODULO 20 EQUALS 1"), _timer(time(0, 1, 59))), ["hit"])

    def test_time_without_modulo_is_still_a_clock(self):
        self.assertEqual(_texts(_line("TIME ABOVE 14:00"), _timer(time(14, 30))), ["hit"])
        self.assertEqual(_texts(_line("TIME EQUALS 2:30 PM"), _timer(time(14, 30))), ["hit"])
        self.assertEqual(_texts(_line("TIME BELOW 14:00"), _timer(time(14, 30))), [])

    def test_time_modulo_on_donation_and_goal_uses_the_event_time(self):
        source = _line("TIME MODULO 20 EQUALS 1 AND AMOUNT ABOVE 5", "DONATION")
        self.assertEqual(_texts(source, _donation(time(20, 21))), ["hit"])
        self.assertEqual(_texts(source, _donation(time(20, 22))), [])
        goal = BlueEvent("goal", {}, {"tier": 1}, time(8, 41), {})
        self.assertEqual(_texts(_line("TIME MODULO 20 EQUALS 1", "GOAL"), goal), ["hit"])

    def test_mod_and_percent_are_modulo(self):
        for spelled in ("TIME MOD 20 EQUALS 1", "time % 20 equals 1", "TIME%20 EQUALS 1", "time mod 20 equals 1"):
            source = _line(spelled)
            self.assertEqual(_problems(source), [], spelled)
            self.assertEqual(_texts(source, _timer(time(3, 21))), ["hit"], spelled)
            self.assertEqual(_texts(source, _timer(time(3, 22))), [], spelled)
        self.assertEqual(_texts(_line("MINUTES % 20 EQUALS 0"), _timer(time(3, 0), minutes=40)), ["hit"])
        self.assertEqual(_texts(_line("MINUTES MOD 20 EQUALS 0"), _timer(time(3, 0), minutes=41)), [])
        self.assertEqual(_texts(_line("AMOUNT % 5 EQUALS 0", "DONATION"), _donation(time(3, 0), 25.0)), ["hit"])

    def test_percent_and_minus_before_a_value(self):
        donation = _donation(time(3, 0), 6.0)
        for condition in ("AMOUNT ABOVE %5", "AMOUNT ABOVE % 5", "AMOUNT % ABOVE 5", "DONOR EQUALS % Ada"):
            source = _line(condition, "DONATION")
            self.assertEqual(_problems(source), [], condition)
            self.assertEqual(_texts(source, donation), ["hit"], condition)
        self.assertEqual(_texts(_line("TIME EQUALS %3:00", "DONATION"), donation), ["hit"])
        self.assertEqual(_texts(_line("TIME EQUALS 3:00%", "DONATION"), donation), ["hit"])
        never = "That can never match. TIME MODULO 20 is 0 through 19."
        for condition in ("TIME MODULO 20 EQUALS -1", "TIME MODULO 20 EQUALS - 1", "TIME MODULO 20 BELOW -5"):
            self.assertEqual(_problems(_line(condition)), [never], condition)
            self.assertEqual(_texts(_line(condition), _timer(time(0, 1))), [], condition)
        for condition in ("TIME MODULO - 5 EQUALS 1", "TIME % - 5 EQUALS 1", "TIME MOD -5 EQUALS 1"):
            self.assertEqual(_problems(_line(condition)), ["The modulus must be above zero."], condition)
        self.assertEqual(_texts(_line("AMOUNT ABOVE -1", "DONATION"), _donation(time(3, 0), 0.5)), ["hit"])
        self.assertEqual(_texts(_line("AMOUNT ABOVE - 10", "DONATION"), _donation(time(3, 0), 0.5)), ["hit"])
        self.assertEqual(_texts(_line("TIME MODULO 20 ABOVE -1"), _timer(time(0, 0))), ["hit"])

    def test_percent_elsewhere_is_harmless(self):
        source = 'ON DONATION IF AMOUNT ABOVE 5% THEN SEND NOTICE TO APP "100% thanks, {DONOR}" %'
        self.assertEqual(_problems(source), [])
        self.assertEqual(_texts(source, _donation(time(3, 0), 6.0)), ["100% thanks, Ada"])
        self.assertEqual(_texts(source, _donation(time(3, 0), 4.0)), [])
        self.assertEqual(_problems('% ON DONATION THEN SEND NOTICE TO APP "x"'), [])
        self.assertEqual(_texts('ON DONATION % IF % AMOUNT ABOVE 5 THEN % SEND NOTICE TO APP "x"', _donation(time(3, 0), 6.0)), ["x"])

    def test_values_that_cannot_work_are_reported(self):
        clock = "TIME needs a clock time such as 14:30 or 9:00 PM. TIME MODULO takes minutes since midnight."
        cases = {
            "TIME EQUALS 5": clock,
            "TIME ABOVE soon": clock,
            "TIME MODULO 20 EQUALS 14:30": "TIME MODULO counts minutes since midnight, so it needs a number, not a clock time.",
            "AMOUNT ABOVE lots": "AMOUNT needs a number.",
            "TOTAL ABOVE 9:00": "TOTAL needs a number.",
            "MINUTES EQUALS ten": "MINUTES needs a number.",
            "TIER EQUALS gold": "TIER needs a number.",
            "MINUTES MODULO 0 EQUALS 0": "The modulus is zero.",
            "MINUTES MODULO -5 EQUALS 1": "The modulus must be above zero.",
            "TIME % -20 EQUALS 1": "The modulus must be above zero.",
            "MINUTES MODULO lots EQUALS 1": "The modulus needs a number.",
            "TIME MODULO 20 EQUALS 20": "That can never match. TIME MODULO 20 is 0 through 19.",
            "TIME MODULO 20 EQUALS 25": "That can never match. TIME MODULO 20 is 0 through 19.",
            "TIME MODULO 20 EQUALS 1.5": "That can never match. TIME MODULO 20 is 0 through 19.",
            "MINUTES MODULO 20 ABOVE 19": "That can never match. MINUTES MODULO 20 is 0 through 19.",
            "MINUTES MODULO 20 BELOW 0": "That can never match. MINUTES MODULO 20 is 0 through 19.",
            "DONOR MODULO 2 EQUALS 1": "MODULO works on AMOUNT, RAW AMOUNT, TOTAL, MINUTES, TIER, and TIME.",
        }
        for condition, message in cases.items():
            self.assertEqual(_problems(_line(condition)), [message], condition)
            self.assertEqual(_texts(_line(condition), _timer(time(0, 0), minutes=20, total=20.0)), [], condition)
        amount = _problems(_line("AMOUNT MODULO 5 ABOVE 5", "DONATION"))
        self.assertEqual(len(amount), 1)
        self.assertTrue(amount[0].startswith("That can never match. AMOUNT MODULO 5"), amount)
        # Values that can match stay quiet.
        for condition in ("MINUTES MODULO 20 ABOVE 18", "TIME MODULO 20 EQUALS 19", "TIME MODULO 20 EQUALS 0", "MINUTES MODULO 20 BELOW 1"):
            self.assertEqual(_problems(_line(condition)), [], condition)
        self.assertEqual(_problems(_line("AMOUNT MODULO 2.5 ABOVE 2.4", "DONATION")), [])
        self.assertEqual(_problems(_line("AMOUNT MODULO 5 EQUALS 2.5", "DONATION")), [])

    def test_a_reported_line_does_not_stop_the_others(self):
        source = _line("TIME EQUALS 5") + "\n" + _line("TIME MODULO 20 EQUALS 1")
        run = evaluate(source, _timer(time(0, 1)))
        self.assertEqual([action.line for action in run.actions], [2])
        self.assertEqual([problem.line for problem in run.problems], [1])

    def test_timer_kind_picks_time_lines(self):
        source = _line("TIME MODULO 20 EQUALS 1") + "\n" + _line("MINUTES MODULO 1 EQUALS 0")
        event = _timer(time(0, 1), minutes=7)
        self.assertEqual([a.line for a in evaluate(source, event).actions], [1, 2])
        self.assertEqual([a.line for a in evaluate(source, event, "clock").actions], [1])
        self.assertEqual([a.line for a in evaluate(source, event, "elapsed").actions], [2])
        donation = _line("TIME MODULO 20 EQUALS 1", "DONATION")
        self.assertEqual(_texts(donation, _donation(time(0, 1)), "elapsed"), ["hit"])


class _Host:
    def __init__(self, case, source, started):
        from streamdonations.blue_host import BlueHost
        from streamdonations.store import Store

        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "donations.db")
        case.addCleanup(self.store.close)
        self.host = BlueHost(SimpleNamespace(store=self.store))
        self.store.on_fresh_donations = self.host.on_donations
        saved = self.store.save_blue_script("Clock", source)
        self.stream_id = self.store._active_stream_id()
        self.store.set_stream_paused(None, False)
        self.store.set_stream_values(self.stream_id, {
            "blue_script_id": saved["id"],
            "blue_started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "blue_timer_minute": "0",
            "blue_clock_minute": str(int(started.timestamp() // 60)),
        })

    def restart(self):
        """A new SuperBerry process on the same ledger file."""
        from streamdonations.blue_host import BlueHost

        self.host = BlueHost(SimpleNamespace(store=self.store))
        self.store.on_fresh_donations = self.host.on_donations

    def stored_minute(self):
        tab = next(tab for tab in self.store.ledger_snapshots() if tab["id"] == self.stream_id)
        return tab["blue_clock_minute"]

    def run(self, start, seconds, seed=7):
        rnd = random.Random(seed)
        moment = start
        fired = []
        # The per-chat cap counts real seconds. Give it the simulated clock.
        self.host._clock = lambda: moment.timestamp()
        while moment < start + timedelta(seconds=seconds):
            self.host.tick(moment)
            fired.extend((moment, item["text"]) for item in self.host.claim())
            self.last = moment
            moment += timedelta(seconds=1.0 + rnd.uniform(0, 0.4))
        return fired


def _local_minute(moment):
    """Local minutes counted from 1970, so a window that crosses midnight still counts up."""
    local = moment.astimezone()
    return int((moment.timestamp() + local.utcoffset().total_seconds()) // 60)


class ClockMinuteTimerTests(unittest.TestCase):
    def test_time_lines_see_every_clock_minute_once(self):
        source = (
            'ON TIMER IF TIME ABOVE 0:00 OR TIME EQUALS 0:00 THEN SEND NOTICE TO APP "clock {TIME}"\n'
            'ON TIMER IF MINUTES MODULO 1 EQUALS 0 THEN SEND NOTICE TO APP "elapsed {MINUTES}"\n'
        )
        base = datetime(2026, 10, 20, 15, 0, 0, tzinfo=timezone.utc)
        # Start right before a clock minute, in the middle, and right after it.
        for offset in (0.2, 30.0, 59.7):
            started = base + timedelta(seconds=int(offset))
            start = base + timedelta(seconds=offset)
            box = _Host(self, source, started)
            fired = box.run(start, 2 * 3600)
            clock = [(moment, text) for moment, text in fired if text.startswith("clock")]
            minutes = [_local_minute(moment) for moment, _ in clock]
            counts = Counter(minutes)
            self.assertEqual([m for m, n in counts.items() if n > 1], [], offset)
            # Every clock minute after the start's minute, up to the last tick, ran once.
            expected = list(range(_local_minute(start) + 1, _local_minute(box.last) + 1))
            self.assertGreaterEqual(len(expected), 119, offset)
            self.assertEqual(minutes, expected, offset)
            for moment, text in clock:
                local = moment.astimezone()
                self.assertEqual(text, f"clock {local.hour:02d}:{local.minute:02d}")
                self.assertLess(local.second, 3, moment)
            elapsed = [text for _, text in fired if text.startswith("elapsed")]
            self.assertEqual(elapsed[:3], ["elapsed 1", "elapsed 2", "elapsed 3"], offset)
            self.assertEqual(len(elapsed), len(set(elapsed)), offset)

    def test_the_users_line_lands_on_the_clock(self):
        from streamdonations.normalize import Donation
        from streamdonations.util import utc_now_iso

        started = datetime(2026, 10, 20, 15, 0, 59, tzinfo=timezone.utc)
        box = _Host(self, USER_LINE, started)
        box.store.add_many([Donation(
            dedupe_key="rumble:chat:1", platform="rumble", kind="rant", external_id="1", channel="",
            author_name="Ada", author_id="", message="", amount_value=150.0, amount_micros=150_000_000,
            currency="USD", created_at=utc_now_iso(), raw={},
        )])
        box.host.claim()
        fired = box.run(started + timedelta(seconds=0.7), 3 * 3600)
        # CHAT TO ALL is one chat per platform, so each landing minute has three.
        minutes = Counter(_local_minute(moment) for moment, _ in fired)
        self.assertEqual(len(minutes), 9)
        self.assertEqual(set(minutes.values()), {3})
        self.assertTrue(all(minute % 1440 % 20 == 1 for minute in minutes), minutes)
        self.assertEqual({text for _, text in fired}, {"$150.00 total for the stream!"})

    def test_a_restart_does_not_repeat_a_clock_minute(self):
        source = 'ON TIMER IF TIME MODULO 1 EQUALS 0 THEN SEND NOTICE TO APP "clock"\n'
        started = datetime(2026, 10, 20, 15, 0, 50, tzinfo=timezone.utc)
        box = _Host(self, source, started)
        box.host.tick(started + timedelta(seconds=11))
        self.assertEqual([item["text"] for item in box.host.claim()], ["clock"])
        self.assertEqual(box.stored_minute(), str(int((started + timedelta(seconds=11)).timestamp() // 60)))
        # A new process in the same minute, with the same start, stays quiet.
        box.restart()
        box.host.tick(started + timedelta(seconds=30))
        self.assertEqual(box.host.claim(), [])
        box.host.tick(started + timedelta(seconds=71))
        self.assertEqual([item["text"] for item in box.host.claim()], ["clock"])

    def test_a_late_first_tick_still_runs_its_minute(self):
        source = 'ON TIMER IF TIME MODULO 1 EQUALS 0 THEN SEND NOTICE TO APP "clock"\n'
        started = datetime(2026, 10, 20, 15, 0, 0, tzinfo=timezone.utc)
        box = _Host(self, source, started)
        # Over 90 seconds after the start, this minute has not been sent yet.
        box.host.tick(started + timedelta(minutes=3, seconds=20))
        self.assertEqual([item["text"] for item in box.host.claim()], ["clock"])
        box.host.tick(started + timedelta(minutes=3, seconds=40))
        self.assertEqual(box.host.claim(), [])

    def test_the_start_minute_does_not_run_and_a_stop_clears_the_minute(self):
        source = 'ON TIMER IF TIME MODULO 1 EQUALS 0 THEN SEND NOTICE TO APP "clock"\n'
        started = datetime(2026, 10, 20, 15, 0, 10, tzinfo=timezone.utc)
        box = _Host(self, source, started)
        box.host.tick(started + timedelta(seconds=40))
        self.assertEqual(box.host.claim(), [])
        box.host._stop(box.stream_id)
        self.assertEqual(box.stored_minute(), "")

    def test_a_connect_stores_its_start_minute(self):
        from streamdonations.util import parse_datetime

        source = 'ON TIMER IF TIME MODULO 1 EQUALS 0 THEN SEND NOTICE TO APP "clock"\n'
        box = _Host(self, source, datetime(2026, 10, 20, 15, 0, 0, tzinfo=timezone.utc))
        box.host._start_body(box.stream_id)
        tab = next(tab for tab in box.store.ledger_snapshots() if tab["id"] == box.stream_id)
        started = parse_datetime(tab["blue_started_at"])
        self.assertEqual(tab["blue_clock_minute"], str(int(started.timestamp() // 60)))

    def test_a_script_running_before_the_upgrade_starts_with_the_next_minute(self):
        source = 'ON TIMER IF TIME MODULO 1 EQUALS 0 THEN SEND NOTICE TO APP "clock"\n'
        started = datetime(2026, 10, 20, 15, 0, 0, tzinfo=timezone.utc)
        box = _Host(self, source, started)
        box.store.set_stream_values(box.stream_id, {"blue_clock_minute": ""})
        later = started + timedelta(hours=1, seconds=20)
        box.host.tick(later)
        self.assertEqual(box.host.claim(), [])
        box.host.tick(later + timedelta(seconds=41))
        self.assertEqual([item["text"] for item in box.host.claim()], ["clock"])

    def test_a_reported_line_reaches_the_log(self):
        source = _line("TIME EQUALS 5") + "\n"
        started = datetime(2026, 10, 20, 15, 0, 0, tzinfo=timezone.utc)
        box = _Host(self, source, started)
        box.run(started, 130)
        logged = " ".join(row["message"] for row in box.store.recent_log())
        self.assertIn("BLUE Clock line 1: TIME needs a clock time", logged)


if __name__ == "__main__":
    unittest.main()
