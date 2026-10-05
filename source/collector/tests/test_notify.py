"""Desktop notifications fire for new automated ledger rows only."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from streamdonations.normalize import Donation, normalize_super_chat_event
from streamdonations.notify import (
    FreedesktopNotifier,
    UnavailableNotifier,
    notification_text,
    system_notifier,
)
from streamdonations.store import Store


def _chat(event_id, message="hello", amount="1000000", currency="USD"):
    return normalize_super_chat_event(
        {
            "id": event_id,
            "snippet": {
                "channelId": "UCb",
                "supporterDetails": {"displayName": "Fan", "channelId": "UCf"},
                "commentText": message,
                "createdAt": "2024-06-01T12:00:00Z",
                "amountMicros": amount,
                "currency": currency,
            },
        }
    )


class _Recording:
    def __init__(self, explode=False):
        self.rows = []
        self.explode = explode

    def donations_added(self, donations, *, membership_price):
        if self.explode:
            raise RuntimeError("notification desk is down")
        self.rows.extend(donations)


class NotificationTextTests(unittest.TestCase):
    def test_usd_super_chat_names_the_donor_amount_and_platform(self):
        summary, body = notification_text(_chat("a", "thanks"), None)
        self.assertEqual(summary, "Fan sent $1.00")
        self.assertEqual(body, "Super Chat on YouTube\nthanks")

    def test_foreign_amount_keeps_its_currency(self):
        donation = _chat("php", "tip", amount="50000000", currency="PHP")
        summary, body = notification_text(donation, None)
        self.assertEqual(summary, "Fan sent 50.00 PHP")
        self.assertIn("Super Chat on YouTube", body)

    def test_blank_name_and_a_long_message_stay_one_notification(self):
        donation = Donation(
            dedupe_key="kick:1",
            platform="kick",
            kind="kicks",
            external_id="1",
            channel="room",
            author_name=" \n ",
            author_id="",
            message="x" * 400,
            amount_value=500,
            amount_micros=None,
            currency="KICKS",
            created_at="2024-06-01T12:00:00Z",
            raw={},
        )
        summary, body = notification_text(donation, None)
        self.assertEqual(summary, "Someone sent $5.00")
        self.assertLessEqual(len(body), 240)
        self.assertNotIn("\n\n", body)

    def test_a_priced_membership_and_a_free_anniversary_stay_distinct(self):
        member = Donation(
            dedupe_key="youtube:member:1",
            platform="youtube",
            kind="membership",
            external_id="1",
            channel="",
            author_name="Fan",
            author_id="",
            message="",
            amount_value=None,
            amount_micros=None,
            currency=None,
            created_at="2024-06-01T12:00:00Z",
            raw={},
        )
        summary, body = notification_text(member, 4.99)
        self.assertEqual(summary, "Fan sent $4.99")
        self.assertEqual(body, "Membership on YouTube")
        anniversary = Donation(
            dedupe_key="youtube:milestone:1",
            platform="youtube",
            kind="member_milestone",
            external_id="1",
            channel="",
            author_name="Fan",
            author_id="",
            message="Member for 2 months",
            amount_value=None,
            amount_micros=None,
            currency=None,
            created_at="2024-06-01T12:00:00Z",
            raw={},
        )
        summary, body = notification_text(anniversary, 4.99)
        self.assertEqual(summary, "Fan")
        self.assertEqual(body, "Super Chat on YouTube\nMember for 2 months")


class FreedesktopCommandTests(unittest.TestCase):
    def test_notify_send_is_one_argv_and_a_failure_tries_the_next_tool(self):
        calls = []

        def which(name):
            return f"/usr/bin/{name}"

        def run(argv, timeout, check, capture_output):
            calls.append(argv)
            code = 0 if argv[0].endswith("dbus-send") else 1
            return subprocess.CompletedProcess(argv, code)

        notifier = FreedesktopNotifier(run=run, which=which, synchronous=True)
        notifier.donations_added([_chat("a")], membership_price=None)
        self.assertEqual(calls[0][0], "/usr/bin/notify-send")
        self.assertIn("--app-name=Stream Donations", calls[0])
        self.assertIn("Fan sent $1.00", calls[0])
        self.assertIn("Super Chat on YouTube\nhello", calls[0])
        self.assertEqual(calls[1][0], "/usr/bin/dbus-send")
        self.assertNotIn("--print-reply", calls[1])
        joined = " ".join(calls[1])
        self.assertIn("string:Fan sent $1.00", joined)

        calls.clear()
        notifier.donations_added([_chat("b", "again")], membership_price=None)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].endswith("dbus-send"))

    def test_missing_tools_are_reported_once(self):
        notes = []
        notifier = FreedesktopNotifier(log=notes.append, which=lambda _name: None, synchronous=True)
        notifier.donations_added([_chat("a")], membership_price=None)
        notifier.donations_added([_chat("b")], membership_price=None)
        self.assertEqual(notes, ["System notifications need notify-send, dbus-send, or busctl."])

    def test_the_platform_backend_is_selected_without_sending(self):
        notifier = system_notifier()
        if sys.platform.startswith("linux"):
            self.assertIsInstance(notifier, FreedesktopNotifier)
            self.assertIsNone(notifier._thread)
        else:
            self.assertIsInstance(notifier, UnavailableNotifier)


class StoreNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "donations.db")
        self.notifier = _Recording()
        self.store._notifier = self.notifier

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_only_a_new_automated_row_is_announced(self):
        self.store.set_setting("membership_price", "4.99")
        first = _chat("live")
        self.assertEqual(self.store.add_many([first, first]), 1)
        self.assertEqual(self.store.add_many([first]), 0)
        self.assertEqual([row.dedupe_key for row in self.notifier.rows], [first.dedupe_key])
        manual = self.store.add_manual_entry(
            author_name="Ada",
            platform="other",
            kind="donation",
            amount=4,
        )
        self.assertIsInstance(manual, int)
        self.assertEqual(len(self.notifier.rows), 1)

    def test_a_broken_notifier_does_not_drop_the_row(self):
        self.store._notifier = _Recording(explode=True)
        self.assertEqual(self.store.add_many([_chat("kept")]), 1)
        rows, total = self.store.list_donations()
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["author_name"], "Fan")
