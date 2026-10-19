"""Thin wrapper around the Cloudflare D1 REST API.

The Python collector runs outside Cloudflare (GitHub Actions), so it can't
use a D1 binding directly — it talks to D1 over HTTP instead:
https://developers.cloudflare.com/api/operations/cloudflare-d1-query-database

Each call is a single SQL statement with positional ``?`` parameters,
mirroring the same binding style used in worker/src/db/queries/*.ts.

Two hard Cloudflare limits shape this module:

* **100 bound parameters per query.** ``upsert_rows()`` derives the row count
  per statement from the number of columns, and ``update_rows()`` derives it
  from the CASE expression it builds. Both assert the limit before sending.
* **Request body size.** Multi-value INSERTs are kept small; the parameter
  limit is always the binding constraint for our tables.

Values are validated before serialisation: ``requests`` uses
``json.dumps(..., allow_nan=False)`` and would otherwise raise an opaque
``InvalidJSONError`` inside ``prepare_body()``.
"""

from __future__ import annotations

import math
import os
import random
import re
import time
from typing import Any, Iterable

import requests

# Cloudflare D1 hard limit: https://developers.cloudflare.com/d1/platform/limits/
MAX_BOUND_PARAMS = 100

# Kept for backwards compatibility with callers that passed a batch size.
DEFAULT_BATCH_SIZE = 150

_MAX_RETRIES = 4
_RETRY_BACKOFF_SECONDS = 1.0
_MAX_RETRY_DELAY_SECONDS = 20.0

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class D1Error(RuntimeError):
    pass


class D1ParamError(D1Error):
    """A bound parameter (or parameter count) would be rejected by D1."""


def _validate_identifier(name: str, *, what: str = "identifier") -> str:
    if not _IDENTIFIER_RE.match(name):
        raise D1ParamError(f"Unsafe SQL {what}: {name!r}")
    return name


def _validate_params(params: Iterable[Any], *, sql: str) -> list[Any]:
    values = list(params)
    if len(values) > MAX_BOUND_PARAMS:
        raise D1ParamError(
            f"Query has {len(values)} bound parameters, exceeding D1's limit of "
            f"{MAX_BOUND_PARAMS}. SQL: {sql[:200]}"
        )
    for index, value in enumerate(values):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise D1ParamError(
                f"Non-finite numeric bound parameter at index {index} ({value!r}); "
                f"clean NaN/Inf before calling D1. SQL: {sql[:200]}"
            )
    return values


class D1Client:
    def __init__(
        self,
        account_id: str | None = None,
        database_id: str | None = None,
        api_token: str | None = None,
    ):
        self.account_id = account_id or os.environ["CLOUDFLARE_ACCOUNT_ID"]
        self.database_id = database_id or os.environ["CLOUDFLARE_D1_DATABASE_ID"]
        self.api_token = api_token or os.environ["CLOUDFLARE_API_TOKEN"]
        self._base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
            f"/d1/database/{self.database_id}/query"
        )
        self._session = requests.Session()

    def execute_sql(self, sql: str, params: Iterable[Any] | None = None) -> list[dict]:
        """Run a single SQL statement, returning its result rows (if any)."""
        values = _validate_params(params or [], sql=sql)
        payload = {"sql": sql, "params": values}
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._session.post(
                    self._base_url,
                    headers={
                        "Authorization": f"Bearer {self.api_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=30,
                )
            except requests.RequestException as exc:
                last_error = exc
                self._sleep_before_retry(attempt, f"network error: {exc}")
                continue

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_error = D1Error(f"D1 transient error ({resp.status_code}): {resp.text}")
                self._sleep_before_retry(attempt, str(last_error))
                continue

            if not resp.ok:
                raise D1Error(f"D1 query failed ({resp.status_code}): {resp.text}\nSQL: {sql[:300]}")

            body = resp.json()
            if not body.get("success"):
                raise D1Error(f"D1 query returned success=false: {body}\nSQL: {sql[:300]}")

            results = body.get("result") or []
            rows: list[dict] = []
            for r in results:
                rows.extend(r.get("results") or [])
            return rows

        raise D1Error(f"D1 query failed after {_MAX_RETRIES} retries: {last_error}")

    @staticmethod
    def _sleep_before_retry(attempt: int, reason: str) -> None:
        if attempt >= _MAX_RETRIES:
            return
        delay = min(_MAX_RETRY_DELAY_SECONDS, _RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
        delay += random.uniform(0, 0.3)
        time.sleep(delay)

    def upsert_rows(
        self,
        table: str,
        rows: list[dict[str, Any]],
        batch_size: int | None = None,
    ) -> int:
        """Bulk ``INSERT OR REPLACE`` a list of dict rows (same keys per row).

        The effective batch size is ``min(batch_size, MAX_BOUND_PARAMS //
        len(columns))`` — never more than D1 accepts. Returns the number of
        rows written.
        """
        if not rows:
            return 0

        table = _validate_identifier(table, what="table name")
        columns = list(rows[0].keys())
        if not columns:
            raise D1ParamError("upsert_rows() needs at least one column")
        for column in columns:
            _validate_identifier(column, what="column name")
        if len(columns) > MAX_BOUND_PARAMS:
            raise D1ParamError(
                f"Table {table} has {len(columns)} columns; a single row already exceeds "
                f"D1's {MAX_BOUND_PARAMS}-parameter limit"
            )

        max_rows_per_statement = max(1, MAX_BOUND_PARAMS // len(columns))
        requested = batch_size if batch_size is not None else max_rows_per_statement
        effective_batch = max(1, min(requested, max_rows_per_statement))

        col_list = ", ".join(columns)
        placeholders_one_row = "(" + ", ".join("?" for _ in columns) + ")"

        written = 0
        for start in range(0, len(rows), effective_batch):
            batch = rows[start : start + effective_batch]
            values_sql = ", ".join(placeholders_one_row for _ in batch)
            sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES {values_sql}"
            params: list[Any] = []
            for offset, row in enumerate(batch):
                missing = [c for c in columns if c not in row]
                if missing:
                    raise D1ParamError(
                        f"Row {start + offset} is missing column(s) {missing} for table {table}"
                    )
                params.extend(row[c] for c in columns)
            assert len(params) <= MAX_BOUND_PARAMS, (
                f"upsert_rows() built {len(params)} parameters for {table}; "
                f"D1 allows {MAX_BOUND_PARAMS}"
            )
            self.execute_sql(sql, params)
            written += len(batch)

        return written

    def update_rows(
        self,
        table: str,
        key_column: str,
        rows: list[dict[str, Any]],
        columns: list[str],
        static_values: dict[str, Any] | None = None,
        batch_size: int | None = None,
    ) -> int:
        """Update several rows in as few statements as D1's limit allows.

        Each value column becomes a ``CASE key WHEN ? THEN ? ... END`` clause,
        so one statement updates up to ``batch_size`` rows instead of issuing
        one HTTP request per row (the old N+1 pattern). ``static_values`` are
        applied to every row (e.g. ``updated_at``).
        """
        if not rows:
            return 0

        table = _validate_identifier(table, what="table name")
        key_column = _validate_identifier(key_column, what="key column")
        if not columns:
            raise D1ParamError("update_rows() needs at least one value column")
        for column in columns:
            _validate_identifier(column, what="column name")
        static = dict(static_values or {})
        for column in static:
            _validate_identifier(column, what="column name")

        params_per_row = 2 * len(columns) + 1  # CASE key+value per column, plus IN (key)
        budget = MAX_BOUND_PARAMS - len(static)
        max_rows_per_statement = max(1, budget // params_per_row)
        requested = batch_size if batch_size is not None else max_rows_per_statement
        effective_batch = max(1, min(requested, max_rows_per_statement))

        written = 0
        for start in range(0, len(rows), effective_batch):
            batch = rows[start : start + effective_batch]
            for offset, row in enumerate(batch):
                if key_column not in row:
                    raise D1ParamError(f"Row {start + offset} is missing key column {key_column!r}")
                missing = [c for c in columns if c not in row]
                if missing:
                    raise D1ParamError(
                        f"Row {start + offset} is missing column(s) {missing} for table {table}"
                    )

            set_parts: list[str] = []
            params: list[Any] = []
            for column in columns:
                case_sql = f"{column} = CASE {key_column} " + " ".join(
                    "WHEN ? THEN ?" for _ in batch
                ) + f" ELSE {column} END"
                set_parts.append(case_sql)
                for row in batch:
                    params.extend([row[key_column], row[column]])
            for column, value in static.items():
                set_parts.append(f"{column} = ?")
                params.append(value)
            placeholders = ", ".join("?" for _ in batch)
            sql = (
                f"UPDATE {table} SET {', '.join(set_parts)} "
                f"WHERE {key_column} IN ({placeholders})"
            )
            params.extend(row[key_column] for row in batch)
            assert len(params) <= MAX_BOUND_PARAMS, (
                f"update_rows() built {len(params)} parameters for {table}; "
                f"D1 allows {MAX_BOUND_PARAMS}"
            )
            self.execute_sql(sql, params)
            written += len(batch)

        return written
