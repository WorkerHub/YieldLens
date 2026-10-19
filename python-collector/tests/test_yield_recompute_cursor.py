import os
import sys
import unittest
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


class FakeD1:
    def __init__(self):
        self.settings = {}

    def execute_sql(self, sql, params=None):
        params = list(params or [])
        upper = sql.strip().upper()
        if upper.startswith("SELECT VALUE FROM") and "SYSTEM_SETTINGS" in upper:
            value = self.settings.get(params[0])
            return [{"value": value}] if value is not None else []
        if upper.startswith("INSERT OR REPLACE INTO") and "SYSTEM_SETTINGS" in upper:
            self.settings[params[0]] = params[1]
        return []


class TestYieldRecomputeCursor(unittest.TestCase):
    def test_cursor_resumes_across_runs(self):
        codes = [f"{i:06d}" for i in range(1, 6)]
        d1 = FakeD1()
        processed = []

        def fake_recompute(_d1, _prefix, code):
            processed.append(code)

        with patch.object(collect_dividends, "recompute_yields_for_stock", side_effect=fake_recompute):
            first = collect_dividends.run_yield_recompute(d1, "", codes, batch_size=2)
            second = collect_dividends.run_yield_recompute(d1, "", codes, batch_size=2)
            third = collect_dividends.run_yield_recompute(d1, "", codes, batch_size=2)

        self.assertEqual(processed, ["000001", "000002", "000003", "000004", "000005"])
        self.assertFalse(first["completed"])
        self.assertEqual(first["cursor"], "000002")
        self.assertFalse(second["completed"])
        self.assertEqual(second["cursor"], "000004")
        self.assertTrue(third["completed"])
        self.assertEqual(third["cursor"], "")
        self.assertEqual(d1.settings["yield_recompute_cursor"], "")
        self.assertIn("yield_recompute_completed_at", d1.settings)

    def test_soft_deadline_saves_cursor(self):
        codes = ["000001", "000002", "000003", "000004", "000005"]
        d1 = FakeD1()
        processed = []
        ticks = iter([0, 1, 2, 100])

        def fake_recompute(_d1, _prefix, code):
            processed.append(code)

        with patch.object(collect_dividends, "recompute_yields_for_stock", side_effect=fake_recompute):
            progress = collect_dividends.run_yield_recompute(
                d1,
                "",
                codes,
                batch_size=5,
                deadline_seconds=10,
                checkpoint_every=1,
                monotonic=lambda: next(ticks),
            )

        self.assertEqual(processed, ["000001", "000002"])
        self.assertEqual(progress["stopped_reason"], "deadline")
        self.assertFalse(progress["completed"])
        self.assertEqual(progress["cursor"], "000002")
        self.assertEqual(d1.settings["yield_recompute_cursor"], "000002")

    def test_one_stock_failure_does_not_abort_slice(self):
        codes = ["000001", "000002", "000003"]
        d1 = FakeD1()

        def fake_recompute(_d1, _prefix, code):
            if code == "000002":
                raise RuntimeError("D1 failed")

        with patch.object(collect_dividends, "recompute_yields_for_stock", side_effect=fake_recompute):
            progress = collect_dividends.run_yield_recompute(d1, "", codes, batch_size=3)

        self.assertEqual(progress["success"], 2)
        self.assertEqual(progress["failure"], 1)
        self.assertEqual(progress["failed_codes"], ["000002"])
        self.assertTrue(progress["completed"])


if __name__ == "__main__":
    unittest.main()
