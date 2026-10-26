import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import migrations
from common.migrations import (
    BUSINESS_TABLES,
    MIGRATIONS,
    Migration,
    ledger_create_sql,
    ledger_table_name,
    render_migration_sql,
    run_migrations,
    split_sql_statements,
    verify_shape,
)
from tests.fake_d1 import (  # noqa: E402 - shared stand-ins, needs the path above
    FIXTURES,
    SCHEMA,
    SqliteD1,
    apply_legacy_schema,
    dump_rows,
    ledger_names,
    new_client,
    schema_without,
    snapshot_structure,
    table_exists,
)

STRUCTURE_FIXTURE = FIXTURES / "schema_v1_structure.json"
PREFIX = "YL_"


class TestLedgerTableName(unittest.TestCase):
    def test_prefix_variants(self):
        self.assertEqual(ledger_table_name("YL_"), "_YL_schema_migrations")
        self.assertEqual(ledger_table_name("AB_"), "_AB_schema_migrations")
        self.assertEqual(ledger_table_name(""), "_schema_migrations")
        # Case is preserved, only the trailing underscore is normalised away.
        self.assertEqual(ledger_table_name("yl"), "_yl_schema_migrations")

    def test_ledger_ddl_matches_fleet_shape(self):
        self.assertEqual(
            ledger_create_sql("_YL_schema_migrations"),
            "CREATE TABLE IF NOT EXISTS _YL_schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL DEFAULT (unixepoch()))",
        )

    def test_manifest_is_the_historical_collapse(self):
        self.assertEqual([(m.name, m.path) for m in MIGRATIONS], [("schema_v1", "worker/src/db/schema.sql")])


class TestSqlStatements(unittest.TestCase):
    def test_splits_on_semicolons_and_drops_comments(self):
        sql = """
        -- leading comment; with a semicolon
        CREATE TABLE t (a TEXT DEFAULT ';');
        CREATE INDEX i ON t(a); -- trailing comment
        """
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 2)
        self.assertIn("';'", statements[0])
        self.assertTrue(statements[1].startswith("CREATE INDEX"))
        self.assertNotIn("--", " ".join(statements))

    def test_keeps_trailing_statement_without_semicolon(self):
        self.assertEqual(split_sql_statements("SELECT 1"), ["SELECT 1"])

    def test_render_expands_prefix_and_never_emits_seed_data(self):
        statements = render_migration_sql(SCHEMA.read_text(encoding="utf-8"), PREFIX)
        sql = "\n".join(statements)

        self.assertNotIn("{prefix}", sql)
        self.assertTrue(all(statement.startswith("CREATE ") for statement in statements))
        self.assertNotIn("INSERT INTO", sql)
        for table in BUSINESS_TABLES:
            self.assertIn(f"{PREFIX}{table}", sql)


class TestStructureParity(unittest.TestCase):
    """§5.3: v1 under the ledger builds exactly what the old script built.

    ``fixtures/schema_v1_structure.json`` was generated on the pre-ledger
    revision of ``tools/apply_schema.py`` (main before this change) applied to
    an empty SQLite database with no prefix; regenerating it here would defeat
    the point of the comparison.
    """

    def test_schema_v1_matches_the_pre_ledger_structure(self):
        expected = json.loads(STRUCTURE_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(sorted(expected["tables"]), sorted(BUSINESS_TABLES))

        connection = sqlite3.connect(":memory:")
        client = SqliteD1(connection)
        outcome = run_migrations(client, "")

        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.applied, ("schema_v1",))
        self.assertEqual(snapshot_structure(connection)["tables"], expected["tables"])


class TestFreshInstall(unittest.TestCase):
    def test_creates_business_tables_plus_ledger(self):
        connection, client = new_client()
        outcome = run_migrations(client, PREFIX)

        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.applied, ("schema_v1",))
        self.assertEqual(outcome.stamped_existing_install, ())  # nothing to stamp yet
        for table in BUSINESS_TABLES:
            self.assertTrue(table_exists(connection, f"{PREFIX}{table}"), table)
        self.assertTrue(table_exists(connection, "_YL_schema_migrations"))
        self.assertEqual(ledger_names(connection, PREFIX), ["schema_v1"])
        self.assertEqual(verify_shape(client, PREFIX), [])

    def test_second_run_skips_and_sends_no_business_ddl(self):
        connection, client = new_client()
        run_migrations(client, PREFIX)
        client.reset_calls()

        outcome = run_migrations(client, PREFIX)

        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.applied, ())
        self.assertEqual(outcome.skipped, ("schema_v1",))
        # The ledger's own IF NOT EXISTS create is always sent (§2.3 step 2);
        # nothing else may reach D1 once the migration is recorded — in
        # particular not a single business-table statement.
        self.assertEqual(
            client.statements,
            [
                "SELECT name FROM sqlite_master WHERE type = 'table'",
                ledger_create_sql("_YL_schema_migrations"),
                "SELECT name FROM _YL_schema_migrations",
            ],
        )


class TestExistingInstallation(unittest.TestCase):
    """§5.4: business tables + data already there, no ledger yet."""

    def _seed_existing_install(self, connection: sqlite3.Connection) -> None:
        apply_legacy_schema(connection, PREFIX)
        connection.execute(
            "INSERT INTO YL_stocks (code, name, created_at, updated_at) VALUES ('600519', '贵州茅台', '2026-10-01', '2026-10-01')"
        )
        connection.execute(
            "INSERT INTO YL_stock_prices (code, trade_date, close) VALUES ('600519', '2026-10-20', 1500.0)"
        )
        connection.execute(
            "INSERT INTO YL_dividends (id, code, report_period, created_at, updated_at) "
            "VALUES ('d1', '600519', '2025-12-31', '2026-10-01', '2026-10-01')"
        )
        connection.execute(
            "INSERT INTO YL_dividend_yields (code, calc_date, price, static_yield) "
            "VALUES ('600519', '2026-10-20', 1500.0, 1.4)"
        )
        connection.execute(
            "INSERT INTO YL_system_settings (key, value, updated_at) "
            "VALUES ('price_backfill_cursor', '000002', '2026-10-20')"
        )
        connection.commit()

    def test_first_ledger_stamp_keeps_structure_and_rows(self):
        connection, client = new_client()
        self._seed_existing_install(connection)
        structure_before = snapshot_structure(connection)
        rows_before = dump_rows(connection, PREFIX)

        outcome = run_migrations(client, PREFIX)

        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.applied, ("schema_v1",))
        self.assertEqual(outcome.stamped_existing_install, ("schema_v1",))
        self.assertEqual(ledger_names(connection, PREFIX), ["schema_v1"])
        self.assertEqual(snapshot_structure(connection), structure_before)
        self.assertEqual(dump_rows(connection, PREFIX), rows_before)

    def test_first_stamp_is_reported_in_the_output(self):
        connection, client = new_client()
        self._seed_existing_install(connection)
        lines: list[str] = []

        outcome = run_migrations(client, PREFIX, write=lines.append)

        self.assertTrue(outcome.ok, outcome)
        notice = [line for line in lines if migrations.EXISTING_INSTALL_NOTICE in line]
        self.assertEqual(len(notice), 1, lines)
        self.assertIn("schema_v1", notice[0])
        self.assertIn("存量库首次打戳", notice[0])

    def test_transition_only_writes_ddl_and_the_ledger_insert(self):
        connection, client = new_client()
        self._seed_existing_install(connection)
        client.reset_calls()

        run_migrations(client, PREFIX)

        ledger_inserts = [sql for sql in client.statements if sql.startswith("INSERT INTO")]
        self.assertEqual(ledger_inserts, ["INSERT INTO _YL_schema_migrations (name) VALUES (?)"])
        forbidden = ("UPDATE ", "DELETE ", "DROP ", "ALTER ", "INSERT OR REPLACE", "INSERT INTO YL_")
        for sql in client.statements:
            for token in forbidden:
                self.assertNotIn(token, sql, f"{token!r} must never run against business tables: {sql}")
            if not sql.startswith("INSERT INTO _YL_schema_migrations"):
                self.assertTrue(
                    sql.startswith(("CREATE TABLE IF NOT EXISTS", "CREATE INDEX IF NOT EXISTS", "SELECT ")),
                    sql,
                )

    def test_missing_business_table_fails_the_shape_check(self):
        connection, client = new_client()
        self._seed_existing_install(connection)
        # A "half-installed" database: the DDL cannot recreate a dropped table
        # because the migration file is overridden with a version lacking it.
        connection.execute("DROP TABLE YL_system_settings")
        connection.commit()
        truncated = schema_without("system_settings")

        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "schema_missing_settings.sql"
            override.write_text(truncated, encoding="utf-8")
            outcome = run_migrations(client, PREFIX, schema_override=override)

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed, "schema_v1")
        self.assertEqual(outcome.missing_tables, ("YL_system_settings",))
        self.assertEqual(ledger_names(connection, PREFIX), [])
        self.assertFalse(table_exists(connection, "YL_system_settings"))


class TestFailureVisibility(unittest.TestCase):
    """§5.5: a failing statement stops the run, is not recorded, and resumes."""

    def _manifest(self, tmp: Path, bad_sql: str) -> tuple[Migration, ...]:
        (tmp / "bad.sql").write_text(bad_sql, encoding="utf-8")
        (tmp / "good.sql").write_text(
            "CREATE TABLE IF NOT EXISTS {prefix}later_table (x TEXT);\n", encoding="utf-8"
        )
        return (
            Migration(name="schema_bad", path="bad.sql"),
            Migration(name="schema_later", path="good.sql"),
        )

    def test_failure_stops_the_run_and_the_rerun_completes(self):
        connection, client = new_client()
        apply_legacy_schema(connection, PREFIX)  # shape check would pass

        bad_sql = (
            "CREATE TABLE IF NOT EXISTS {prefix}tmp_bad (x TEXT);\n"
            "CREATE INDEX IF NOT EXISTS idx_tmp_missing ON {prefix}does_not_exist (code);\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = self._manifest(tmp_path, bad_sql)

            outcome = run_migrations(client, PREFIX, migrations=manifest, repo_dir=tmp_path)

            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.failed, "schema_bad")
            self.assertEqual(outcome.failed_statement, 2)
            self.assertEqual(outcome.pending, ("schema_later",))
            self.assertEqual(ledger_names(connection, PREFIX), [])
            # The failing migration stopped mid-way and the next one never ran.
            self.assertFalse(table_exists(connection, "YL_later_table"))
            self.assertFalse(
                any("YL_later_table" in sql for sql in client.statements),
                "the later migration must not be attempted",
            )

            # Fix the broken statement, rerun: both migrations complete.
            fixed = (
                "CREATE TABLE IF NOT EXISTS {prefix}tmp_bad (x TEXT);\n"
                "CREATE INDEX IF NOT EXISTS idx_tmp_bad ON {prefix}tmp_bad (x);\n"
            )
            (tmp_path / "bad.sql").write_text(fixed, encoding="utf-8")
            retry = run_migrations(client, PREFIX, migrations=manifest, repo_dir=tmp_path)

        self.assertTrue(retry.ok, retry)
        self.assertEqual(retry.applied, ("schema_bad", "schema_later"))
        self.assertEqual(retry.skipped, ())
        self.assertEqual(ledger_names(connection, PREFIX), ["schema_bad", "schema_later"])

    def test_failed_migration_name_reaches_the_output(self):
        connection, client = new_client()
        apply_legacy_schema(connection, PREFIX)
        lines: list[str] = []
        bad_sql = "CREATE INDEX IF NOT EXISTS idx_nope ON {prefix}missing (code);\n"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = self._manifest(tmp_path, bad_sql)
            outcome = run_migrations(
                client, PREFIX, migrations=manifest, repo_dir=tmp_path, write=lines.append
            )

        self.assertFalse(outcome.ok)
        self.assertTrue(any("FAIL" in line for line in lines), lines)
        self.assertTrue(any("schema_bad" in line for line in lines), lines)
        self.assertTrue(any("stopped at schema_bad statement 1/1" in line for line in lines), lines)


class TestPrefixIsolation(unittest.TestCase):
    def test_each_prefix_gets_its_own_ledger_and_tables(self):
        connection, client = new_client()

        for prefix in ("YL_", "AB_"):
            outcome = run_migrations(client, prefix)
            self.assertTrue(outcome.ok, outcome)
            self.assertEqual(outcome.applied, ("schema_v1",))

        self.assertTrue(table_exists(connection, "_YL_schema_migrations"))
        self.assertTrue(table_exists(connection, "_AB_schema_migrations"))
        self.assertTrue(table_exists(connection, "YL_stocks"))
        self.assertTrue(table_exists(connection, "AB_stocks"))
        self.assertEqual(ledger_names(connection, "YL_"), ["schema_v1"])
        self.assertEqual(ledger_names(connection, "AB_"), ["schema_v1"])

        # Running one prefix again must not touch the other prefix's ledger.
        client.reset_calls()
        outcome = run_migrations(client, "YL_")
        self.assertEqual(outcome.skipped, ("schema_v1",))
        self.assertFalse(any("_AB_" in sql for sql in client.statements), client.statements)

    def test_empty_prefix_uses_the_unprefixed_ledger(self):
        connection, client = new_client()
        outcome = run_migrations(client, "")

        self.assertTrue(outcome.ok, outcome)
        self.assertTrue(table_exists(connection, "_schema_migrations"))
        self.assertEqual(
            [row[0] for row in connection.execute("SELECT name FROM _schema_migrations")],
            ["schema_v1"],
        )
        for table in BUSINESS_TABLES:
            self.assertTrue(table_exists(connection, table), table)


if __name__ == "__main__":
    unittest.main()
