"""In-memory giveaway drawings. No chat is sent and nothing is stored."""

import time
import unittest

from streamdonations.chat_feed import ChatFeed
from streamdonations.giveaway import Giveaway, keyword_announce, winner_announce


def _line(platform, author, text):
    return {"platform": platform, "author": author, "text": text}


class GiveawayTests(unittest.TestCase):
    def test_exact_keyword_is_case_insensitive_and_one_entry_per_platform(self):
        drawing = Giveaway(choose=lambda rows: rows[0])
        started = drawing.start("  #Win   ")
        self.assertEqual(started["keyword"], "#Win")
        self.assertEqual(started["announce"], "Type #Win in chat to enter the giveaway.")
        self.assertEqual(started["winner_announce"], "")
        self.assertTrue(started["running"])
        self.assertFalse(started["closed"])
        self.assertEqual(started["entries"], 0)

        drawing.observe([])
        drawing.observe([
            _line("youtube", "Fan", "  #WIN  "),
            _line("youtube", "fan", "#win"),
            _line("youtube", " FAN ", "#win"),
            _line("youtube", "Fan", "hello #win"),
            _line("youtube", "Fan", "#win!"),
            _line("rumble", "Fan", "#win"),
            _line("kick", "Other", "nope"),
            _line("kick", "   ", "#win"),
            _line("other", "Fan", "#win"),
            "not a line",
        ])
        status = drawing.status()
        self.assertEqual(status["entries"], 3)
        self.assertEqual(status["platforms"], {"youtube": 1, "rumble": 1, "kick": 1})
        self.assertIsNone(status["winner"])
        self.assertEqual(status["winner_announce"], "")

        closed = drawing.stop()
        self.assertFalse(closed["running"])
        self.assertTrue(closed["closed"])
        self.assertEqual(closed["winner"], {"platform": "youtube", "author": "Fan"})
        self.assertEqual(
            closed["winner_announce"],
            "The giveaway winner is Fan on YouTube.",
        )
        again = drawing.stop()
        self.assertEqual(again["winner"], closed["winner"])
        self.assertFalse(again["running"])

    def test_same_name_on_another_platform_keeps_its_own_spelling(self):
        drawing = Giveaway(choose=lambda rows: rows[-1])
        drawing.start("#go")
        drawing.observe([
            _line("youtube", "CoolName", "#go"),
            _line("kick", "COOLNAME", "#GO"),
        ])
        closed = drawing.stop()
        self.assertEqual(closed["winner"], {"platform": "kick", "author": "COOLNAME"})
        self.assertEqual(
            closed["winner_announce"],
            "The giveaway winner is COOLNAME on Kick.",
        )
        self.assertEqual(
            winner_announce({"platform": "rumble", "author": "CoolName"}),
            "The giveaway winner is CoolName on Rumble.",
        )

    def test_drawn_winners_leave_the_pool_and_stay_in_the_list(self):
        seen = []

        def choose(rows):
            seen.append([row["author"] for row in rows])
            return rows[0]

        drawing = Giveaway(choose=choose)
        with self.assertRaisesRegex(ValueError, "after the drawing is closed"):
            drawing.redraw()
        drawing.start("#win")
        with self.assertRaisesRegex(ValueError, "after the drawing is closed"):
            drawing.redraw()
        drawing.observe([
            _line("youtube", "Ada", "#win"),
            _line("rumble", "Bea", "#win"),
            _line("kick", "Cal", "#win"),
        ])
        closed = drawing.stop()
        self.assertEqual(closed["winner"], {"platform": "youtube", "author": "Ada"})
        self.assertEqual(closed["winners"], [{"platform": "youtube", "author": "Ada"}])
        self.assertEqual(closed["entries"], 2)
        self.assertEqual(closed["platforms"], {"youtube": 0, "rumble": 1, "kick": 1})
        self.assertEqual(seen, [["Ada", "Bea", "Cal"]])
        again = drawing.redraw()
        self.assertEqual(again["winner"], {"platform": "rumble", "author": "Bea"})
        self.assertEqual(again["winners"], [
            {"platform": "youtube", "author": "Ada"},
            {"platform": "rumble", "author": "Bea"},
        ])
        self.assertEqual(again["entries"], 1)
        self.assertEqual(again["platforms"], {"youtube": 0, "rumble": 0, "kick": 1})
        self.assertTrue(again["closed"])
        self.assertFalse(again["running"])
        self.assertEqual(
            again["winner_announce"],
            "The giveaway winner is Bea on Rumble.",
        )
        self.assertEqual(seen[1], ["Bea", "Cal"])
        back = drawing.redraw()
        self.assertEqual(back["winner"], {"platform": "kick", "author": "Cal"})
        self.assertEqual(back["winners"], [
            {"platform": "youtube", "author": "Ada"},
            {"platform": "rumble", "author": "Bea"},
            {"platform": "kick", "author": "Cal"},
        ])
        self.assertEqual(seen[2], ["Cal"])
        self.assertEqual(back["entries"], 0)
        drawing.observe([_line("youtube", "Late", "#win")])
        self.assertEqual(drawing.status()["entries"], 0)
        self.assertEqual(drawing.status()["winners"], back["winners"])
        with self.assertRaisesRegex(ValueError, "nobody else"):
            drawing.redraw()
        self.assertEqual(drawing.stop()["winners"], back["winners"])

        solo = Giveaway(choose=lambda rows: rows[0])
        solo.start("#win")
        solo.observe([_line("youtube", "Ada", "#win")])
        finished = solo.stop()
        self.assertEqual(finished["entries"], 0)
        self.assertEqual(finished["winners"], [{"platform": "youtube", "author": "Ada"}])
        with self.assertRaisesRegex(ValueError, "nobody else"):
            solo.redraw()

    def test_stop_before_start_does_not_close_and_a_second_start_is_refused(self):
        drawing = Giveaway()
        idle = drawing.stop()
        self.assertFalse(idle["running"])
        self.assertFalse(idle["closed"])
        self.assertIsNone(idle["winner"])
        self.assertEqual(idle["winner_announce"], "")
        self.assertEqual(idle["announce"], "")
        drawing.start("once")
        with self.assertRaisesRegex(ValueError, "already running"):
            drawing.start("twice")
        drawing.stop()

    def test_empty_drawing_has_no_winner_and_a_new_start_clears_it(self):
        drawing = Giveaway(choose=lambda rows: (_ for _ in ()).throw(AssertionError("no draw")))
        drawing.start("#empty")
        closed = drawing.stop()
        self.assertTrue(closed["closed"])
        self.assertEqual(closed["entries"], 0)
        self.assertIsNone(closed["winner"])
        self.assertEqual(closed["winner_announce"], "")
        self.assertEqual(closed["announce"], "Type #empty in chat to enter the giveaway.")
        restarted = drawing.start("#next")
        self.assertTrue(restarted["running"])
        self.assertFalse(restarted["closed"])
        self.assertEqual(restarted["entries"], 0)
        self.assertIsNone(restarted["winner"])
        drawing.stop()

    def test_lines_before_start_and_after_the_deadline_do_not_count(self):
        drawing = Giveaway(choose=lambda rows: rows[0])
        drawing.observe([_line("youtube", "Early", "#win")])
        drawing.start("#win")
        self.assertEqual(drawing.status()["entries"], 0)
        drawing.observe([_line("youtube", "Fan", "#win")])
        with drawing._lock:
            drawing._deadline = time.monotonic() - 1
        drawing.observe([_line("rumble", "Late", "#win")])
        status = drawing.status()
        self.assertFalse(status["running"])
        self.assertTrue(status["closed"])
        self.assertEqual(status["entries"], 0)
        self.assertEqual(status["platforms"]["youtube"], 0)
        self.assertEqual(status["winner"], {"platform": "youtube", "author": "Fan"})
        self.assertEqual(status["winners"], [{"platform": "youtube", "author": "Fan"}])
        self.assertIsNone(status["remaining"])

    def test_status_expires_a_past_deadline_without_counting(self):
        drawing = Giveaway()
        drawing.start("#win")
        with drawing._lock:
            drawing._deadline = time.monotonic() - 5
        status = drawing.status()
        self.assertFalse(status["running"])
        self.assertTrue(status["closed"])
        self.assertEqual(status["entries"], 0)
        self.assertIsNone(status["winner"])
        drawing.observe([_line("youtube", "Late", "#win")])
        self.assertEqual(drawing.status()["entries"], 0)

    def test_unique_entries_stop_at_the_limit(self):
        drawing = Giveaway(limit=2)
        drawing.start("hi")
        drawing.observe([
            _line("youtube", "A", "hi"),
            _line("youtube", "B", "hi"),
            _line("youtube", "C", "hi"),
            _line("kick", "A", "hi"),
        ])
        status = drawing.status()
        self.assertEqual(status["entries"], 2)
        self.assertEqual(status["platforms"], {"youtube": 2, "rumble": 0, "kick": 0})
        drawing.stop()

    def test_keyword_and_timer_limits(self):
        drawing = Giveaway()
        for keyword in ("", "   ", "a" * 81):
            with self.assertRaisesRegex(ValueError, "1 to 80"):
                drawing.start(keyword)
        drawing.start("a" * 80, "")
        self.assertEqual(drawing.status()["keyword"], "a" * 80)
        self.assertEqual(drawing.status()["seconds"], 0)
        self.assertIsNone(drawing.status()["remaining"])
        drawing.stop()

        fresh = Giveaway()
        for seconds in (True, False, 1.5, -1, 86401, "nope", "1.5"):
            with self.assertRaisesRegex(ValueError, "whole number of seconds"):
                fresh.start("ok", seconds)
        started = fresh.start("  go   now  ", "90")
        self.assertEqual(started["keyword"], "go now")
        self.assertEqual(started["seconds"], 90)
        self.assertIsInstance(started["remaining"], int)
        self.assertGreater(started["remaining"], 0)
        self.assertLessEqual(started["remaining"], 90)
        self.assertEqual(keyword_announce("   "), "")
        self.assertEqual(winner_announce(None), "")
        self.assertEqual(winner_announce({"platform": "youtube", "author": ""}), "")
        fresh.stop()
        self.assertIsNone(fresh._timer)

    def test_chat_listener_sees_each_new_line_once_and_errors_stay_inside_the_feed(self):
        feed = ChatFeed()
        seen = []

        def record(lines):
            seen.extend(lines)

        feed.on_lines = record
        feed.add("youtube", [{"key": "m1", "author": "Fan", "text": "#win"}])
        feed.add("youtube", [{"key": "m1", "author": "Fan", "text": "#win"}])
        self.assertEqual(seen, [{
            "platform": "youtube",
            "author": "Fan",
            "text": "#win",
            "time": seen[0]["time"],
        }])
        self.assertNotIn("key", seen[0])

        def boom(_lines):
            raise RuntimeError("listener failed")

        feed.on_lines = boom
        feed.add("kick", [{"key": "m2", "author": "Other", "text": "hello"}])
        payload = feed.since(0, 0, False)
        self.assertEqual([line["text"] for line in payload["lines"]], ["#win", "hello"])


if __name__ == "__main__":
    unittest.main()
