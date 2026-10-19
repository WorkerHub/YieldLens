import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.table_prefix import normalize_table_prefix
from tools import apply_schema

REPO_DIR = Path(__file__).resolve().parents[2]
SCHEMA = REPO_DIR / "worker" / "src" / "db" / "schema.sql"


class TestSplitSqlStatements(unittest.TestCase):
    def test_splits_on_semicolons_and_drops_comments(self):
        sql = """
        -- leading comment; with a semicolon
        CREATE TABLE t (a TEXT DEFAULT ';');
        CREATE INDEX i ON t(a); -- trailing comment
        """
        statements = apply_schema.split_sql_statements(sql)
        self.assertEqual(len(statements), 2)
        self.assertIn("';'", statements[0])
        self.assertTrue(statements[1].startswith("CREATE INDEX"))
        self.assertNotIn("--", " ".join(statements))

    def test_keeps_trailing_statement_without_semicolon(self):
        self.assertEqual(apply_schema.split_sql_statements("SELECT 1"), ["SELECT 1"])


class TestRenderSchema(unittest.TestCase):
    def test_yl_prefix_produces_expected_tables(self):
        prefix = normalize_table_prefix("YL")
        self.assertEqual(prefix, "YL_")
        statements = apply_schema.render_schema(SCHEMA.read_text(encoding="utf-8"), prefix)
        sql = "\n".join(statements)

        self.assertTrue(statements)
        self.assertTrue(all(statement.startswith("CREATE ") for statement in statements))
        self.assertNotIn("{prefix}", sql)
        self.assertNotIn("INSERT INTO", sql)  # seed data is never applied here
        for table in (
            "YL_stocks",
            "YL_stock_prices",
            "YL_dividends",
            "YL_dividend_yields",
            "YL_system_settings",
        ):
            self.assertIn(table, sql)

    def test_invalid_prefix_is_rejected(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = apply_schema.main(["--prefix", "123", "--dry-run"])
        self.assertEqual(code, 2)
        self.assertIn("Invalid TABLE_PREFIX", stderr.getvalue())


class TestApplyStatements(unittest.TestCase):
    def test_dry_run_does_not_require_credentials(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = apply_schema.main(["--prefix", "YL", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("dry run: no statements were sent to D1", stdout.getvalue())
        self.assertIn("CREATE TABLE", stdout.getvalue())

    def test_blank_prefix_falls_back_to_yl(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = apply_schema.main(["--prefix", "   ", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("prefix : 'YL_'", stdout.getvalue())
        self.assertNotIn("CREATE TABLE IF NOT EXISTS stocks", stdout.getvalue())

    def test_reports_each_result_and_keeps_going(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def execute_sql(self, sql, params=None):
                self.calls.append(sql)
                if len(self.calls) == 2:
                    raise RuntimeError("boom")
                return [{"ok": 1}]

        client = FakeClient()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            failures = apply_schema.apply_statements(
                client,
                ["CREATE TABLE a (x TEXT)", "CREATE INDEX i ON a(x)", "CREATE TABLE b (y TEXT)"],
            )

        self.assertEqual(failures, 1)
        self.assertEqual(len(client.calls), 3)
        out = stdout.getvalue()
        self.assertIn("OK", out)
        self.assertIn("FAIL", out)
        self.assertIn("boom", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
