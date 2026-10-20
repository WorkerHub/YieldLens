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
        self.assertIn("- history", text)
        self.assertIn("github.event.inputs.mode == 'backfill' && '30' || '600'", text)
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
        self.assertIn("github.event.inputs.mode == 'backfill'", text)
        self.assertIn("concurrency:", text)

    def test_collector_workflows_run_on_the_self_hosted_runner(self):
        for name in ("collect-prices.yml", "collect-dividends.yml"):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn("runs-on: [self-hosted, yieldlens]", text)
            self.assertNotIn("jobs:\n  collect:\n    runs-on: ubuntu-latest", text)
            # The repository is public: contributor-controlled events must never
            # reach the team's own machine.
            self.assertNotIn("pull_request:", text)
            self.assertIn(
                "if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'",
                text,
            )
            # Anything else fails on a GitHub-hosted job, so the run is red and
            # the self-hosted runner is never assigned.
            self.assertIn("reject-untrusted:", text)
            self.assertIn("Refusing to run the self-hosted collector", text)
            # The reject job must stay on a GitHub-hosted runner.
            self.assertIn("runs-on: ubuntu-latest", text)
            # setup-python hardcodes /Users/runner/hostedtoolcache on macOS
            # (v5-v7), so the self-hosted job provisions CPython through uv.
            self.assertIn("uv venv --python 3.11", text)
            self.assertNotIn("uses: actions/setup-python", text)

    def test_schema_workflow_stays_on_github_hosted_runner(self):
        text = (WORKFLOWS / "apply-schema.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-latest", text)
        self.assertNotIn("self-hosted", text)

    def test_workflows_request_only_read_permission(self):
        for name in ("collect-prices.yml", "collect-dividends.yml", "apply-schema.yml"):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn("permissions:", text)
            self.assertIn("contents: read", text)

    def test_collectors_use_org_level_shared_configuration(self):
        for name in ("collect-prices.yml", "collect-dividends.yml"):
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            self.assertIn(
                "CLOUDFLARE_ACCOUNT_ID: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}", text
            )
            # Organization variable is D1_DATABASE_ID, not CLOUDFLARE_D1_DATABASE_ID.
            self.assertIn("CLOUDFLARE_D1_DATABASE_ID: ${{ vars.D1_DATABASE_ID }}", text)
            self.assertNotIn(
                "CLOUDFLARE_D1_DATABASE_ID: ${{ vars.CLOUDFLARE_D1_DATABASE_ID }}", text
            )
            self.assertIn(
                "CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", text
            )
            # The shared database uses the project prefix YL by default.
            self.assertIn('TABLE_PREFIX: "YL"', text)
            self.assertNotIn("vars.TABLE_PREFIX", text)

    def test_apply_schema_workflow_is_manual_idempotent_and_never_seeds(self):
        text = (WORKFLOWS / "apply-schema.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn('default: "YL"', text)
        self.assertIn("vars.CLOUDFLARE_ACCOUNT_ID", text)
        self.assertIn("vars.D1_DATABASE_ID", text)
        self.assertIn("secrets.CLOUDFLARE_API_TOKEN", text)
        self.assertIn("tools/apply_schema.py", text)
        self.assertIn("dry_run", text)
        # Production schema application must never load sample data.
        self.assertNotIn("db:seed", text)
        self.assertNotIn("--file", text)


if __name__ == "__main__":
    unittest.main()
