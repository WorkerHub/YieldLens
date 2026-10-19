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
    "collect_prices", os.path.join(os.path.dirname(os.path.dirname(__file__)), "collect_prices.py")
)
collect_prices = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect_prices)

from common.retry import RetryExhausted, call_with_retries
from common.stock_list import (
    DATABASE_SOURCE,
    FALLBACK_SOURCE,
    PRIMARY_SOURCE,
    fetch_stock_list,
    sync_stock_list,
)


class FakeD1:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.upserts = []
        self.updates = []

    def execute_sql(self, sql, params=None):
        if sql.strip().upper().startswith("SELECT CODE, NAME, IS_ST"):
            return self.existing
        return []

    def upsert_rows(self, table, rows, batch_size=None):
        self.upserts.append((table, rows))
        return len(rows)

    def update_rows(self, table, key_column, rows, columns, static_values=None, batch_size=None):
        self.updates.append((table, key_column, rows, columns, static_values))
        return len(rows)


def _fallback_frame():
    return pd.DataFrame(
        [
            {"代码": "600519", "名称": "贵州茅台"},
            {"代码": "000001", "名称": "*ST 平安"},
        ]
    )


class TestFetchStockListFallback(unittest.TestCase):
    @patch("common.retry.time.sleep", lambda _: None)
    def test_primary_failure_uses_snapshot_fallback(self):
        with patch.object(
            collect_prices.ak, "stock_info_a_code_name", side_effect=ConnectionResetError("reset")
        ):
            with patch.object(collect_prices.ak, "stock_zh_a_spot_em", return_value=_fallback_frame()):
                result = fetch_stock_list(collect_prices.log)

        self.assertEqual(result.source, FALLBACK_SOURCE)
        self.assertEqual([r["code"] for r in result.rows], ["600519", "000001"])
        self.assertTrue(result.used_fallback)
        self.assertTrue(any("reset" in e for e in result.errors))

    @patch("common.retry.time.sleep", lambda _: None)
    def test_both_sources_failing_raises(self):
        with patch.object(
            collect_prices.ak, "stock_info_a_code_name", side_effect=ConnectionResetError("reset")
        ):
            with patch.object(
                collect_prices.ak, "stock_zh_a_spot_em", side_effect=ConnectionResetError("reset2")
            ):
                with self.assertRaises(RuntimeError):
                    fetch_stock_list(collect_prices.log)

    @patch("common.retry.time.sleep", lambda _: None)
    def test_primary_success_does_not_call_fallback(self):
        frame = pd.DataFrame([{"code": "600519", "name": "贵州茅台"}])
        with patch.object(collect_prices.ak, "stock_info_a_code_name", return_value=frame):
            with patch.object(collect_prices.ak, "stock_zh_a_spot_em") as fallback:
                result = fetch_stock_list(collect_prices.log)
        self.assertEqual(result.source, PRIMARY_SOURCE)
        fallback.assert_not_called()


class TestSyncStockList(unittest.TestCase):
    @patch("common.retry.time.sleep", lambda _: None)
    def test_uses_database_when_both_upstream_sources_fail(self):
        d1 = FakeD1(existing=[{"code": "600519", "name": "贵州茅台", "is_st": 0}])
        with patch.object(
            collect_prices.ak, "stock_info_a_code_name", side_effect=ConnectionResetError("reset")
        ):
            with patch.object(
                collect_prices.ak, "stock_zh_a_spot_em", side_effect=ConnectionResetError("reset2")
            ):
                result = sync_stock_list(d1, "yl_", collect_prices.log)

        self.assertEqual(result.source, DATABASE_SOURCE)
        self.assertEqual(result.rows, [{"code": "600519", "name": "贵州茅台"}])
        self.assertEqual(d1.upserts, [])
        self.assertEqual(d1.updates, [])

    @patch("common.retry.time.sleep", lambda _: None)
    def test_empty_database_and_failed_upstream_raises(self):
        d1 = FakeD1(existing=[])
        with patch.object(
            collect_prices.ak, "stock_info_a_code_name", side_effect=ConnectionResetError("reset")
        ):
            with patch.object(
                collect_prices.ak, "stock_zh_a_spot_em", side_effect=ConnectionResetError("reset2")
            ):
                with self.assertRaises(RuntimeError):
                    sync_stock_list(d1, "yl_", collect_prices.log)

    @patch("common.retry.time.sleep", lambda _: None)
    def test_inserts_new_and_refreshes_existing_name_and_st(self):
        d1 = FakeD1(existing=[{"code": "600519", "name": "旧名称", "is_st": 0}])
        with patch.object(
            collect_prices.ak, "stock_info_a_code_name", side_effect=ConnectionResetError("reset")
        ):
            with patch.object(collect_prices.ak, "stock_zh_a_spot_em", return_value=_fallback_frame()):
                sync_stock_list(d1, "yl_", collect_prices.log)

        self.assertEqual(len(d1.upserts), 1)
        table, rows = d1.upserts[0]
        self.assertEqual(table, "yl_stocks")
        self.assertEqual([r["code"] for r in rows], ["000001"])
        self.assertEqual(rows[0]["is_st"], 1)

        table, key, updates, columns, _static = d1.updates[0]
        self.assertEqual(table, "yl_stocks")
        self.assertEqual(key, "code")
        self.assertEqual(columns, ["name", "is_st"])
        self.assertEqual(updates, [{"code": "600519", "name": "贵州茅台", "is_st": 0}])


class TestRetryHelper(unittest.TestCase):
    def test_retries_with_exponential_backoff_and_succeeds(self):
        attempts = []
        sleeps = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionResetError("reset")
            return "ok"

        result = call_with_retries(
            flaky,
            description="test call",
            max_attempts=4,
            base_delay=1.0,
            jitter=0.0,
            sleep=sleeps.append,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_exhausted_retries_raise_with_attempt_count(self):
        def always_fails():
            raise ConnectionResetError("reset")

        with self.assertRaises(RetryExhausted) as ctx:
            call_with_retries(
                always_fails,
                description="test call",
                max_attempts=3,
                jitter=0.0,
                sleep=lambda _: None,
            )
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertIn("reset", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
