"""Ledger-based schema migrations for the shared D1 database.

`schema.sql` used to be re-applied in full on every manual run, so nothing
recorded *which* version of the schema a database was actually at. This module
replaces that with a ``schema_migrations`` ledger, byte-for-byte the same shape
the rest of the fleet uses:

    CREATE TABLE IF NOT EXISTS <ledger> (
      name TEXT PRIMARY KEY,
      applied_at INTEGER NOT NULL DEFAULT (unixepoch())
    )

The ledger table is owned per table prefix, so the shared D1 database can carry
several projects' ledgers side by side without interference:

* ``YL_`` -> ``_YL_schema_migrations``      (production)
* ``AB_`` -> ``_AB_schema_migrations``
* ``""``  -> ``_schema_migrations``

``ledger_table_name()`` is the **only** place that formula lives: the runner
(``tools/apply_schema.py``) and the tests all go through it. Never spell the
ledger name out anywhere else.

## The migration manifest (single source of truth)

``MIGRATIONS`` below is an explicit, ordered list of ``{name, path}`` entries.
Order is written down in code on purpose — never derive it by scanning a
directory and sorting file names, because a rename would silently reorder
history.

``schema_v1`` is the historical collapse: its content *is* the existing
``worker/src/db/schema.sql`` file, unchanged, which stays where it is.

## Rules for appending migrations

1. **Merged migrations are frozen.** Never rename, edit or reorder a migration
   that has already been recorded in a ledger — databases that ran it will not
   run it again, so the edit would never reach them.
2. **Migrations must be additive.** Use ``CREATE ... IF NOT EXISTS``,
   ``ALTER TABLE ... ADD COLUMN``, ``INSERT OR IGNORE``. No ``DROP``, no
   ``RENAME``, no data rewrites — destructive changes need their own issue and
   their own migration strategy.
3. **A new migration** = a new SQL file (e.g.
   ``worker/src/db/migrations/schema_v2.sql``) plus one entry appended to the
   end of ``MIGRATIONS``.
4. **SQL files** keep the ``{prefix}`` placeholder and ``--`` line comments;
   the renderer/cutter in this module already handles both.

## Runner semantics

``run_migrations()`` creates the ledger, reads the recorded names, then walks
the manifest in order. Each unrecorded migration is rendered, split into
statements and executed one statement per request. Only when *every* statement
succeeded *and* the shape check passed is the migration recorded in the ledger.
The first failing statement stops everything: that migration is not recorded,
its remaining statements are skipped, later migrations are not attempted, and
the caller exits non-zero. That is deliberately stricter than the old
"keep going, summarise at the end" behaviour — the ledger must never claim a
migration completed when it did not.

Existing installations (business tables present, no ledger yet) need no
special path: v1 is unrecorded, so its ``IF NOT EXISTS`` DDL simply no-ops,
the shape check passes and the migration gets stamped. Only two kinds of writes
happen in that flow — the idempotent ``CREATE`` statements and the ledger
``INSERT``; no business table is ever inserted into, updated, dropped or
altered (``worker/seed.sql`` is never applied here).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

COLLECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = COLLECTOR_DIR.parent

LEDGER_SUFFIX = "schema_migrations"

#: Business tables every migration run must leave in place (prefix applied).
BUSINESS_TABLES: tuple[str, ...] = (
    "stocks",
    "stock_prices",
    "dividends",
    "dividend_yields",
    "system_settings",
)

#: ``--schema`` on apply_schema.py only ever replaces this migration's file.
SCHEMA_OVERRIDE_TARGET = "schema_v1"

EXISTING_INSTALL_NOTICE = "existing installation — first ledger stamp (存量库首次打戳；DDL 均为 IF NOT EXISTS 空转)"


@dataclass(frozen=True)
class Migration:
    """One append-only migration: a stable name plus its SQL file."""

    name: str
    path: str


#: Ordered manifest — the single source of truth for what runs, and in what order.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(name="schema_v1", path="worker/src/db/schema.sql"),
)


class SqlClient(Protocol):
    """The slice of ``common.d1_client.D1Client`` this module needs."""

    def execute_sql(self, sql: str, params: Iterable[Any] | None = None) -> list[dict]:
        ...


@dataclass(frozen=True)
class MigrationRun:
    """What a ``run_migrations()`` call did, for the caller's summary output."""

    applied: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    stamped_existing_install: tuple[str, ...] = ()
    failed: str | None = None
    failed_statement: int | None = None
    error: str | None = None
    missing_tables: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.failed is None and not self.missing_tables


def ledger_table_name(prefix: str) -> str:
    """Return the ledger table name for an already-normalised prefix.

    ``YL_`` -> ``_YL_schema_migrations``, ``AB_`` -> ``_AB_schema_migrations``,
    ``""`` -> ``_schema_migrations``. Case is preserved and the trailing
    underscore is dropped before the suffix is added.
    """
    core = prefix[:-1] if prefix.endswith("_") else prefix
    return f"_{core}_{LEDGER_SUFFIX}" if core else f"_{LEDGER_SUFFIX}"


def ledger_create_sql(ledger: str) -> str:
    """Ledger DDL — identical to the other projects in the fleet, verbatim."""
    return (
        f"CREATE TABLE IF NOT EXISTS {ledger} "
        "(name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL DEFAULT (unixepoch()))"
    )


def split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script on semicolons outside strings/comments.

    The schema only uses ``--`` line comments, but tracking quotes keeps the
    splitter safe if a future statement contains a semicolon in a literal.
    """
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    in_single = False
    in_double = False
    length = len(sql)

    while index < length:
        char = sql[index]
        following = sql[index + 1] if index + 1 < length else ""

        if not in_single and not in_double and char == "-" and following == "-":
            # Drop the comment but keep the newline so tokens stay separated.
            while index < length and sql[index] != "\n":
                index += 1
            continue

        if char == "'" and not in_double:
            if in_single and following == "'":
                buffer.append("''")
                index += 2
                continue
            in_single = not in_single
        elif char == '"' and not in_single:
            if in_double and following == '"':
                buffer.append('""')
                index += 2
                continue
            in_double = not in_double
        elif char == ";" and not in_single and not in_double:
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    return statements


def render_migration_sql(template: str, prefix: str) -> list[str]:
    """Expand ``{prefix}`` in a migration file and cut it into statements."""
    sql = template.replace("{prefix}", prefix)
    if "{prefix}" in sql:
        raise ValueError("schema still contains an unresolved {prefix} placeholder")
    return split_sql_statements(sql)


def summarize_statement(statement: str, limit: int = 140) -> str:
    flattened = " ".join(statement.split())
    if len(flattened) <= limit:
        return flattened
    return f"{flattened[: limit - 1]}…"


def migration_path(
    migration: Migration,
    *,
    repo_dir: Path = REPO_DIR,
    schema_override: Path | None = None,
) -> Path:
    """Resolve a migration's SQL file, honouring the ``--schema`` override."""
    if schema_override is not None and migration.name == SCHEMA_OVERRIDE_TARGET:
        return Path(schema_override)
    return repo_dir / migration.path


def _table_names(client: SqlClient) -> set[str]:
    rows = client.execute_sql("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row["name"] for row in rows}


def read_applied_migrations(client: SqlClient, ledger: str) -> set[str]:
    """Names already recorded in this prefix's ledger."""
    rows = client.execute_sql(f"SELECT name FROM {ledger}")
    return {row["name"] for row in rows}


def record_migration(client: SqlClient, ledger: str, name: str) -> None:
    """Record a fully applied migration. Nothing writes to the ledger but this."""
    client.execute_sql(f"INSERT INTO {ledger} (name) VALUES (?)", [name])


def verify_shape(client: SqlClient, prefix: str) -> list[str]:
    """Return the business tables missing under ``prefix`` (empty means OK)."""
    tables = _table_names(client)
    return [f"{prefix}{name}" for name in BUSINESS_TABLES if f"{prefix}{name}" not in tables]


def run_migrations(
    client: SqlClient,
    prefix: str,
    *,
    migrations: Sequence[Migration] = MIGRATIONS,
    repo_dir: Path = REPO_DIR,
    schema_override: Path | None = None,
    write=print,
) -> MigrationRun:
    """Apply every unrecorded migration, in manifest order, stamping the ledger.

    ``write`` receives the human-readable progress lines (stdout by default);
    failures are also written to stderr.
    """
    ledger = ledger_table_name(prefix)

    # Peek before creating the ledger so the "existing installation, first
    # stamp" case can be reported to whoever ran the workflow.
    existing_tables = _table_names(client)
    existing_install = ledger not in existing_tables and all(
        f"{prefix}{name}" in existing_tables for name in BUSINESS_TABLES
    )

    client.execute_sql(ledger_create_sql(ledger))
    applied_names = read_applied_migrations(client, ledger)

    applied: list[str] = []
    skipped: list[str] = []
    stamped: list[str] = []

    for index, migration in enumerate(migrations):
        pending = tuple(item.name for item in migrations[index + 1 :])

        if migration.name in applied_names:
            skipped.append(migration.name)
            write(f"skip {migration.name}: already recorded in {ledger}")
            continue

        path = migration_path(migration, repo_dir=repo_dir, schema_override=schema_override)
        statements = render_migration_sql(path.read_text(encoding="utf-8"), prefix)
        write(f"migration {migration.name}: {path} ({len(statements)} statement(s))")
        if existing_install:
            write(f"NOTE {migration.name}: {EXISTING_INSTALL_NOTICE}")

        total = len(statements)
        for position, statement in enumerate(statements, start=1):
            label = f"[{position:02d}/{total:02d}]"
            summary = summarize_statement(statement)
            try:
                rows = client.execute_sql(statement)
            except Exception as exc:  # noqa: BLE001 - report the real reason
                write(f"{label} FAIL {summary}")
                print(f"         reason: {exc}", file=sys.stderr, flush=True)
                write(
                    f"stopped at {migration.name} statement {position}/{total}: "
                    f"{migration.name} not recorded"
                )
                return MigrationRun(
                    applied=tuple(applied),
                    skipped=tuple(skipped),
                    stamped_existing_install=tuple(stamped),
                    failed=migration.name,
                    failed_statement=position,
                    error=str(exc),
                    pending=pending,
                )
            write(f"{label} OK   {summary} ({len(rows)} row(s))")

        # Shape check runs before the stamp: a half-built database must never be
        # recorded as "v1 done", next run should retry instead.
        missing = verify_shape(client, prefix)
        if missing:
            write(
                f"FAIL {migration.name}: shape check failed, "
                f"missing table(s): {', '.join(missing)}"
            )
            write(f"stopped at {migration.name}: shape check failed, {migration.name} not recorded")
            return MigrationRun(
                applied=tuple(applied),
                skipped=tuple(skipped),
                stamped_existing_install=tuple(stamped),
                failed=migration.name,
                missing_tables=tuple(missing),
                pending=pending,
            )

        record_migration(client, ledger, migration.name)
        write(f"apply {migration.name}: recorded in {ledger}")
        applied.append(migration.name)
        if existing_install:
            stamped.append(migration.name)

    return MigrationRun(
        applied=tuple(applied),
        skipped=tuple(skipped),
        stamped_existing_install=tuple(stamped),
    )


def dry_run_statements(
    prefix: str,
    *,
    migrations: Sequence[Migration] = MIGRATIONS,
    repo_dir: Path = REPO_DIR,
    schema_override: Path | None = None,
) -> list[tuple[str, Path, list[str]]]:
    """Render every migration in the manifest for offline inspection."""
    rendered: list[tuple[str, Path, list[str]]] = []
    for migration in migrations:
        path = migration_path(migration, repo_dir=repo_dir, schema_override=schema_override)
        statements = render_migration_sql(path.read_text(encoding="utf-8"), prefix)
        rendered.append((migration.name, path, statements))
    return rendered
