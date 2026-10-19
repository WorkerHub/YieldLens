import json
import logging
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import trading_calendar


class FakeD1:
    def __init__(self, settings=None):
        self.settings = settings or {}

    def execute_sql(self, sql, params=None):
        params = list(params or [])
        upper = sql.strip().upper()
        if upper.startswith("SELECT VALUE FROM") and "SYSTEM_SETTINGS" in upper:
            value = self.settings.get(params[0])
            return [{"value": value}] if value is not None else []
        if upper.startswith("INSERT OR REPLACE INTO") and "SYSTEM_SETTINGS" in upper:
            self.settings[params[0]] = params[1]
        return []


class TestTradingCalendar(unittest.TestCase):
    def setUp(self):
        trading_calendar.reset_cache()
        self.log = logging.getLogger("test_trading_calendar")

    def tearDown(self):
        trading_calendar.reset_cache()

    @patch("common.trading_calendar.ak.tool_trade_date_hist_sina")
    def test_successful_fetch_is_cached_in_system_settings(self, mock_fetch):
        mock_fetch.return_value = pd.DataFrame(
            {"trade_date": [date(2026, 10, 16), date(2026, 10, 19)]}
        )
        d1 = FakeD1()
        calendar = trading_calendar.load_trading_calendar(d1, "yl_", self.log)

        self.assertEqual(calendar, {date(2026, 10, 16), date(2026, 10, 19)})
        self.assertIn("trading_calendar", d1.settings)
        cached = json.loads(d1.settings["trading_calendar"])
        self.assertEqual(cached["dates"], ["2026-10-16", "2026-10-19"])

    @patch("common.retry.time.sleep", lambda _: None)
    @patch("common.trading_calendar.ak.tool_trade_date_hist_sina")
    def test_upstream_failure_uses_cached_calendar(self, mock_fetch):
        mock_fetch.side_effect = ConnectionResetError("reset")
        cached = json.dumps(
            {"dates": ["2026-10-16", "2026-10-19"], "updated_at": "2026-10-18T00:00:00Z"}
        )
        d1 = FakeD1(settings={"trading_calendar": cached})

        calendar = trading_calendar.load_trading_calendar(d1, "", self.log)
        self.assertEqual(calendar, {date(2026, 10, 16), date(2026, 10, 19)})

    @patch("common.retry.time.sleep", lambda _: None)
    @patch("common.trading_calendar.ak.tool_trade_date_hist_sina")
    def test_no_upstream_and_no_cache_fails_closed(self, mock_fetch):
        mock_fetch.side_effect = ConnectionResetError("reset")
        d1 = FakeD1()

        calendar = trading_calendar.load_trading_calendar(d1, "", self.log)
        self.assertIsNone(calendar)
        self.assertIsNone(
            trading_calendar.is_trading_day(date(2026, 10, 19), self.log, calendar)
        )

    def test_weekend_is_false_without_calendar(self):
        self.assertFalse(trading_calendar.is_trading_day(date(2026, 10, 17), self.log, None))

    def test_known_calendar(self):
        calendar = {date(2026, 10, 19)}
        self.assertTrue(trading_calendar.is_trading_day(date(2026, 10, 19), self.log, calendar))
        self.assertFalse(trading_calendar.is_trading_day(date(2026, 10, 20), self.log, calendar))


if __name__ == "__main__":
    unittest.main()
