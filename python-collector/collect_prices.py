#!/usr/bin/env python3
"""OHLCV collector — run frequently during A-share trading hours.

Fetches daily closing prices for every stock in the `stocks` table (bootstrapping
that table from AKShare's official-exchange stock list on first run), and writes
them to `stock_prices`. Updates `stocks.last_price` / `price_updated_at` from the
most recent row per stock.

Two modes, controlled by env vars (see .env.example):
  - Incremental (default): only fetches from each stock's last known trade_date
    forward. Cheap, safe to run every 15-30 minutes during trading hours.
  - Backfill (BACKFILL_MODE=true): fetches ~10 years of history for a slice of
    the stock list (BACKFILL_BATCH selects which 1/50th of the market to run),
    so a full historical backfill can be spread across multiple workflow runs
    without timing out or hammering the upstream data source in one go.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta

import akshare as ak

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.d1_client import D1Client
from common.rate_limiter import sina_limiter
from common.table_prefix import get_table_prefix
from common.ticker_utils import code_to_akshare_daily_prefix, infer_exchange, normalize_ticker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_prices")

BACKFILL_YEARS = 10
BACKFILL_TOTAL_BATCHES = 50
STOCKS_PER_LOG = 50


def bootstrap_stock_list(d1: D1Client, prefix: str) -> None:
    """Populate the stocks table from AKShare's official code/name list if empty."""
    existing = d1.execute_sql(f"SELECT COUNT(*) as n FROM {prefix}stocks")
    if existing and existing[0]["n"] > 0:
        return

    log.info("stocks table is empty, bootstrapping from ak.stock_info_a_code_name()")
    df = ak.stock_info_a_code_name()
    now = datetime.utcnow().isoformat() + "Z"

    rows = []
    for _, r in df.iterrows():
        code = str(r["code"]).zfill(6)
        rows.append(
            {
                "code": code,
                "name": r["name"],
                "market": "A",
                "exchange": infer_exchange(code),
                "industry": "",
                "total_shares": None,
                "float_shares": None,
                "is_st": 0,
                "list_date": None,
                "last_price": None,
                "price_updated_at": None,
                "market_cap": None,
                "pe_ttm": None,
                "pb": None,
                "static_yield": None,
                "ttm_yield": None,
                "yield_updated_at": None,
                "last_dividend_date": None,
                "consecutive_dividend_years": 0,
                "created_at": now,
                "updated_at": now,
            }
        )

    written = d1.upsert_rows(f"{prefix}stocks", rows)
    log.info(f"Bootstrapped {written} stocks")


def get_watermark(d1: D1Client, prefix: str, code: str) -> str | None:
    rows = d1.execute_sql(
        f"SELECT trade_date FROM {prefix}stock_prices WHERE code = ? ORDER BY trade_date DESC LIMIT 1",
        [code],
    )
    return rows[0]["trade_date"] if rows else None


def fetch_ohlcv(code: str, start_date: str, end_date: str):
    sina_limiter.wait()
    symbol = code_to_akshare_daily_prefix(code)
    return ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=end_date)


def collect_one_stock(d1: D1Client, prefix: str, code: str, backfill_mode: bool) -> bool:
    """Returns True on success, False on failure (caller tracks failure rate)."""
    today_str = date.today().strftime("%Y%m%d")

    if backfill_mode:
        start_date = (date.today() - timedelta(days=365 * BACKFILL_YEARS)).strftime("%Y%m%d")
    else:
        watermark = get_watermark(d1, prefix, code)
        if watermark:
            # watermark is stored as YYYY-MM-DD; AKShare wants YYYYMMDD.
            watermark_date = datetime.strptime(watermark, "%Y-%m-%d").date()
            if (date.today() - watermark_date).days < 1:
                return True  # already up to date
            start_date = watermark_date.strftime("%Y%m%d")
        else:
            # No price history at all yet — pull the last year so the detail
            # page has at least some chart data; full history comes from a
            # dedicated backfill run.
            start_date = (date.today() - timedelta(days=365)).strftime("%Y%m%d")

    try:
        df = fetch_ohlcv(code, start_date, today_str)
    except Exception as exc:  # noqa: BLE001 - log and continue, don't abort the batch
        log.warning(f"{code}: OHLCV fetch failed: {exc}")
        return False

    if df is None or df.empty:
        return True  # no new data (e.g. stock suspended), not a failure

    rows = []
    for _, r in df.iterrows():
        rows.append(
            {
                "code": code,
                "trade_date": str(r["date"]),
                "open": float(r["open"]) if r["open"] == r["open"] else None,
                "high": float(r["high"]) if r["high"] == r["high"] else None,
                "low": float(r["low"]) if r["low"] == r["low"] else None,
                "close": float(r["close"]),
                "volume": float(r["volume"]) if r["volume"] == r["volume"] else None,
                "amount": float(r["amount"]) if r["amount"] == r["amount"] else None,
            }
        )

    d1.upsert_rows(f"{prefix}stock_prices", rows)

    latest = rows[-1]
    now = datetime.utcnow().isoformat() + "Z"
    d1.execute_sql(
        f"UPDATE {prefix}stocks SET last_price = ?, price_updated_at = ?, updated_at = ? WHERE code = ?",
        [latest["close"], now, now, code],
    )
    return True


def main() -> None:
    prefix = get_table_prefix()
    d1 = D1Client()

    backfill_mode = os.environ.get("BACKFILL_MODE", "false").lower() == "true"
    backfill_batch = int(os.environ.get("BACKFILL_BATCH", "0"))

    bootstrap_stock_list(d1, prefix)

    codes = [
        normalize_ticker(r["code"])
        for r in d1.execute_sql(f"SELECT code FROM {prefix}stocks WHERE market = 'A' ORDER BY code")
    ]

    if backfill_mode:
        codes = [c for i, c in enumerate(codes) if i % BACKFILL_TOTAL_BATCHES == backfill_batch]
        log.info(
            f"Backfill mode: batch {backfill_batch}/{BACKFILL_TOTAL_BATCHES}, "
            f"{len(codes)} stocks in this slice"
        )
    else:
        log.info(f"Incremental mode: {len(codes)} stocks to check")

    success = 0
    failure = 0
    for i, code in enumerate(codes, start=1):
        ok = collect_one_stock(d1, prefix, code, backfill_mode)
        success += int(ok)
        failure += int(not ok)
        if i % STOCKS_PER_LOG == 0:
            log.info(f"Progress: {i}/{len(codes)} ({failure} failures so far)")

    log.info(f"Done. {success} ok, {failure} failed out of {len(codes)}")

    if codes and failure / len(codes) > 0.2:
        now = datetime.utcnow().isoformat() + "Z"
        d1.execute_sql(
            f"INSERT OR REPLACE INTO {prefix}system_settings (key, value, updated_at) VALUES (?, ?, ?)",
            [
                "collect_prices_last_failure",
                f"{failure}/{len(codes)} stocks failed on {now}",
                now,
            ],
        )
        log.warning(f"High failure rate ({failure}/{len(codes)}), recorded to system_settings")


if __name__ == "__main__":
    main()
