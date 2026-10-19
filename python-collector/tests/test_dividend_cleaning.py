import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

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


class FakeD1:
    def __init__(self):
        self.table = None
        self.rows = None

    def upsert_rows(self, table, rows):
        self.table = table
        self.rows = rows
        return len(rows)


def _frame(rows):
    return pd.DataFrame(rows)


class TestDividendCleaning(unittest.TestCase):
    def test_nan_bonus_and_transfer_columns_become_zero(self):
        df = _frame(
            [
                {
                    "代码": "600519",
                    "现金分红-现金分红比例": 21.0,
                    "股权登记日": pd.Timestamp("2026-06-19"),
                    "除权除息日": pd.Timestamp("2026-06-20"),
                    "方案进度": "实施分配",
                    "送转股份-送转比例": float("nan"),
                    "送转股份-转股比例": float("nan"),
                },
                {
                    "代码": "000002",
                    "现金分红-现金分红比例": 3.0,
                    "股权登记日": pd.Timestamp("2026-06-18 00:00:00"),
                    "除权除息日": pd.Timestamp("2026-06-19 00:00:00"),
                    "方案进度": float("nan"),
                    "送转股份-送转比例": 2.0,
                    "送转股份-转股比例": None,
                },
            ]
        )
        d1 = FakeD1()
        with patch.object(collect_dividends.ak, "stock_fhps_em", return_value=df):
            with patch.object(collect_dividends.eastmoney_limiter, "wait"):
                written = collect_dividends.collect_dividends_for_period(d1, "yl_", "20251231")

        self.assertEqual(written, 2)
        self.assertEqual(d1.table, "yl_dividends")
        first, second = d1.rows

        self.assertEqual(first["per_share_div"], 2.1)
        self.assertEqual(first["bonus_share_ratio"], 0.0)
        self.assertEqual(first["transfer_ratio"], 0.0)
        self.assertEqual(first["record_date"], "2026-06-19")
        self.assertEqual(first["ex_div_date"], "2026-06-20")
        self.assertEqual(first["status"], "implemented")

        self.assertEqual(second["bonus_share_ratio"], 2.0)
        self.assertEqual(second["transfer_ratio"], 0.0)
        self.assertEqual(second["status"], "proposal")
        self.assertEqual(second["record_date"], "2026-06-18")
        self.assertEqual(second["report_period"], "2025-12-31")

        for row in d1.rows:
            for key, value in row.items():
                if isinstance(value, float):
                    self.assertEqual(value, value, f"{key} is NaN")
                    self.assertNotEqual(value, float("inf"))

    def test_rows_without_cash_dividend_are_skipped(self):
        df = _frame(
            [
                {
                    "代码": "000001",
                    "现金分红-现金分红比例": float("nan"),
                    "股权登记日": pd.Timestamp("2026-06-19"),
                    "除权除息日": pd.Timestamp("2026-06-20"),
                    "方案进度": "实施",
                    "送转股份-送转比例": 0,
                    "送转股份-转股比例": 0,
                }
            ]
        )
        d1 = FakeD1()
        with patch.object(collect_dividends.ak, "stock_fhps_em", return_value=df):
            with patch.object(collect_dividends.eastmoney_limiter, "wait"):
                written = collect_dividends.collect_dividends_for_period(d1, "", "20251231")
        self.assertEqual(written, 0)
        self.assertIsNone(d1.rows)

    def test_upstream_failure_does_not_abort_period(self):
        d1 = FakeD1()
        with patch.object(
            collect_dividends.ak, "stock_fhps_em", side_effect=ConnectionResetError("reset")
        ):
            with patch.object(collect_dividends.eastmoney_limiter, "wait"):
                with patch("common.retry.time.sleep", lambda _: None):
                    written = collect_dividends.collect_dividends_for_period(d1, "", "20251231")
        self.assertEqual(written, 0)


class TestDividendStatus(unittest.TestCase):
    def test_nan_and_non_string_values_are_proposals(self):
        self.assertEqual(collect_dividends.parse_dividend_status(float("nan")), "proposal")
        self.assertEqual(collect_dividends.parse_dividend_status(pd.NA), "proposal")
        self.assertEqual(collect_dividends.parse_dividend_status(pd.NaT), "proposal")
        self.assertEqual(collect_dividends.parse_dividend_status(None), "proposal")

    def test_known_statuses(self):
        self.assertEqual(collect_dividends.parse_dividend_status("实施分配"), "implemented")
        self.assertEqual(collect_dividends.parse_dividend_status("已实施完成"), "implemented")
        self.assertEqual(collect_dividends.parse_dividend_status("董事会预案"), "proposal")


if __name__ == "__main__":
    unittest.main()
