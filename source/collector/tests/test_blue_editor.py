"""BLUE 1.11: the editor's grammar, live problem marks, and the dry-run test box."""

import copy
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "js"))

import make_blue_fixture  # noqa: E402
from streamdonations import blue  # noqa: E402
from streamdonations.blue import BlueEvent, explain, grammar  # noqa: E402
from streamdonations.blue_dryrun import dry_run  # noqa: E402
from streamdonations.blue_host import CHAT_LINES_PER_MINUTE, BlueHost  # noqa: E402
from streamdonations.normalize import Donation  # noqa: E402
from streamdonations.server import App, Handler, ThreadingServer  # noqa: E402
from streamdonations.store import Store  # noqa: E402
from streamdonations.util import utc_now_iso  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "streamdonations" / "web"


def _gift(key, platform="youtube", donor="Ada", amount=10.0, currency="USD"):
    return Donation(
        dedupe_key=key, platform=platform,
        kind={"youtube": "super_chat", "kick": "kicks", "rumble": "rant"}[platform],
        external_id=key, channel="", author_name=donor, author_id="", message="",
        amount_value=amount, amount_micros=int(amount * 1_000_000),
        currency=currency, created_at=utc_now_iso(), raw={},
    )


class GrammarTests(unittest.TestCase):
    def test_grammar_is_blue_py(self):
        words = grammar()
        self.assertEqual(words["keywords"], sorted(blue._KEYWORDS))
        self.assertEqual(set(words["fields"]), set(blue._FIELDS) | {"raw"})
        self.assertEqual(words["modulo"], ["modulo", "modulus", "mod", "%"])
        self.assertEqual(words["chat_targets"], ["youtube", "rumble", "kick", "all", "platform"])
        self.assertEqual(words["notice_targets"], sorted(blue._NOTICE_TARGETS))
        self.assertEqual(words["placeholders"], list(blue.PLACEHOLDERS))
        self.assertEqual(words["placeholder_aliases"], blue._PLACEHOLDER_ALIASES)
        self.assertEqual(words["time_pattern"], blue._TIME.pattern)
        self.assertEqual(words["number_pattern"], blue._NUMBER.pattern)
        self.assertEqual(words["placeholder_pattern"], blue._PLACEHOLDER.pattern)
        # Every keyword has a color.
        known = set()
        for name in ("events", "fields", "operators", "modulo", "logic", "actions", "chat_targets", "notice_targets"):
            known |= set(words[name])
        self.assertEqual(set(words["keywords"]) - known, set())
        self.assertTrue(set(blue._STRUCTURAL) <= known)

    def test_placeholders_are_what_the_host_fills_in(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "donations.db")
        self.addCleanup(store.close)
        host = BlueHost(SimpleNamespace(store=store))
        tab = store.ledger_snapshots()[0]
        now = datetime.now(timezone.utc)
        gift = _gift("youtube:public:p")
        filled = set()
        for event in (
            host._donation_event(gift, tab, now, 0, 0.0),
            host._timer_event(3, now, 0, 0.0, tab),
            host._goal_event(tab, 1, now, donor="Ada", platform="kick", amount=5.0, total=5.0),
        ):
            filled |= set(event.placeholders) | set(event.lookup or {})
        self.assertEqual(filled, set(blue.PLACEHOLDERS))

    def test_node_fixture_is_current(self):
        saved = (Path(make_blue_fixture.HERE) / "blue_fixture.json").read_text(encoding="utf-8")
        self.assertEqual(
            json.loads(saved), json.loads(make_blue_fixture.text()),
            "Run python3 source/collector/tests/js/make_blue_fixture.py",
        )


def _event(name="donation", platform="youtube", amount=20.0, clock=time(14, 30), minutes=0, tier=0):
    values = {"platform": platform, "donor": "Ada", "type": "Super Chat", "currency": "USD"}
    numbers = {"minutes": minutes, "tier": tier, "amount": amount}
    return BlueEvent(name, values, numbers, clock, {"donor": "Ada", "time": clock.strftime("%H:%M")}, None)


class ExplainTests(unittest.TestCase):
    def test_each_condition_with_what_it_saw(self):
        report = explain(
            'ON DONATION IF AMOUNT IS ABOVE 10 AND PLATFORM IS KICK OR DONOR EQUALS Ada THEN SEND CHAT TO ALL AND NOT RUMBLE "hi {DONOR}"',
            _event(),
        )
        self.assertEqual(report["problems"], [])
        line = report["lines"][0]
        self.assertTrue(line["applies"])
        self.assertTrue(line["matched"])
        self.assertEqual(line["groups"], [
            [
                {"text": "AMOUNT ABOVE 10", "saw": "20", "result": True},
                {"text": "PLATFORM EQUALS KICK", "saw": "youtube", "result": False},
            ],
            [{"text": "DONOR EQUALS Ada", "saw": "Ada", "result": True}],
        ])
        self.assertEqual(
            [(action["target"], action["text"]) for action in line["actions"]],
            [("youtube", "hi Ada"), ("kick", "hi Ada")],
        )

    def test_modulo_and_timer_kind(self):
        report = explain(
            "ON TIMER IF TIME MODULO 15 EQUALS 0 THEN SEND NOTICE TO APP \"x\"\n"
            "ON TIMER IF MINUTES % 7 EQUALS 1 THEN SEND NOTICE TO APP \"y\"\n"
            "ON DONATION THEN SEND NOTICE TO APP \"z\"",
            _event("timer", minutes=15),
        )
        first, second, third = report["lines"]
        self.assertEqual(first["timer"], "clock")
        self.assertEqual(first["groups"][0][0]["saw"], "14:30 is minute 870 of the day; 870 MODULO 15 is 0")
        self.assertTrue(first["matched"])
        self.assertEqual(second["timer"], "elapsed")
        self.assertEqual(second["groups"][0][0]["saw"], "15 MODULO 7 is 1")
        self.assertFalse(third["applies"])
        self.assertEqual(third["actions"], [])

    def test_percent_lines_the_editor_paints_as_modulo(self):
        # The same lines tests/js/blue_highlight.test.js expects to be MODULO.
        for condition in ("AMOUNT IS % 5 EQUALS 0", "AMOUNT NOT % 5 EQUALS 0", "TIME IS % 15 EQUALS 0",
                          "AMOUNT the remainder % 10 EQUALS 0", "NOT AMOUNT % 4 EQUALS 0", "RAW AMOUNT % 100 EQUALS 0"):
            source = f'ON DONATION IF {condition} THEN SEND NOTICE TO APP "x"'
            report = explain(source, _event())
            self.assertEqual(report["problems"], [], condition)
            self.assertIn("MODULO", report["lines"][0]["groups"][0][0]["text"], condition)
        self.assertEqual(
            blue.inspect_script('ON DONATION IF AMOUNT % -5 EQUALS 0 THEN SEND NOTICE TO APP "x"')[0]["line"], 1)
        report = explain('ON DONATION IF AMOUNT IS ABOVE % 5 THEN SEND NOTICE TO APP "x"', _event())
        self.assertEqual(report["lines"][0]["groups"][0][0]["text"], "AMOUNT ABOVE 5")

    def test_problems_and_platform(self):
        report = explain('ON DONATION IF AMOUNT IS ABOVE lots THEN SEND NOTICE TO APP "x"\nON DONATION THEN SEND CHAT TO PLATFORM "t"',
                         _event(platform="other"))
        self.assertEqual(report["problems"], [{"line": 1, "message": "AMOUNT needs a number."}])
        self.assertEqual(report["lines"][0]["actions"], [{"kind": "chat", "target": "", "via_platform": True, "text": "t"}])


class DryRunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "donations.db")
        self.addCleanup(self.store.close)
        self.allowed = {"youtube", "kick"}
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
        self.source = (
            'ON DONATION IF AMOUNT IS ABOVE 5 THEN SEND CHAT TO PLATFORM "Thanks {MENTION}!"\n'
            'ON TIMER IF TIME MODULO 15 EQUALS 0 THEN SEND CHAT TO YOUTUBE "It is {TIME}"\n'
            'ON GOAL IF TIER IS ABOVE 0 THEN SEND NOTICE TO SYSTEM "Tier {TIER}"'
        )
        saved = self.store.save_blue_script("Thanks", self.source)
        self.store.set_stream_values(self.stream, {
            "blue_script_id": saved["id"],
            "blue_started_at": (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "blue_timer_minute": "0",
        })
        # A real thank-you, so the guards have something to look at.
        self.store.add_many([_gift("youtube:public:1", amount=10.0)])
        self.assertEqual([item["target"] for item in self.host.claim()], ["youtube"])

    def snapshot(self):
        dump = _dump(self.store)
        tab = self.host._tab(self.stream)
        return {
            "db": dump,
            "pending": copy.deepcopy(self.host._pending),
            "guards": copy.deepcopy(self.host._guards),
            "seen": set(self.host._seen),
            "missing": set(self.host._missing),
            "quiet": set(self.host._quiet),
            "chat_guard": tab.get("blue_chat_guard"),
            "clock_minute": tab.get("blue_clock_minute"),
            "timer_minute": tab.get("blue_timer_minute"),
            "log": [row["message"] for row in self.store.recent_log()],
        }

    def test_dry_runs_change_nothing(self):
        before = self.snapshot()
        self.assertIsNotNone(before["db"])
        runs = [
            {"source": self.source.split("\n")[0], "amount": "10", "platform": "youtube", "donor": "Ada"},
            {"source": self.source.split("\n")[0], "amount": "10", "platform": "rumble", "donor": "Bea"},
            {"source": self.source, "event": "timer", "time": "14:30", "minutes": "45"},
            {"source": self.source, "event": "goal", "tier": "2", "total": "500"},
            {"source": 'ON DONATION THEN SEND CHAT TO ALL "{TOTALLINE}"', "platform": "kick", "currency": "KICKS",
             "amount": "500"},
            {"source": "ON DONATION IF AMOUNT IS ABOVE lots THEN"},
        ]
        for payload in runs:
            self.assertTrue(dry_run(self.host, payload)["dry_run"])
        for bad in ({"source": ""}, {"source": "x", "event": "chat"}, {"source": "x", "tier": "9"},
                    {"source": "x", "stream_id": "nope"}, {"source": "x", "time": "25:99"}):
            with self.assertRaises(ValueError):
                dry_run(self.host, bad)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.host.claim(), [])

    def test_guard_report(self):
        line = self.source.split("\n")[0]
        result = dry_run(self.host, {"source": line, "amount": "10", "platform": "youtube", "donor": "Ada"})
        action = result["lines"][0]["actions"][0]
        self.assertEqual((action["would"], action["target"]), ("skip", "youtube"))
        self.assertIn("already thanked", action["why"])
        self.assertEqual([check["name"] for check in action["checks"]], ["PLATFORM", "Repeat", "Chat cap"])
        self.assertIn("1 of 6", action["checks"][-1]["message"])
        # Another donor is not a repeat.
        other = dry_run(self.host, {"source": line, "amount": "10", "platform": "youtube", "donor": "Bea"})
        self.assertEqual(other["lines"][0]["actions"][0]["would"], "chat")
        self.assertEqual(other["lines"][0]["actions"][0]["final"], "Thanks @Bea!")
        # A line that is not in the running script has no history to check.
        loose = dry_run(self.host, {"source": 'ON DONATION THEN SEND CHAT TO PLATFORM "hey"', "donor": "Ada", "amount": "10"})
        checks = loose["lines"][0]["actions"][0]["checks"]
        self.assertIn("Not checked", [c for c in checks if c["name"] == "Repeat"][0]["message"])
        # Two minutes later the thank-you has expired.
        self.now += 121
        later = dry_run(self.host, {"source": line, "amount": "10", "platform": "youtube", "donor": "Ada"})
        self.assertEqual(later["lines"][0]["actions"][0]["would"], "chat")

    def test_cap_platform_and_other(self):
        self.host._guards[self.stream]["sent"]["youtube"] = [self.now] * CHAT_LINES_PER_MINUTE
        full = dry_run(self.host, {"source": 'ON DONATION THEN SEND CHAT TO YOUTUBE "x"', "donor": "Cy"})
        self.assertEqual(full["lines"][0]["actions"][0]["would"], "skip")
        self.assertIn("6 chat lines", full["lines"][0]["actions"][0]["why"])
        rumble = dry_run(self.host, {"source": 'ON DONATION THEN SEND CHAT TO PLATFORM "x"', "platform": "rumble"})
        self.assertIn("cannot be sent from here", rumble["lines"][0]["actions"][0]["why"])
        self.assertTrue(any("no real origin" in note or "has none" in note for note in rumble["notes"]))
        other = dry_run(self.host, {"source": 'ON DONATION THEN SEND CHAT TO PLATFORM "x"', "platform": "other"})
        self.assertIn("Other-platform", other["lines"][0]["actions"][0]["why"])

    def test_one_run_counts_its_own_sends(self):
        # One slot used by the real thank-you in setUp; four more, so one is left.
        self.host._guards[self.stream]["sent"]["youtube"] += [self.now] * 4
        source = "\n".join(f'ON DONATION THEN SEND CHAT TO YOUTUBE "line {n}"' for n in range(3))
        before = self.snapshot()
        result = dry_run(self.host, {"source": source, "donor": "Cy", "amount": "3"})
        self.assertEqual([line["actions"][0]["would"] for line in result["lines"]], ["chat", "skip", "skip"])
        self.assertIn("6 chat lines", result["lines"][1]["actions"][0]["why"])
        self.assertIn("counting 1 this test would send first", result["lines"][1]["actions"][0]["checks"][-1]["message"])
        # Two chats to one target on one line are already one action, as in a real run.
        twice = dry_run(self.host, {"source": 'ON DONATION THEN SEND CHAT TO KICK "a" AND SEND CHAT TO KICK "b"',
                                    "donor": "Cy", "amount": "3", "platform": "kick"})
        self.assertEqual([action["would"] for action in twice["lines"][0]["actions"]], ["chat"])
        # The same line twice in one test: the second copy repeats the first copy's thank-you
        # only if it is the same saved line, so two unsaved copies are two lines.
        pair = dry_run(self.host, {"source": 'ON DONATION THEN SEND CHAT TO KICK "a"\nON DONATION THEN SEND CHAT TO KICK "a"',
                                   "donor": "Cy", "amount": "3", "platform": "kick"})
        self.assertEqual([entry["actions"][0]["would"] for entry in pair["lines"]], ["chat", "chat"])
        # ALL uses separate counters, so all three would send.
        every = dry_run(self.host, {"source": 'ON DONATION THEN SEND CHAT TO ALL "hi"', "donor": "Cy", "amount": "3"})
        self.assertEqual([a["would"] for a in every["lines"][0]["actions"]], ["chat", "chat", "chat"])
        # Nothing of that tally is kept.
        self.assertEqual(self.snapshot(), before)
        again = dry_run(self.host, {"source": source, "donor": "Cy", "amount": "3"})
        self.assertEqual(again["lines"][0]["actions"][0]["would"], "chat")

    def test_blank_goal_tier_is_the_ledger_tier(self):
        result = dry_run(self.host, {"source": 'ON GOAL IF TIER IS ABOVE 0 THEN SEND NOTICE TO APP "x"', "event": "goal"})
        self.assertEqual((result["ledger"]["tier"], result["ledger"]["tier_from"]), (0, "ledger"))
        self.assertFalse(result["lines"][0]["matched"])
        self.assertEqual(result["lines"][0]["groups"][0][0]["saw"], "0")
        typed = dry_run(self.host, {"source": 'ON GOAL IF TIER IS ABOVE 0 THEN SEND NOTICE TO APP "x"', "tier": "1"})
        self.assertTrue(typed["lines"][0]["matched"])

    def test_repeat_history_follows_the_copy_of_a_line(self):
        line = 'ON DONATION THEN SEND CHAT TO YOUTUBE "hi {DONOR}"'
        saved = self.store.save_blue_script("Copies", line + "\n" + line)
        self.store.set_stream_values(self.stream, {"blue_script_id": saved["id"]})
        tab = self.host._tab(self.stream)
        script = {"id": saved["id"], "name": "Copies"}
        # Line 1 thanked Ada; line 2 has no history.
        event = self.host._donation_event(
            SimpleNamespace(kind="super_chat", amount_value=10.0, amount_micros=10_000_000, currency="USD",
                            platform="youtube", message="", raw={}, author_name="Ada", dedupe_key="", channel="",
                            created_at=utc_now_iso(), external_id="", author_id=""),
            tab, datetime.now(timezone.utc), 0, 10.0,
        )
        key = self.host._repeat_key(tab, script, SimpleNamespace(line=1, target="youtube"), event)
        from streamdonations.blue_host import _guard_key
        self.host._guards[self.stream]["thanked"][_guard_key(key)] = self.now + 60
        payload = {"source": line + "\n" + line, "donor": "Ada", "amount": "10"}
        result = dry_run(self.host, payload)
        first, second = (entry["actions"][0] for entry in result["lines"])
        self.assertEqual(first["would"], "skip")
        self.assertIn("already thanked", first["why"])
        self.assertEqual(second["would"], "chat")
        self.assertEqual(second["checks"][0]["message"], "Not thanked on that line and chat in the last 2 minutes.")

    def test_ledger_facts_and_overrides(self):
        result = dry_run(self.host, {"source": 'ON TIMER IF TOTAL IS ABOVE 5 THEN SEND NOTICE TO APP "{TOTAL} at {TIME}"',
                                     "time": "09:05"})
        self.assertEqual(result["event"], "timer")
        self.assertEqual(result["ledger"]["total"], "$10.00")
        self.assertEqual(result["ledger"]["script"], "Thanks")
        self.assertEqual(result["lines"][0]["actions"][0]["final"], "$10.00 at 09:05")
        typed = dry_run(self.host, {"source": 'ON TIMER IF TOTAL IS ABOVE 5 THEN SEND NOTICE TO APP "x"', "total": "1"})
        self.assertEqual(typed["ledger"]["total_from"], "typed")
        self.assertFalse(typed["lines"][0]["matched"])
        goal = dry_run(self.host, {"source": 'ON GOAL IF TIER EQUALS 3 THEN SEND NOTICE TO APP "{TIER} {DONOR}"',
                                   "tier": "3", "donor": "Di"})
        self.assertEqual(goal["lines"][0]["actions"][0]["final"], "3 Di")
        kicks = dry_run(self.host, {"source": 'ON DONATION THEN SEND NOTICE TO APP "{RAWAMOUNT} {CURRENCY}"',
                                    "platform": "kick", "amount": "100"})
        self.assertEqual(kicks["lines"][0]["actions"][0]["final"].split()[-1], "KICKS")


def _dump(store):
    with store._lock:
        return list(store._conn.iterdump())


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.token = "b" * 32
        self.app = App(self.store, self.token, 1)
        self.server = ThreadingServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.app.ui_port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.app.ui_port}"
        # As App.start does: the first ledger is written once, before any request.
        self.store.stream_tabs()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.stop_webhook()
        self.store.close()
        self.tmp.cleanup()

    def request(self, path, body=None, origin="own"):
        headers = {"Cookie": f"sd_session={self.token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            if origin == "own":
                headers["Origin"] = self.base
            elif origin:
                headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, data=data, headers=headers,
                                         method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.headers, exc.read()
            finally:
                exc.close()

    def test_grammar_inspect_and_test_routes(self):
        status, _headers, raw = self.request("/api/blue/grammar")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), json.loads(json.dumps(grammar())))
        status, _headers, raw = self.request("/api/blue/inspect", {"source": "ON DONATION IF AMOUNT IS ABOVE x THEN"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["problems"][0]["line"], 1)
        status, _headers, raw = self.request("/api/blue/inspect", {"source": "x" * 16001})
        self.assertEqual(status, 400)
        before = _dump(self.store)
        status, _headers, raw = self.request("/api/blue/test", {"source": 'ON DONATION THEN SEND CHAT TO ALL "hi {DONOR}"',
                                                               "donor": "Ada"})
        self.assertEqual(status, 200)
        result = json.loads(raw)
        self.assertTrue(result["dry_run"])
        self.assertEqual([a["target"] for a in result["lines"][0]["actions"]], ["youtube", "rumble", "kick"])
        self.assertEqual(_dump(self.store), before)
        self.assertEqual(self.app.service.blue._pending, [])

    def test_posts_need_the_page_origin(self):
        for origin in (None, "http://evil.example"):
            for path in ("/api/blue/test", "/api/blue/inspect"):
                status, _headers, _raw = self.request(path, {"source": 'ON DONATION THEN SEND NOTICE TO APP "x"'}, origin)
                self.assertEqual(status, 403, (path, origin))

    def test_page_has_the_editor_and_script(self):
        status, headers, raw = self.request("/blue_highlight.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        page = (WEB / "index.html").read_text(encoding="utf-8")
        for needle in ('id="blue-highlight"', 'id="blue-gutter-lines"', 'id="blue-source"', 'wrap="off"',
                       'id="blue-test-form"', "Dry run - nothing is sent", '<script src="/blue_highlight.js"></script>'):
            self.assertIn(needle, page)
        self.assertNotIn("style=", page)
        # The explanation is behind Help, closed by default, in short sections.
        self.assertIn('id="blue-help-button" class="blue-help-button" aria-expanded="false" aria-controls="blue-help"', page)
        self.assertIn('<div id="blue-help" class="blue-help" role="region" aria-labelledby="blue-help-button" hidden>', page)
        panel = page[page.index('<div id="blue-help"'):page.index('<p id="blue-problems"')]
        for heading in ("Events", "Conditions and fields", "MODULO and TIME", "Targets", "Placeholders",
                        "Limits and guards", "Editor colors", "Test box"):
            self.assertIn(f"<h3>{heading}</h3>", panel)
        for placeholder in blue.PLACEHOLDERS:
            self.assertIn("{" + placeholder.upper() + "}", panel)
        self.assertNotIn('<p class="hint">A line starts with', page)
        self.assertNotIn("innerHTML", (WEB / "blue_highlight.js").read_text(encoding="utf-8"))
        # The test box has no way to send for real.
        script = (WEB / "app.js").read_text(encoding="utf-8")
        start = script.index("function runBlueTest")
        end = script.index("function setupBlueEditor")
        self.assertNotIn("/api/blue/result", script[start:end])
        self.assertNotIn("/api/blue/pending", script[start:end])
        self.assertNotIn("sendChat", script[start:end])


if __name__ == "__main__":
    unittest.main()
