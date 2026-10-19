"""Stock master-data sync with upstream fallbacks.

``ak.stock_info_a_code_name()`` (Shenzhen exchange + Shanghai exchange pages)
is the canonical source, but GitHub-hosted runners have been seen getting
``Connection reset by peer`` from it. The collector therefore:

1. retries the primary source with exponential backoff;
2. falls back to ``ak.stock_zh_a_spot_em()`` (Eastmoney full-market snapshot);
3. falls back to the stock list already stored in D1.

Only when all three fail (and the table is empty) does the run abort.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import akshare as ak

from common.d1_client import D1Client
from common.numeric import is_missing
from common.retry import call_with_retries
from common.ticker_utils import infer_exchange, normalize_ticker

PRIMARY_SOURCE = "akshare_stock_info_a_code_name"
FALLBACK_SOURCE = "akshare_stock_zh_a_spot_em"
DATABASE_SOURCE = "database_existing"


@dataclass
class StockListResult:
    rows: list[dict]
    source: str
    errors: list[str] = field(default_factory=list)

    @property
    def used_fallback(self) -> bool:
        return self.source != PRIMARY_SOURCE


def _clean_name(raw: object, code: str) -> str:
    if raw is None or is_missing(raw):
        return code
    name = str(raw).strip()
    return name or code


def _rows_from_info_frame(df) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for _, record in df.iterrows():
        raw_code = record.get("code", record.get("代码"))
        if raw_code is None or is_missing(raw_code):
            continue
        try:
            code = normalize_ticker(str(raw_code).split(".")[0].zfill(6))
        except ValueError:
            continue
        if code in seen:
            continue
        seen.add(code)
        name = _clean_name(record.get("name", record.get("名称")), code)
        rows.append({"code": code, "name": name})
    return rows


def _fetch_primary(logger: logging.Logger) -> list[dict]:
    df = call_with_retries(
        lambda: ak.stock_info_a_code_name(),
        description="ak.stock_info_a_code_name()",
        max_attempts=3,
        logger=logger,
    )
    if df is None or df.empty:
        raise RuntimeError("ak.stock_info_a_code_name() returned no rows")
    return _rows_from_info_frame(df)


def _fetch_snapshot_fallback(logger: logging.Logger) -> list[dict]:
    df = call_with_retries(
        lambda: ak.stock_zh_a_spot_em(),
        description="ak.stock_zh_a_spot_em() (stock-list fallback)",
        max_attempts=3,
        logger=logger,
    )
    if df is None or df.empty:
        raise RuntimeError("ak.stock_zh_a_spot_em() returned no rows")
    return _rows_from_info_frame(df)


def fetch_stock_list(logger: logging.Logger) -> StockListResult:
    """Fetch code/name rows from the primary source, then the snapshot fallback."""
    errors: list[str] = []

    try:
        rows = _fetch_primary(logger)
        if rows:
            return StockListResult(rows=rows, source=PRIMARY_SOURCE)
        errors.append("primary source returned no rows")
    except Exception as exc:  # noqa: BLE001 - fall through to the next source
        errors.append(f"primary source failed: {exc}")
        logger.warning("Stock list primary source failed, trying snapshot fallback: %s", exc)

    try:
        rows = _fetch_snapshot_fallback(logger)
        if rows:
            return StockListResult(rows=rows, source=FALLBACK_SOURCE, errors=errors)
        errors.append("snapshot fallback returned no rows")
    except Exception as exc:  # noqa: BLE001 - caller decides whether DB fallback is enough
        errors.append(f"snapshot fallback failed: {exc}")
        logger.warning("Stock list snapshot fallback failed: %s", exc)

    raise RuntimeError("; ".join(errors))


def _stock_row(code: str, name: str, now: str) -> dict:
    # Only the columns that must be set explicitly. Everything else (industry,
    # prices, yields, counters) has a schema default or is populated by the
    # collectors later; fewer columns also means more rows per D1 statement
    # (7 columns -> 14 rows, instead of 21 columns -> 4 rows).
    return {
        "code": code,
        "name": name,
        "market": "A",
        "exchange": infer_exchange(code),
        "is_st": 1 if "ST" in name.upper() else 0,
        "created_at": now,
        "updated_at": now,
    }


def sync_stock_list(d1: D1Client, prefix: str, logger: logging.Logger) -> StockListResult:
    """Insert new listings and refresh names / ST flags; never wipe existing rows."""
    existing_rows = d1.execute_sql(f"SELECT code, name, is_st FROM {prefix}stocks")
    existing = {r["code"]: r for r in existing_rows}
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        result = fetch_stock_list(logger)
    except Exception as exc:  # noqa: BLE001 - DB fallback keeps the collector alive
        if existing:
            logger.error(
                "Stock list unavailable (%s); falling back to the %d stocks already in D1",
                exc,
                len(existing),
            )
            return StockListResult(
                rows=[{"code": c, "name": r.get("name") or c} for c, r in existing.items()],
                source=DATABASE_SOURCE,
                errors=[str(exc)],
            )
        raise RuntimeError(
            f"Stock list unavailable and {prefix}stocks is empty; cannot bootstrap: {exc}"
        ) from exc

    new_rows = [_stock_row(r["code"], r["name"], now) for r in result.rows if r["code"] not in existing]
    if new_rows:
        written = d1.upsert_rows(f"{prefix}stocks", new_rows)
        logger.info("Added %d newly-listed stocks (source=%s)", written, result.source)
    else:
        logger.info("No new stocks to add (source=%s)", result.source)

    updates = []
    for r in result.rows:
        current = existing.get(r["code"])
        if not current:
            continue
        is_st = 1 if "ST" in r["name"].upper() else 0
        if current.get("name") != r["name"] or current.get("is_st") != is_st:
            updates.append({"code": r["code"], "name": r["name"], "is_st": is_st})
    if updates:
        d1.update_rows(
            f"{prefix}stocks",
            "code",
            updates,
            ["name", "is_st"],
            static_values={"updated_at": now},
        )
        logger.info("Refreshed name/is_st for %d stocks", len(updates))

    if result.errors:
        logger.warning("Stock list used fallback %s; reasons: %s", result.source, "; ".join(result.errors))
    return result
