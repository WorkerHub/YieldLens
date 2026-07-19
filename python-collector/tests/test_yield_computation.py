import os
import sys
import unittest

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


class TestPriceOnOrBefore(unittest.TestCase):
    def setUp(self):
        self.prices = [
            {"trade_date": "2020-01-01", "close": 10.0},
            {"trade_date": "2020-06-01", "close": 12.0},
            {"trade_date": "2021-01-01", "close": 15.0},
        ]

    def test_exact_match(self):
        self.assertEqual(collect_dividends.price_on_or_before(self.prices, "2020-06-01"), 12.0)

    def test_between_dates_returns_earlier(self):
        self.assertEqual(collect_dividends.price_on_or_before(self.prices, "2020-09-01"), 12.0)

    def test_before_all_dates_returns_none(self):
        self.assertIsNone(collect_dividends.price_on_or_before(self.prices, "2019-01-01"))

    def test_after_all_dates_returns_latest(self):
        self.assertEqual(collect_dividends.price_on_or_before(self.prices, "2022-01-01"), 15.0)


class TestReportPeriods(unittest.TestCase):
    def test_generates_year_end_and_mid_year(self):
        periods = collect_dividends.report_periods(2)
        # 2 years -> 4 periods, most recent first
        self.assertEqual(len(periods), 4)
        for p in periods:
            self.assertEqual(len(p), 8)
            self.assertTrue(p.endswith("1231") or p.endswith("0630"))


class TestDividendStatusParsing(unittest.TestCase):
    def test_implemented_variants(self):
        self.assertEqual(collect_dividends.parse_dividend_status("实施分配"), "implemented")
        self.assertEqual(collect_dividends.parse_dividend_status("实施"), "implemented")

    def test_proposal_variants(self):
        self.assertEqual(collect_dividends.parse_dividend_status("预案"), "proposal")
        self.assertEqual(collect_dividends.parse_dividend_status("董事会预案"), "proposal")

    def test_none_defaults_to_proposal(self):
        self.assertEqual(collect_dividends.parse_dividend_status(None), "proposal")

    def test_unknown_string_with_shishi_substring(self):
        self.assertEqual(collect_dividends.parse_dividend_status("已实施完成"), "implemented")


if __name__ == "__main__":
    unittest.main()
