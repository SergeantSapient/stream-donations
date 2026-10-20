"""PLATFORM, RAW AMOUNT, CURRENCY, and IS (1.10.0) with TIME MODULO, MOD, %, and signs (1.9.2)."""

import sys
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from streamdonations.blue import BlueEvent, evaluate, inspect_script

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_blue_time_modulo import USER_LINE, _Host  # noqa: E402


def _gift(raw=1000.0, currency="JPY", amount=6.5, platform="youtube", clock=time(0, 1)):
    values = {"platform": platform, "donor": "Ada", "type": "Super Chat", "currency": currency}
    numbers = {"amount": amount, "rawamount": raw, "minutes": 3}
    return BlueEvent("donation", values, numbers, clock, {"donor": "Ada"})


def _timer(clock=time(12, 21), minutes=40, total=150.0):
    return BlueEvent("timer", {}, {"minutes": minutes, "total": total}, clock, {"total": f"${total:,.2f}"})


def _line(condition, event="DONATION", action='NOTICE TO APP "hit"'):
    return f"ON {event} IF {condition} THEN SEND {action}"


def _problems(source):
    return [problem["message"] for problem in inspect_script(source)]


def _hits(source, event):
    return [action.text for action in evaluate(source, event).actions]


class CrossFeatureTests(unittest.TestCase):
    def test_is_means_equals_with_modulo_and_signs(self):
        for condition, event in (
            ("MINUTES MOD 20 IS 0", _timer()),
            ("minutes % 20 is 0", _timer()),
            ("TIME MODULO 20 IS 1", _timer()),
            ("RAW AMOUNT MODULO 5 IS 0", _gift()),
            ("RAW_AMOUNT % 7 IS 6", _gift()),
            ("RAWAMOUNT MOD 3 EQUALS 1", _gift()),
            ("RAW AMOUNT ABOVE - 10", _gift(raw=-5.0)),
            ("RAW AMOUNT IS - 5", _gift(raw=-5.0)),
            ("CURRENCY IS % JPY", _gift()),
            ("AMOUNT ABOVE %5", _gift()),
        ):
            source = _line(condition, "TIMER" if event.name == "timer" else "DONATION")
            self.assertEqual(_problems(source), [], condition)
            self.assertEqual(_hits(source, event), ["hit"], condition)
        self.assertEqual(_hits(_line("MINUTES MOD 20 IS 0", "TIMER"), _timer(minutes=41)), [])
        self.assertEqual(_hits(_line("RAW AMOUNT MODULO 5 IS 0"), _gift(raw=1001.0)), [])
        self.assertEqual(_hits(_line("RAW AMOUNT IS - 5"), _gift(raw=5.0)), [])

    def test_reported_values_with_the_new_fields(self):
        modulo = "MODULO works on AMOUNT, RAW AMOUNT, TOTAL, MINUTES, TIER, and TIME."
        cases = {
            "CURRENCY MODULO 2 EQUALS 0": modulo,
            "CURRENCY MOD 2 IS 1": modulo,
            "CURRENCY % 2 IS 1": modulo,
            "RAW AMOUNT IS lots": "RAW AMOUNT needs a number.",
            "RAW AMOUNT ABOVE lots": "RAW AMOUNT needs a number.",
            "AMOUNT IS GREAT": "AMOUNT needs a number.",
            "TIME IS 5": "TIME needs a clock time such as 14:30 or 9:00 PM. TIME MODULO takes minutes since midnight.",
            "RAW AMOUNT MODULO -5 EQUALS 0": "The modulus must be above zero.",
            "RAW AMOUNT MODULO 5 IS 5": "That can never match. RAW AMOUNT MODULO 5 is from 0 up to 5.",
            "RAW AMOUNT MODULO 5 IS - 1": "That can never match. RAW AMOUNT MODULO 5 is from 0 up to 5.",
            "TIME MODULO 20 IS -1": "That can never match. TIME MODULO 20 is 0 through 19.",
        }
        for condition, message in cases.items():
            self.assertEqual(_problems(_line(condition)), [message], condition)
        # Text fields still compare as text, with IS.
        self.assertEqual(_hits(_line("CURRENCY IS JPY"), _gift()), ["hit"])
        self.assertEqual(_hits(_line("NOT CURRENCY IS USD"), _gift()), ["hit"])
        self.assertEqual(_hits(_line("TIME IS 0:01"), _gift()), ["hit"])

    def test_platform_on_a_timer_is_still_a_syntax_error(self):
        source = _line("TIME MODULO 20 EQUALS 1", "TIMER", 'CHAT TO PLATFORM "x"')
        self.assertEqual(_problems(source), ["A timer has no PLATFORM to send to."])
        self.assertEqual(evaluate(source, _timer(time(0, 1))).actions, [])

    def test_platform_with_time_modulo_on_a_donation(self):
        source = _line("TIME % 20 IS 1 AND RAW AMOUNT MOD 5 IS 0", "DONATION", 'CHAT TO PLATFORM "hi {DONOR}"')
        run = evaluate(source, _gift(platform="kick", clock=time(9, 21)))
        self.assertEqual([(a.target, a.via_platform, a.text) for a in run.actions], [("kick", True, "hi Ada")])
        self.assertEqual(evaluate(source, _gift(platform="kick", clock=time(9, 22))).actions, [])

    def test_the_users_line(self):
        self.assertEqual(_problems(USER_LINE), [])
        run = evaluate(USER_LINE, _timer(time(12, 21)))
        self.assertEqual([a.target for a in run.actions], ["youtube", "rumble", "kick"])
        self.assertEqual({a.text for a in run.actions}, {"$150.00 total for the stream!"})
        self.assertEqual(evaluate(USER_LINE, _timer(time(12, 22))).actions, [])
        self.assertEqual(evaluate(USER_LINE, _timer(time(12, 21), total=50.0)).actions, [])

    def test_totalline_on_a_clock_timer(self):
        from streamdonations.normalize import Donation
        from streamdonations.util import utc_now_iso

        source = 'ON TIMER IF TIME MODULO 1 EQUALS 0 THEN SEND CHAT TO YOUTUBE "{TOTALLINE}"\n'
        started = datetime(2026, 10, 20, 15, 0, 30, tzinfo=timezone.utc)
        box = _Host(self, source, started)
        box.store.add_many([Donation(
            dedupe_key="rumble:chat:1", platform="rumble", kind="rant", external_id="1", channel="",
            author_name="Ada", author_id="", message="", amount_value=12.0, amount_micros=12_000_000,
            currency="USD", created_at=utc_now_iso(), raw={},
        )])
        box.host.claim()
        box.host.tick(started + timedelta(seconds=31))
        pending = box.host.claim()
        self.assertEqual([item["target"] for item in pending], ["youtube"])
        self.assertIn("$12.00", pending[0]["text"])
        self.assertLessEqual(len(pending[0]["text"]), 200)


if __name__ == "__main__":
    unittest.main()
