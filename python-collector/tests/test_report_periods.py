import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CLOUDFLARE_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_D1_DATABASE_ID", "test-db")
os.environ.setdefault("CLOUDFLARE_API_TOKEN", "test-token")

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "collect_dividends", os.path.join(os.path.dirname(os.path.dirname(__file__)), "collect_dividends.py")
)
collect_dividends = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect_dividends)


class TestLatestDisclosedReportPeriod(unittest.TestCase):
    def test_after_mid_year_uses_current_half_year(self):
        self.assertEqual(
            collect_dividends.latest_disclosed_report_period(date(2026, 10, 19)),
            "20260630",
        )
        self.assertEqual(
            collect_dividends.latest_disclosed_report_period(date(2026, 7, 1)),
            "20260630",
        )

    def test_first_half_uses_previous_annual_period(self):
        self.assertEqual(
            collect_dividends.latest_disclosed_report_period(date(2026, 6, 30)),
            "20251231",
        )
        self.assertEqual(
            collect_dividends.latest_disclosed_report_period(date(2026, 3, 15)),
            "20251231",
        )
        self.assertEqual(
            collect_dividends.latest_disclosed_report_period(date(2026, 1, 5)),
            "20251231",
        )


class TestReportPeriods(unittest.TestCase):
    def test_daily_window_covers_three_disclosed_periods(self):
        periods = collect_dividends.daily_report_periods(3, today=date(2026, 10, 19))
        self.assertEqual(periods, ["20260630", "20251231", "20250630"])

    def test_backfill_is_descending_and_unique(self):
        periods = collect_dividends.report_periods(10, today=date(2026, 10, 19))
        self.assertEqual(len(periods), 20)
        self.assertEqual(periods[0], "20260630")
        self.assertEqual(periods[-1], "20161231")
        self.assertEqual(len(set(periods)), len(periods))
        self.assertEqual(periods, sorted(periods, reverse=True))

    def test_previous_year_annual_is_first_in_april(self):
        periods = collect_dividends.report_periods(2, today=date(2026, 4, 20))
        self.assertEqual(periods[:4], ["20251231", "20250630", "20241231", "20240630"])

    def test_zero_years_returns_empty(self):
        self.assertEqual(collect_dividends.report_periods(0), [])


class TestCollectMode(unittest.TestCase):
    def test_default_is_daily(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COLLECT_MODE", None)
            self.assertEqual(collect_dividends.resolve_collect_mode(), "daily")

    def test_invalid_mode_rejected(self):
        with patch.dict(os.environ, {"COLLECT_MODE": "everything"}):
            with self.assertRaises(ValueError):
                collect_dividends.resolve_collect_mode()


if __name__ == "__main__":
    unittest.main()
