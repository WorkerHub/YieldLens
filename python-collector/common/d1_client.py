"""Thin wrapper around the Cloudflare D1 REST API.

The Python collector runs outside Cloudflare (GitHub Actions), so it can't
use a D1 binding directly — it talks to D1 over HTTP instead:
https://developers.cloudflare.com/api/operations/cloudflare-d1-query-database

Each call is a single SQL statement with positional ``?`` parameters,
mirroring the same binding style used in worker/src/db/queries/*.ts.
"""

from __future__ import annotations

import os
import time
from typing import Any, Iterable

import requests

# D1's REST API query endpoint accepts request bodies up to 1MB. Multi-value
# INSERT statements are batched well under that limit to leave headroom for
# large text columns (dividend descriptions etc).
DEFAULT_BATCH_SIZE = 150

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 2.0


class D1Error(RuntimeError):
    pass


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

    def execute_sql(self, sql: str, params: Iterable[Any] | None = None) -> list[dict]:
        """Run a single SQL statement, returning its result rows (if any)."""
        payload = {"sql": sql, "params": list(params) if params else []}
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(
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
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue

            if resp.status_code == 429:
                last_error = D1Error(f"D1 rate limited (attempt {attempt}): {resp.text}")
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
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

    def upsert_rows(
        self,
        table: str,
        rows: list[dict[str, Any]],
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> int:
        """Bulk ``INSERT OR REPLACE`` a list of dict rows (same keys per row).

        Returns the number of rows written. Rows are split into batches of
        ``batch_size`` to stay under D1's request body size limit.
        """
        if not rows:
            return 0

        columns = list(rows[0].keys())
        col_list = ", ".join(columns)
        placeholders_one_row = "(" + ", ".join("?" for _ in columns) + ")"

        written = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            values_sql = ", ".join(placeholders_one_row for _ in batch)
            sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES {values_sql}"
            params: list[Any] = []
            for row in batch:
                params.extend(row[c] for c in columns)
            self.execute_sql(sql, params)
            written += len(batch)

        return written
