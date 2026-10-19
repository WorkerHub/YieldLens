#!/usr/bin/env python3
"""Local end-to-end check for the collection chain — no Cloudflare credentials.

It runs both collectors against an in-memory SQLite database that implements
the same ``execute_sql`` / ``upsert_rows`` / ``update_rows`` contract as
``D1Client`` (so the 100-bound-parameter validation is exercised for real),
with AKShare replaced by fixtures:

* the primary stock-list endpoint fails (simulating the GitHub runner reset);
* the Eastmoney full-market snapshot is used as the stock-list fallback and
  as the price/fundamentals snapshot;
* dividends include NaN bonus/transfer columns and a NaN status.

Run from ``python-collector``:

    python tools/local_e2e.py

Exit code 0 means the whole chain (stock list -> prices -> dividends ->
yields) produced consistent, prefixed tables.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

COLLECTOR_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = COLLECTOR_DIR.parent
sys.path.insert(0, str(COLLECTOR_DIR))

os.environ.setdefault("CLOUDFLARE_ACCOUNT_ID", "local-e2e-account")
os.environ.setdefault("CLOUDFLARE_D1_DATABASE_ID", "local-e2e-db")
os.environ.setdefault("CLOUDFLARE_API_TOKEN", "local-e2e-token")
os.environ["TABLE_PREFIX"] = "yl"
os.environ["COLLECT_MODE"] = "daily"
os.environ["PRICE_MODE"] = "close"

import collect_dividends  # noqa: E402
import collect_prices  # noqa: E402
from common.d1_client import D1Client, _validate_params  # noqa: E402

PREFIX = "yl_"
FIXED_TODAY = date(2026, 10, 19)
EXPECTED_LATEST_PERIOD = "2026-06-30"


class LocalD1(D1Client):
    """SQLite-backed D1 stand-in that keeps the real batching/validation."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def execute_sql(self, sql: str, params=None):
        # Exercise the same pre-flight checks as the real client: >100 bound
        # parameters and NaN/Inf must fail before SQLite sees the statement.
        values = _validate_params(params or [], sql=sql)
        cursor = self.connection.execute(sql, values)
        rows = []
        if cursor.description:
            columns = [description[0] for description in cursor.description]
            rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
        self.connection.commit()
        return rows


def _schema_sql() -> str:
    schema = (REPO_DIR / "worker" / "src" / "db" / "schema.sql").read_text(encoding="utf-8")
    assert "{prefix}" in schema
    return schema.replace("{prefix}", PREFIX)


def _snapshot_frame(today, close_600519: float):
    # 成交量 is in 手 (lots); the collector must multiply by 100 to shares.
    return pd.DataFrame(
        [
            {"代码": "600519", "名称": "贵州茅台", "最新价": close_600519, "今开": 1490.0, "最高": 1510.0, "最低": 1480.0, "成交量": 10.0, "成交额": 1_500_000.0, "总市值": 1_900_000_000_000.0, "市盈率-动态": 20.0, "市净率": 8.0},
            {"代码": "000001", "名称": "平安银行", "最新价": 11.2, "今开": 11.0, "最高": 11.3, "最低": 10.9, "成交量": 20.0, "成交额": 22_400.0, "总市值": 220_000_000_000.0, "市盈率-动态": 5.0, "市净率": 0.6},
            {"代码": "000002", "名称": "万科A", "最新价": 8.4, "今开": 8.3, "最高": 8.5, "最低": 8.2, "成交量": 30.0, "成交额": 25_200.0, "总市值": 100_000_000_000.0, "市盈率-动态": float("nan"), "市净率": 0.4},
        ]
    )


def _history_frame():
    # Past dates only, so the backfill smoke run cannot overwrite today's
    # snapshot close. Volume is already in shares, matching the daily API.
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-10-16"),
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 1000.0,
                "amount": 100_000.0,
            },
            {
                "date": pd.Timestamp("2026-10-17"),
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1000.0,
                "amount": 101_000.0,
            },
        ]
    )


def _dividend_frame(today):
    return pd.DataFrame(
        [
            {"代码": "600519", "现金分红-现金分红比例": 21.0, "股权登记日": pd.Timestamp(today - timedelta(days=1)), "除权除息日": pd.Timestamp(today), "方案进度": "实施分配", "送转股份-送转比例": float("nan"), "送转股份-转股比例": float("nan")},
            {"代码": "000001", "现金分红-现金分红比例": 3.0, "股权登记日": pd.Timestamp(today - timedelta(days=1)), "除权除息日": pd.Timestamp(today), "方案进度": float("nan"), "送转股份-送转比例": 2.0, "送转股份-转股比例": None},
            {"代码": "000002", "现金分红-现金分红比例": float("nan"), "股权登记日": pd.Timestamp(today - timedelta(days=1)), "除权除息日": pd.Timestamp(today), "方案进度": "实施", "送转股份-送转比例": 0.0, "送转股份-转股比例": 0.0},
        ]
    )


def main() -> int:
    today = FIXED_TODAY
    connection = sqlite3.connect(":memory:")
    connection.executescript(_schema_sql())
    local_d1 = LocalD1(connection)

    dividends = _dividend_frame(today)
    current_snapshot = _snapshot_frame(today, 1490.0)
    snapshot_calls = 0
    history_calls = 0

    def snapshot_side_effect():
        nonlocal snapshot_calls
        snapshot_calls += 1
        return current_snapshot

    def history_side_effect(*_args, **_kwargs):
        nonlocal history_calls
        history_calls += 1
        return _history_frame()

    def primary_side_effect():
        raise ConnectionResetError("simulated GitHub runner reset")

    calendar = pd.DataFrame({"trade_date": [today]})

    patches = [
        patch.object(collect_prices, "D1Client", lambda: local_d1),
        patch.object(collect_dividends, "D1Client", lambda: local_d1),
        patch("common.retry.time.sleep", lambda _: None),
        patch("common.stock_list.ak.stock_info_a_code_name", side_effect=primary_side_effect),
        patch("common.stock_list.ak.stock_zh_a_spot_em", side_effect=snapshot_side_effect),
        patch("collect_prices.ak.stock_zh_a_spot_em", side_effect=snapshot_side_effect),
        patch("collect_prices.ak.stock_info_a_code_name", side_effect=primary_side_effect),
        patch.object(collect_prices, "fetch_ohlcv", side_effect=history_side_effect),
        patch("common.trading_calendar.ak.tool_trade_date_hist_sina", return_value=calendar),
        patch("collect_dividends.ak.stock_fhps_em", return_value=dividends),
        patch("collect_dividends.ak.stock_zh_a_spot_em", side_effect=snapshot_side_effect),
        patch("collect_dividends.ak.stock_info_a_code_name", side_effect=primary_side_effect),
    ]
    for patcher in patches:
        patcher.start()
    try:
        print("== price collector (intraday snapshot, close=1490) ==")
        os.environ["PRICE_MODE"] = "snapshot"
        current_snapshot = _snapshot_frame(today, 1490.0)
        collect_prices.main(today=today)
        intraday_close = connection.execute(
            "SELECT last_price FROM yl_stocks WHERE code = '600519'"
        ).fetchone()[0]
        assert intraday_close == 1490.0, intraday_close

        print("== price collector (close snapshot, close=1500) ==")
        os.environ["PRICE_MODE"] = "close"
        current_snapshot = _snapshot_frame(today, 1500.0)
        collect_prices.main(today=today)

        print("== dividend collector (daily, 3 report periods) ==")
        collect_dividends.main(today=today)

        print("== price backfill smoke (batch=1, cursor resume) ==")
        os.environ["PRICE_MODE"] = "backfill"
        os.environ["PRICE_BATCH_SIZE"] = "1"
        collect_prices.main(today=today)
        collect_prices.main(today=today)
    finally:
        for patcher in reversed(patches):
            patcher.stop()

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    }
    expected_tables = {
        "yl_stocks",
        "yl_stock_prices",
        "yl_dividends",
        "yl_dividend_yields",
        "yl_system_settings",
    }
    assert expected_tables <= tables, f"missing prefixed tables: {expected_tables - tables}"

    stock_count = connection.execute("SELECT COUNT(*) FROM yl_stocks").fetchone()[0]
    price_count = connection.execute("SELECT COUNT(*) FROM yl_stock_prices").fetchone()[0]
    dividend_count = connection.execute("SELECT COUNT(*) FROM yl_dividends").fetchone()[0]
    yield_count = connection.execute("SELECT COUNT(*) FROM yl_dividend_yields").fetchone()[0]
    assert stock_count == 3, stock_count
    # 3 today rows + 2 past bars for each of the 2 backfill smoke stocks.
    assert price_count == 7, price_count
    assert dividend_count == 6, dividend_count  # 2 cash-dividend stocks x 3 periods
    # Only 600519 is "implemented"; 000001 has a NaN status -> proposal.
    assert yield_count == 1, yield_count

    latest_period, distinct_periods = connection.execute(
        "SELECT MAX(report_period), COUNT(DISTINCT report_period) FROM yl_dividends"
    ).fetchone()
    assert latest_period == EXPECTED_LATEST_PERIOD, latest_period
    assert distinct_periods == 3, distinct_periods

    expected_prices = {"600519": 1500.0, "000001": 11.2, "000002": 8.4}
    for code, expected in expected_prices.items():
        last_price, updated_at = connection.execute(
            "SELECT last_price, price_updated_at FROM yl_stocks WHERE code = ?", (code,)
        ).fetchone()
        assert last_price == expected, (code, last_price, expected)
        assert updated_at, code

    null_bonus = connection.execute(
        "SELECT COUNT(*) FROM yl_dividends WHERE bonus_share_ratio IS NULL OR transfer_ratio IS NULL"
    ).fetchone()[0]
    assert null_bonus == 0, null_bonus

    statuses = dict(
        connection.execute(
            "SELECT code, status FROM yl_dividends GROUP BY code"
        ).fetchall()
    )
    assert statuses == {"600519": "implemented", "000001": "proposal"}, statuses

    snapshot_volume = connection.execute(
        "SELECT volume FROM yl_stock_prices WHERE code = '600519' AND trade_date = ?",
        (today.isoformat(),),
    ).fetchone()[0]
    history_volume = connection.execute(
        "SELECT volume FROM yl_stock_prices WHERE code = '000001' AND trade_date = '2026-10-16'"
    ).fetchone()[0]
    assert snapshot_volume == 1000.0, snapshot_volume  # 10 lots -> 1000 shares
    assert history_volume == 1000.0, history_volume
    assert snapshot_volume == history_volume

    backfill_cursor = connection.execute(
        "SELECT value FROM yl_system_settings WHERE key = 'price_backfill_cursor'"
    ).fetchone()[0]
    assert backfill_cursor == "000002", backfill_cursor
    assert history_calls == 2, history_calls

    assert snapshot_calls >= 4, snapshot_calls
    print()
    print("local e2e OK")
    print(f"  prefix tables : {', '.join(sorted(expected_tables))}")
    print(f"  stocks        : {stock_count}")
    print(f"  price rows    : {price_count} (close={expected_prices})")
    print(f"  dividends     : {dividend_count} (latest report period {latest_period})")
    print(f"  yield rows    : {yield_count}")
    print("  intraday 1490 -> close 1500 overwrite: OK")
    print(f"  volume unit   : snapshot={snapshot_volume}, daily={history_volume} shares")
    print(f"  backfill cursor: {backfill_cursor} after 2 one-stock runs")
    print(f"  snapshot calls: {snapshot_calls}; per-stock history calls: {history_calls}")
    print("  stock-list fallback: primary reset -> Eastmoney snapshot -> D1 rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
