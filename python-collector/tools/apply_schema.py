#!/usr/bin/env python3
"""Apply the pending ledger-backed migrations to the shared D1 database.

``worker/src/db/schema.sql`` is no longer re-applied wholesale on every run:
it is migration ``schema_v1`` in the manifest of ``common/migrations.py``, and
each applied migration is recorded in this prefix's ledger table (production
``YL_`` -> ``_YL_schema_migrations``). This script is the thin production entry
point used by ``.github/workflows/apply-schema.yml``:

* expands the ``{prefix}`` template with the shared normalisation rule
  (``YL`` and ``YL_`` both become ``YL_``);
* creates the ledger if needed, then applies the unrecorded migrations in
  manifest order, one SQL statement per request through the D1 HTTP API
  (reusing the authentication/retry/error handling in ``common/d1_client.py``);
* prints a result line per statement and **stops at the first failure** — a
  migration is only recorded after all of its statements succeeded and the
  shape check passed, so the ledger never claims a half-applied migration;
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
from common.migrations import (  # noqa: E402
    MIGRATIONS,
    MigrationRun,
    dry_run_statements,
    ledger_table_name,
    run_migrations,
    summarize_statement,
)
from common.table_prefix import normalize_table_prefix  # noqa: E402

DEFAULT_PREFIX = "YL"
SCHEMA_V1_PATH = MIGRATIONS[0].path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the pending ledger-backed migrations (manifest: "
            "common/migrations.py) to the shared D1 database."
        )
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help=f"Table prefix (default: $TABLE_PREFIX or {DEFAULT_PREFIX})",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        help=(
            "Override the file used for the schema_v1 migration (default: the "
            f"manifest entry {SCHEMA_V1_PATH}); other migrations still come from the manifest"
        ),
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


def print_dry_run(prefix: str, schema_override: Path | None) -> int:
    rendered = dry_run_statements(prefix, schema_override=schema_override)
    for name, path, statements in rendered:
        print(f"migration {name}: {path} ({len(statements)} statement(s))")
        total = len(statements)
        for position, statement in enumerate(statements, start=1):
            print(f"[{position:02d}/{total:02d}] DRY  {summarize_statement(statement)}")
    print(
        "dry run: ledger state unknown — these are all migrations in the manifest, "
        "not the set that would actually be applied"
    )
    print("dry run: no statements were sent to D1", flush=True)
    return 0


def print_summary(outcome: MigrationRun, ledger: str) -> None:
    def join(names: tuple[str, ...]) -> str:
        return ", ".join(names) if names else "(none)"

    print("summary:", flush=True)
    print(f"  applied : {join(outcome.applied)}")
    print(f"  skipped : {join(outcome.skipped)}")
    if outcome.stamped_existing_install:
        print(
            f"  stamped : {join(outcome.stamped_existing_install)} "
            "(existing installation, first ledger stamp)"
        )
    if outcome.failed:
        position = f" (statement {outcome.failed_statement})" if outcome.failed_statement else ""
        print(f"  failed  : {outcome.failed}{position}")
        if outcome.missing_tables:
            print(f"  missing : {', '.join(outcome.missing_tables)}")
        if outcome.pending:
            print(f"  pending : {join(outcome.pending)} (not attempted)")


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

    schema_override: Path | None = None
    if args.schema is not None:
        schema_override = args.schema.expanduser().resolve()
        if not schema_override.is_file():
            print(f"error: schema not found: {schema_override}", file=sys.stderr)
            return 2

    ledger = ledger_table_name(prefix)
    print(f"prefix : {prefix!r}")
    print(f"ledger : {ledger}", flush=True)

    if args.dry_run:
        try:
            return print_dry_run(prefix, schema_override)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        client = D1Client(account_id=args.account_id, database_id=args.database_id)
    except KeyError as exc:
        print(
            f"error: missing environment variable {exc.args[0]}; "
            "set CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_D1_DATABASE_ID / CLOUDFLARE_API_TOKEN",
            file=sys.stderr,
        )
        return 2

    try:
        outcome = run_migrations(client, prefix, schema_override=schema_override)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - a failed ledger read/write must not dump a traceback
        print(
            f"FAILED: could not run migrations against {ledger}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print_summary(outcome, ledger)
    if not outcome.ok:
        print(
            f"FAILED: migration {outcome.failed} did not complete; "
            f"{ledger} was not updated for it",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(
        f"OK: {len(outcome.applied)} migration(s) applied, "
        f"{len(outcome.skipped)} already recorded ({ledger})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
