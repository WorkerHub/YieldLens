"""Shared test doubles for the schema-application tests.

``SqliteD1`` implements the same ``execute_sql`` contract as
``common.d1_client.D1Client`` on top of an in-memory SQLite connection and
records every statement, so tests can assert both the resulting structure and
"no SQL was sent" claims. ``tools/local_e2e.py`` uses the same stand-in recipe.

It is not a test module itself (name does not match ``test*.py``), just the
fixture plumbing shared by ``test_migrations.py`` and ``test_apply_schema.py``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from common.migrations import BUSINESS_TABLES, ledger_table_name, render_migration_sql, split_sql_statements

REPO_DIR = Path(__file__).resolve().parents[2]
SCHEMA = REPO_DIR / "worker" / "src" / "db" / "schema.sql"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

__all__ = [
    "BUSINESS_TABLES",
    "FIXTURES",
    "REPO_DIR",
    "SCHEMA",
    "SqliteD1",
    "apply_legacy_schema",
    "dump_rows",
    "ledger_names",
    "new_client",
    "schema_without",
    "snapshot_structure",
    "table_exists",
]


class SqliteD1:
    """SQLite-backed stand-in for ``D1Client`` that records every call."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.calls: list[tuple[str, list]] = []

    def execute_sql(self, sql: str, params=None):
        values = list(params or [])
        self.calls.append((sql, values))
        cursor = self.connection.execute(sql, values)
        rows = []
        if cursor.description:
            columns = [description[0] for description in cursor.description]
            rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
        # Every D1 call is its own request, so make effects visible immediately.
        self.connection.commit()
        return rows

    @property
    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]

    def reset_calls(self) -> None:
        self.calls.clear()


def new_client() -> tuple[sqlite3.Connection, SqliteD1]:
    connection = sqlite3.connect(":memory:")
    return connection, SqliteD1(connection)


def apply_legacy_schema(connection: sqlite3.Connection, prefix: str) -> None:
    """Create the business tables the way the pre-ledger apply_schema did."""
    for statement in render_migration_sql(SCHEMA.read_text(encoding="utf-8"), prefix):
        connection.execute(statement)
    connection.commit()


def snapshot_structure(connection: sqlite3.Connection) -> dict:
    """``PRAGMA table_info`` + index list for the business tables only.

    The ledger table is deliberately excluded: it is not part of v1's schema.
    """
    tables: dict[str, dict] = {}
    for table in BUSINESS_TABLES:
        columns = [
            {
                "cid": row[0],
                "name": row[1],
                "type": row[2],
                "notnull": row[3],
                "dflt_value": row[4],
                "pk": row[5],
            }
            for row in connection.execute(f"PRAGMA table_info({table})")
        ]
        indexes = []
        for row in connection.execute(f"PRAGMA index_list({table})"):
            index_name = row[1]
            indexes.append(
                {
                    "name": index_name,
                    "unique": row[2],
                    "origin": row[3],
                    "partial": row[4],
                    "columns": [
                        {"seqno": info[0], "cid": info[1], "name": info[2]}
                        for info in connection.execute(f"PRAGMA index_info({index_name})")
                    ],
                    "sql": _index_sql(connection, index_name),
                }
            )
        indexes.sort(key=lambda item: item["name"])
        tables[table] = {"columns": columns, "indexes": indexes}
    return {"tables": tables}


def _index_sql(connection: sqlite3.Connection, index_name: str) -> str | None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (index_name,)
    ).fetchone()
    return row[0] if row else None


def dump_rows(connection: sqlite3.Connection, prefix: str = "") -> dict[str, list[tuple]]:
    """Every business table's rows, in a stable order, for before/after compares."""
    return {
        table: list(
            connection.execute(f"SELECT * FROM {prefix}{table} ORDER BY 1, 2").fetchall()
        )
        for table in BUSINESS_TABLES
    }


def ledger_names(connection: sqlite3.Connection, prefix: str) -> list[str]:
    ledger = ledger_table_name(prefix)
    return [row[0] for row in connection.execute(f"SELECT name FROM {ledger} ORDER BY name")]


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def schema_without(*tables: str) -> str:
    """schema.sql minus the statements touching ``tables`` (missing-table fixture)."""
    template = SCHEMA.read_text(encoding="utf-8")
    kept = [
        statement
        for statement in split_sql_statements(template)
        if not any(f"{{prefix}}{table}" in statement for table in tables)
    ]
    return ";\n".join(kept) + ";\n"
