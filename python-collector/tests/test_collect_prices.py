import os
import sys
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CLOUDFLARE_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_D1_DATABASE_ID", "test-db")
os.environ.setdefault("CLOUDFLARE_API_TOKEN", "test-token")

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "collect_prices", os.path.join(os.path.dirname(os.path.dirname(__file__)), "collect_prices.py")
)
collect_prices = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect_prices)


class FakeD1:
    def __init__(self):
        self.settings = {}
        self.upserts = []
        self.updates = []
        self.sql = []

    def execute_sql(self, sql, params=None):
        params = list(params or [])
        self.sql.append((sql, params))
        upper = sql.strip().upper()
        if upper.startswith("SELECT VALUE FROM") and "SYSTEM_SETTINGS" in upper:
            key = params[0]
            if key in self.settings:
                return [{"value": self.settings[key]}]
            return []
        if upper.startswith("INSERT OR REPLACE INTO") and "SYSTEM_SETTINGS" in upper:
            self.settings[params[0]] = params[1]
        return []

    def upsert_rows(self, table, rows, batch_size=None):
        self.upserts.append((table, rows))
        return len(rows)

    def update_rows(self, table, key_column, rows, columns, static_values=None, batch_size=None):
        self.updates.append((table, key_column, rows, columns, static_values))
        return len(rows)


def _snapshot_frame():
    return pd.DataFrame(
        [
            {
                "代码": "600519",
                "最新价": 1500.0,
                "今开": 1490.0,
                "最高": float("nan"),
                "最低": 1480.0,
                "成交量": 1000.0,
                "成交额": 1_500_000.0,
            },
            {
                "代码": "000001",
                "最新价": 11.2,
                "今开": 11.0,
                "最高": 11.3,
                "最低": 10.9,
                "成交量": float("nan"),
                "成交额": 20_000.0,
            },
            {
                "代码": "000002",
                "最新价": float("nan"),
                "今开": 8.0,
                "最高": 8.1,
                "最低": 7.9,
                "成交量": 100.0,
                "成交额": 800.0,
            },
        ]
    )


class TestSnapshotWriter(unittest.TestCase):
    def test_writes_today_rows_and_updates_last_price(self):
        d1 = FakeD1()
        stats = collect_prices.write_spot_snapshot(
            d1,
            "yl_",
            _snapshot_frame(),
            trade_date=date(2026, 10, 19),
            now_iso="2026-10-19T07:10:00Z",
        )

        self.assertEqual(stats["rows"], 2)
        self.assertEqual(stats["updated"], 2)
        self.assertEqual(stats["skipped"], 1)
        table, rows = d1.upserts[0]
        self.assertEqual(table, "yl_stock_prices")
        self.assertEqual([r["code"] for r in rows], ["600519", "000001"])
        self.assertTrue(all(r["trade_date"] == "2026-10-19" for r in rows))
        self.assertIsNone(rows[0]["high"])
        self.assertIsNone(rows[1]["volume"])
        self.assertEqual(rows[0]["close"], 1500.0)

        table, key, updated, columns, static = d1.updates[0]
        self.assertEqual(table, "yl_stocks")
        self.assertEqual(key, "code")
        self.assertEqual(columns, ["last_price"])
        self.assertEqual(updated[0], {"code": "600519", "last_price": 1500.0})
        self.assertEqual(static["price_updated_at"], "2026-10-19T07:10:00Z")

    def test_empty_snapshot_is_a_noop(self):
        d1 = FakeD1()
        df = pd.DataFrame([{"代码": "600519", "最新价": float("nan")}])
        stats = collect_prices.write_spot_snapshot(d1, "", df, trade_date=date(2026, 10, 19))
        self.assertEqual(stats["rows"], 0)
        self.assertEqual(d1.upserts, [])
        self.assertEqual(d1.updates, [])


class TestCollectOneStock(unittest.TestCase):
    def test_refetches_today_even_when_watermark_is_today(self):
        today = date(2026, 10, 19)
        df = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2026-10-19"),
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 100.0,
                    "amount": 1000.0,
                }
            ]
        )
        d1 = FakeD1()
        with patch.object(collect_prices, "beijing_today", return_value=today):
            with patch.object(collect_prices, "get_watermark", return_value="2026-10-19"):
                with patch.object(collect_prices, "fetch_ohlcv", return_value=df) as fetch:
                    ok = collect_prices.collect_one_stock(d1, "yl_", "600519", backfill_mode=False)

        self.assertTrue(ok)
        fetch.assert_called_once_with("600519", "20261014", "20261019")
        self.assertEqual(d1.upserts[0][1][0]["trade_date"], "2026-10-19")
        # last_price is only written when today's bar is present.
        self.assertTrue(any("UPDATE yl_stocks SET last_price" in sql for sql, _ in d1.sql))

    def test_nan_ohlc_values_are_cleaned(self):
        today = date(2026, 10, 19)
        df = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2026-10-19 00:00:00"),
                    "open": float("nan"),
                    "high": 10.5,
                    "low": float("inf"),
                    "close": 10.2,
                    "volume": float("nan"),
                    "amount": 1000.0,
                }
            ]
        )
        d1 = FakeD1()
        with patch.object(collect_prices, "beijing_today", return_value=today):
            with patch.object(collect_prices, "get_watermark", return_value=None):
                with patch.object(collect_prices, "fetch_ohlcv", return_value=df):
                    collect_prices.collect_one_stock(d1, "", "600519", backfill_mode=False)

        row = d1.upserts[0][1][0]
        self.assertIsNone(row["open"])
        self.assertIsNone(row["low"])
        self.assertIsNone(row["volume"])
        self.assertEqual(row["close"], 10.2)
        self.assertEqual(row["trade_date"], "2026-10-19")

    def test_no_history_starts_one_year_back(self):
        today = date(2026, 10, 19)
        d1 = FakeD1()
        with patch.object(collect_prices, "beijing_today", return_value=today):
            with patch.object(collect_prices, "get_watermark", return_value=None):
                with patch.object(collect_prices, "fetch_ohlcv", return_value=pd.DataFrame()) as fetch:
                    collect_prices.collect_one_stock(d1, "", "600519", backfill_mode=False)
        fetch.assert_called_once_with("600519", "20251019", "20261019")


class TestModeResolution(unittest.TestCase):
    def test_auto_is_snapshot_before_close(self):
        now = datetime(2026, 10, 19, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with patch.dict(os.environ, {"PRICE_MODE": "auto"}, clear=False):
            os.environ.pop("BACKFILL_MODE", None)
            self.assertEqual(collect_prices.resolve_price_mode(now=now), "snapshot")

    def test_auto_is_close_after_close(self):
        now = datetime(2026, 10, 19, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
        with patch.dict(os.environ, {"PRICE_MODE": "auto"}, clear=False):
            os.environ.pop("BACKFILL_MODE", None)
            self.assertEqual(collect_prices.resolve_price_mode(now=now), "close")

    def test_explicit_modes_win(self):
        now = datetime(2026, 10, 19, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(collect_prices.resolve_price_mode("close", now=now), "close")
        self.assertEqual(collect_prices.resolve_price_mode("snapshot", now=now), "snapshot")
        self.assertEqual(collect_prices.resolve_price_mode("backfill", now=now), "backfill")

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            collect_prices.resolve_price_mode("sometimes")

    def test_legacy_backfill_flag(self):
        now = datetime(2026, 10, 19, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with patch.dict(os.environ, {"PRICE_MODE": "auto", "BACKFILL_MODE": "true"}, clear=False):
            self.assertEqual(collect_prices.resolve_price_mode(now=now), "backfill")


class TestShardedHistory(unittest.TestCase):
    def test_cursor_resumes_and_completes(self):
        codes = [f"{i:06d}" for i in range(1, 6)]
        d1 = FakeD1()
        processed = []

        def fake_collect(_d1, _prefix, code, _backfill):
            processed.append(code)
            return True

        with patch.object(collect_prices, "collect_one_stock", side_effect=fake_collect):
            first = collect_prices.run_sharded_history(
                d1, "", codes, mode="history", batch_size=2, today=date(2026, 10, 19)
            )
            second = collect_prices.run_sharded_history(
                d1, "", codes, mode="history", batch_size=2, today=date(2026, 10, 19)
            )
            third = collect_prices.run_sharded_history(
                d1, "", codes, mode="history", batch_size=2, today=date(2026, 10, 19)
            )

        self.assertEqual(processed, ["000001", "000002", "000003", "000004", "000005"])
        self.assertFalse(first["completed"])
        self.assertEqual(first["processed"], 2)
        self.assertFalse(second["completed"])
        self.assertEqual(second["processed"], 4)
        self.assertTrue(third["completed"])
        self.assertEqual(third["processed"], 5)
        self.assertEqual(d1.settings["price_history_cursor:2026-10-19"], "")

    def test_failures_are_counted_not_raised(self):
        codes = ["000001", "000002", "000003"]
        d1 = FakeD1()
        results = iter([True, False, True])
        with patch.object(collect_prices, "collect_one_stock", side_effect=lambda *_: next(results)):
            progress = collect_prices.run_sharded_history(
                d1, "", codes, mode="history", batch_size=3, today=date(2026, 10, 19)
            )
        self.assertEqual(progress["success"], 2)
        self.assertEqual(progress["failure"], 1)
        self.assertTrue(progress["completed"])

    def test_one_stock_exception_does_not_abort_the_shard(self):
        codes = ["000001", "000002", "000003"]
        d1 = FakeD1()
        calls = []

        def fake_collect(_d1, _prefix, code, _backfill):
            calls.append(code)
            if code == "000002":
                raise RuntimeError("D1 write failed")
            return True

        with patch.object(collect_prices, "collect_one_stock", side_effect=fake_collect):
            progress = collect_prices.run_sharded_history(
                d1, "", codes, mode="history", batch_size=3, today=date(2026, 10, 19)
            )
        self.assertEqual(calls, codes)
        self.assertEqual(progress["success"], 2)
        self.assertEqual(progress["failure"], 1)
        self.assertTrue(progress["completed"])


if __name__ == "__main__":
    unittest.main()
