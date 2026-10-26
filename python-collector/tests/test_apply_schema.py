"""Tests for the ``tools/apply_schema.py`` CLI shell.

The runner itself (ledger, manifest, stop-on-first-failure, shape check) lives
in ``common/migrations.py`` and is covered by ``test_migrations.py``; this file
only covers the argument handling / summary output / exit codes of the script
that production and the workflow actually invoke.
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fake_d1 import (  # noqa: E402 - shared stand-ins, needs the path above
    ledger_names,
    new_client,
    schema_without,
)
from tools import apply_schema  # noqa: E402


class TestDryRun(unittest.TestCase):
    def test_dry_run_needs_no_credentials_and_sends_nothing(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), patch.object(
            apply_schema, "D1Client", side_effect=AssertionError("D1 must not be contacted")
        ):
            code = apply_schema.main(["--prefix", "YL", "--dry-run"])

        out = stdout.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("prefix : 'YL_'", out)
        self.assertIn("ledger : _YL_schema_migrations", out)
        self.assertIn("migration schema_v1:", out)
        self.assertIn("CREATE TABLE", out)
        self.assertIn("[13/13] DRY", out)
        self.assertIn("ledger state unknown", out)
        self.assertIn("dry run: no statements were sent to D1", out)

    def test_blank_prefix_falls_back_to_yl(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = apply_schema.main(["--prefix", "   ", "--dry-run"])
        self.assertEqual(code, 0)
        out = stdout.getvalue()
        self.assertIn("prefix : 'YL_'", out)
        self.assertIn("YL_stocks", out)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS stocks", out)

    def test_invalid_prefix_is_rejected(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = apply_schema.main(["--prefix", "123", "--dry-run"])
        self.assertEqual(code, 2)
        self.assertIn("Invalid TABLE_PREFIX", stderr.getvalue())

    def test_missing_schema_override_is_rejected(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = apply_schema.main(["--prefix", "YL", "--schema", "/nope/missing.sql", "--dry-run"])
        self.assertEqual(code, 2)
        self.assertIn("schema not found", stderr.getvalue())


class TestApply(unittest.TestCase):
    def _run_main(self, connection, client, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(
            apply_schema, "D1Client", lambda **kwargs: client
        ):
            code = apply_schema.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_first_run_applies_then_second_run_skips(self):
        connection, client = new_client()
        code, out, _ = self._run_main(connection, client, ["--prefix", "YL"])

        self.assertEqual(code, 0)
        self.assertIn("apply schema_v1: recorded in _YL_schema_migrations", out)
        self.assertIn("applied : schema_v1", out)
        self.assertIn("OK: 1 migration(s) applied", out)
        self.assertEqual(ledger_names(connection, "YL_"), ["schema_v1"])

        client.reset_calls()
        code, out, _ = self._run_main(connection, client, ["--prefix", "YL"])

        self.assertEqual(code, 0)
        self.assertIn("skip schema_v1: already recorded in _YL_schema_migrations", out)
        self.assertIn("skipped : schema_v1", out)
        self.assertIn("OK: 0 migration(s) applied, 1 already recorded", out)

    def test_failure_exits_non_zero_and_names_the_migration(self):
        connection, client = new_client()
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.sql"
            broken.write_text(
                "CREATE TABLE IF NOT EXISTS {prefix}stocks (code TEXT PRIMARY KEY);\n"
                "CREATE INDEX IF NOT EXISTS idx_nope ON {prefix}missing_table (code);\n",
                encoding="utf-8",
            )
            code, out, err = self._run_main(
                connection, client, ["--prefix", "YL", "--schema", str(broken)]
            )

        self.assertEqual(code, 1)
        self.assertIn("FAILED: migration schema_v1 did not complete", err)
        self.assertIn("stopped at schema_v1 statement 2/2", out)
        self.assertIn("failed  : schema_v1 (statement 2)", out)
        self.assertEqual(ledger_names(connection, "YL_"), [])

    def test_schema_override_replaces_only_schema_v1(self):
        connection, client = new_client()
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "schema_missing_settings.sql"
            override.write_text(schema_without("system_settings"), encoding="utf-8")
            code, out, err = self._run_main(
                connection, client, ["--prefix", "YL", "--schema", str(override)]
            )

        self.assertEqual(code, 1)
        self.assertIn("missing table(s): YL_system_settings", out)
        self.assertIn("FAILED: migration schema_v1 did not complete", err)
        self.assertEqual(ledger_names(connection, "YL_"), [])


if __name__ == "__main__":
    unittest.main()
