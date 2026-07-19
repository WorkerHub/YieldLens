import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.ticker_utils import (
    code_to_akshare_daily_prefix,
    infer_exchange,
    normalize_ticker,
)


class TestTickerUtils(unittest.TestCase):
    def test_normalize_bare_code(self):
        self.assertEqual(normalize_ticker("600519"), "600519")

    def test_normalize_prefixed(self):
        self.assertEqual(normalize_ticker("sh600519"), "600519")
        self.assertEqual(normalize_ticker("SH600519"), "600519")
        self.assertEqual(normalize_ticker("sz000001"), "000001")

    def test_normalize_suffixed(self):
        self.assertEqual(normalize_ticker("600519.SH"), "600519")
        self.assertEqual(normalize_ticker("000001.sz"), "000001")

    def test_normalize_invalid_raises(self):
        with self.assertRaises(ValueError):
            normalize_ticker("not-a-ticker")

    def test_infer_exchange(self):
        self.assertEqual(infer_exchange("600519"), "SH")
        self.assertEqual(infer_exchange("688981"), "SH")
        self.assertEqual(infer_exchange("000001"), "SZ")
        self.assertEqual(infer_exchange("300750"), "SZ")
        self.assertEqual(infer_exchange("830799"), "BJ")

    def test_code_to_akshare_daily_prefix(self):
        self.assertEqual(code_to_akshare_daily_prefix("600519"), "sh600519")
        self.assertEqual(code_to_akshare_daily_prefix("000001"), "sz000001")
        self.assertEqual(code_to_akshare_daily_prefix("sh600519"), "sh600519")


if __name__ == "__main__":
    unittest.main()
