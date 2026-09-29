"""Pacific-time reset for the YouTube Data API day."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from streamdonations.youtube_quota import next_pacific_midnight


class QuotaResetTests(unittest.TestCase):
    def test_reset_is_the_next_pacific_midnight(self):
        before = datetime(2026, 9, 29, 6, 14, tzinfo=ZoneInfo("UTC"))
        reset = datetime.fromtimestamp(next_pacific_midnight(before), ZoneInfo("UTC"))
        self.assertEqual(reset.isoformat(), "2026-09-29T07:00:00+00:00")

        after = datetime(2026, 9, 29, 7, 30, tzinfo=ZoneInfo("UTC"))
        reset = datetime.fromtimestamp(next_pacific_midnight(after), ZoneInfo("UTC"))
        self.assertEqual(reset.isoformat(), "2026-09-30T07:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
