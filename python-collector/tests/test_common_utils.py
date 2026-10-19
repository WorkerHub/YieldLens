import os
import sys
import unittest
from datetime import date, datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.numeric import clean_date, is_missing, to_float, to_int
from common.table_prefix import get_table_prefix, normalize_table_prefix
from common.ticker_utils import code_to_akshare_daily_prefix, infer_exchange


class TestNumericCleaning(unittest.TestCase):
    def test_missing_values(self):
        for value in (None, float("nan"), float("inf"), pd.NA, pd.NaT):
            self.assertTrue(is_missing(value), repr(value))
        for value in ("-", "", "nan", 0, 1.5):
            self.assertFalse(is_missing(value), repr(value))

    def test_to_float(self):
        self.assertEqual(to_float("1,234.5"), 1234.5)
        self.assertEqual(to_float(" 2.5 "), 2.5)
        self.assertEqual(to_float(float("nan"), 0.0), 0.0)
        self.assertEqual(to_float(float("inf"), 0.0), 0.0)
        self.assertIsNone(to_float("-"))
        self.assertIsNone(to_float("not a number"))
        self.assertEqual(to_float(3), 3.0)

    def test_to_int(self):
        self.assertEqual(to_int("3"), 3)
        self.assertIsNone(to_int(float("nan")))
        self.assertEqual(to_int(float("nan"), 0), 0)

    def test_clean_date(self):
        self.assertEqual(clean_date(pd.Timestamp("2026-06-20 00:00:00")), "2026-06-20")
        self.assertEqual(clean_date(datetime(2026, 6, 20, 15, 30)), "2026-06-20")
        self.assertEqual(clean_date(date(2026, 6, 20)), "2026-06-20")
        self.assertEqual(clean_date("2026/6/20"), "2026-06-20")
        self.assertEqual(clean_date("2026-06-20 00:00:00"), "2026-06-20")
        self.assertIsNone(clean_date(pd.NaT))
        self.assertIsNone(clean_date("not a date"))


class TestTablePrefix(unittest.TestCase):
    def test_normalisation_matches_worker(self):
        self.assertEqual(normalize_table_prefix(""), "")
        self.assertEqual(normalize_table_prefix("   "), "")
        self.assertEqual(normalize_table_prefix("yl"), "yl_")
        self.assertEqual(normalize_table_prefix("yl_"), "yl_")
        self.assertEqual(normalize_table_prefix("_tenant"), "_tenant_")

    def test_invalid_prefix_rejected(self):
        for raw in ("123", "yl-db", "yl;drop"):
            with self.assertRaises(ValueError):
                normalize_table_prefix(raw)

    def test_get_table_prefix_reads_env(self):
        old = os.environ.get("TABLE_PREFIX")
        try:
            os.environ["TABLE_PREFIX"] = "yl"
            self.assertEqual(get_table_prefix(), "yl_")
            os.environ["TABLE_PREFIX"] = ""
            self.assertEqual(get_table_prefix(), "")
        finally:
            if old is None:
                os.environ.pop("TABLE_PREFIX", None)
            else:
                os.environ["TABLE_PREFIX"] = old


class TestTickerExchange(unittest.TestCase):
    def test_920_prefix_is_beijing(self):
        self.assertEqual(infer_exchange("920001"), "BJ")
        self.assertEqual(code_to_akshare_daily_prefix("920001"), "bj920001")

    def test_other_exchanges_unchanged(self):
        self.assertEqual(infer_exchange("600519"), "SH")
        self.assertEqual(infer_exchange("688981"), "SH")
        self.assertEqual(infer_exchange("900901"), "SH")
        self.assertEqual(infer_exchange("000001"), "SZ")
        self.assertEqual(infer_exchange("830799"), "BJ")


if __name__ == "__main__":
    unittest.main()
