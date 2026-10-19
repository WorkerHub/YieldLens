#!/usr/bin/env python3
"""Apply ``worker/src/db/schema.sql`` to the shared D1 database.

The script is the production entry point used by
``.github/workflows/apply-schema.yml``. It:

* expands the ``{prefix}`` template with the shared normalisation rule
  (``YL`` and ``YL_`` both become ``YL_``);
* sends one SQL statement per request through the D1 HTTP API, reusing the
  authentication/retry/error handling in ``common/d1_client.py``;
* prints a result line for every statement and keeps going after a failure so
  one bad statement does not hide the rest, then exits non-zero if any failed;
* never touches ``worker/seed.sql`` — production must not contain sample data.

Run locally without credentials to inspect the generated SQL:

    python tools/apply_schema.py --prefix YL --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

COLLECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = COLLECTOR_DIR.parent
sys.path.insert(0, str(COLLECTOR_DIR))

from common.d1_client import D1Client  # noqa: E402
from common.table_prefix import normalize_table_prefix  # noqa: E402

DEFAULT_PREFIX = "YL"
DEFAULT_SCHEMA = REPO_DIR / "worker" / "src" / "db" / "schema.sql"


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


def render_schema(template: str, prefix: str) -> list[str]:
    sql = template.replace("{prefix}", prefix)
    if "{prefix}" in sql:
        raise ValueError("schema still contains an unresolved {prefix} placeholder")
    return split_sql_statements(sql)


def summarize_statement(statement: str, limit: int = 140) -> str:
    flattened = " ".join(statement.split())
    if len(flattened) <= limit:
        return flattened
    return f"{flattened[: limit - 1]}…"


def apply_statements(client: D1Client, statements: list[str]) -> int:
    """Execute statements in order; return the number of failures."""
    failures = 0
    total = len(statements)

    for position, statement in enumerate(statements, start=1):
        label = f"[{position:02d}/{total:02d}]"
        summary = summarize_statement(statement)
        try:
            rows = client.execute_sql(statement)
        except Exception as exc:  # noqa: BLE001 - report every statement
            failures += 1
            print(f"{label} FAIL {summary}", flush=True)
            print(f"         reason: {exc}", file=sys.stderr, flush=True)
        else:
            print(f"{label} OK   {summary} ({len(rows)} row(s))", flush=True)

    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply worker/src/db/schema.sql to the shared D1 database."
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help=f"Table prefix (default: $TABLE_PREFIX or {DEFAULT_PREFIX})",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help=f"Schema template path (default: {DEFAULT_SCHEMA})",
    )
    parser.add_argument(
        "--account-id",
        default=None,
        help="Cloudflare account id (default: $CLOUDFLARE_ACCOUNT_ID)",
    )
    parser.add_argument(
        "--database-id",
        default=None,
        help="D1 database id (default: $CLOUDFLARE_D1_DATABASE_ID)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated statements without calling D1",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    raw_prefix = (args.prefix or os.environ.get("TABLE_PREFIX") or "").strip()
    # Never fall back to an unprefixed schema on the shared database.
    raw_prefix = raw_prefix or DEFAULT_PREFIX
    try:
        prefix = normalize_table_prefix(raw_prefix)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    schema_path = args.schema.expanduser().resolve()
    if not schema_path.is_file():
        print(f"error: schema not found: {schema_path}", file=sys.stderr)
        return 2

    try:
        statements = render_schema(schema_path.read_text(encoding="utf-8"), prefix)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"schema : {schema_path}")
    print(f"prefix : {prefix!r} ({len(statements)} statement(s))", flush=True)

    if args.dry_run:
        for position, statement in enumerate(statements, start=1):
            print(f"[{position:02d}/{len(statements):02d}] DRY  {summarize_statement(statement)}")
        print("dry run: no statements were sent to D1", flush=True)
        return 0

    try:
        client = D1Client(account_id=args.account_id, database_id=args.database_id)
    except KeyError as exc:
        print(
            f"error: missing environment variable {exc.args[0]}; "
            "set CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_D1_DATABASE_ID / CLOUDFLARE_API_TOKEN",
            file=sys.stderr,
        )
        return 2

    failures = apply_statements(client, statements)
    if failures:
        print(
            f"FAILED: {failures} of {len(statements)} statement(s) failed",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(f"OK: applied {len(statements)} statement(s) to the shared D1 database", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
