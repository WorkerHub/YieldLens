import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


class TestCollectorWorkflows(unittest.TestCase):
    def test_price_workflow_uses_explicit_mode_and_concurrency(self):
        text = (WORKFLOWS / "collect-prices.yml").read_text(encoding="utf-8")
        self.assertIn("PRICE_MODE:", text)
        self.assertIn("github.event.inputs.mode || 'auto'", text)
        self.assertIn("concurrency:", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn('cron: "10 7 * * 1-5"', text)  # 15:10 CST close run
        # The old schedule-event-unsafe expression must be gone.
        self.assertNotIn("backfill_batch != ''", text)
        self.assertNotIn("BACKFILL_BATCH", text)

    def test_dividend_workflow_splits_daily_and_backfill(self):
        text = (WORKFLOWS / "collect-dividends.yml").read_text(encoding="utf-8")
        self.assertIn("COLLECT_MODE:", text)
        self.assertIn("github.event.inputs.mode || 'daily'", text)
        self.assertIn("options:", text)
        self.assertIn("backfill", text)
        self.assertIn("concurrency:", text)

    def test_workflows_request_only_read_permission(self):
        for name in ("collect-prices.yml", "collect-dividends.yml"):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn("permissions:", text)
            self.assertIn("contents: read", text)


if __name__ == "__main__":
    unittest.main()
