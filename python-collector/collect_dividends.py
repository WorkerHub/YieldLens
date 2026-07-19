#!/usr/bin/env python3
"""Dividend + fundamentals collector — run once daily after market close.

Steps:
  1. Sync the full A-share stock list (code/name) from AKShare's official
     exchange data, so newly-listed stocks and renames are picked up.
  2. Bulk-fetch dividend records for the last N report periods (year-end +
     mid-year) via ak.stock_fhps_em(), which returns the whole market in one
     call per period — this is why dividends are cheap to backfill compared
     to price history.
  3. Best-effort fetch of market cap / PE / PB / industry via
     ak.stock_zh_a_spot_em(). This endpoint depends on Eastmoney's numbered
     push2 subdomains, which were unreachable from the sandbox this collector
     was developed in (see PRD.md section 5.2) — production GitHub Actions
     runners have normal internet access, but this step is wrapped so a
     failure here never aborts the whole run.
  4. Recompute static/TTM dividend yield time series (`dividend_yields`) and
     the denormalized current-yield columns on `stocks`, using dividend
     history plus the price history collected by collect_prices.py.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.d1_client import D1Client
from common.rate_limiter import eastmoney_limiter
from common.table_prefix import get_table_prefix
from common.ticker_utils import infer_exchange, normalize_ticker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_dividends")

BACKFILL_YEARS = 10


def sync_stock_list(d1: D1Client, prefix: str) -> None:
    log.info("Syncing stock list from ak.stock_info_a_code_name()")
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

    # INSERT OR REPLACE would clobber columns populated elsewhere (last_price,
    # yields, etc) with NULLs for already-existing stocks, so only insert
    # codes that aren't already present.
    existing = {
        r["code"] for r in d1.execute_sql(f"SELECT code FROM {prefix}stocks")
    }
    new_rows = [r for r in rows if r["code"] not in existing]
    if new_rows:
        written = d1.upsert_rows(f"{prefix}stocks", new_rows)
        log.info(f"Added {written} newly-listed stocks")
    else:
        log.info("No new stocks to add")


def report_periods(years: int) -> list[str]:
    """Return 'YYYYMMDD' report period dates: year-end + mid-year for each
    of the last `years` years, most recent first.
    """
    periods = []
    current_year = date.today().year
    for y in range(current_year, current_year - years, -1):
        periods.append(f"{y}1231")
        periods.append(f"{y}0630")
    return periods


DIVIDEND_STATUS_MAP = {
    "实施分配": "implemented",
    "实施": "implemented",
    "预案": "proposal",
    "股东大会通过": "proposal",
    "董事会预案": "proposal",
}


def parse_dividend_status(raw: str | None) -> str:
    if not raw:
        return "proposal"
    return DIVIDEND_STATUS_MAP.get(raw, "implemented" if "实施" in raw else "proposal")


def collect_dividends_for_period(d1: D1Client, prefix: str, period: str) -> int:
    eastmoney_limiter.wait()
    try:
        df = ak.stock_fhps_em(date=period)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"stock_fhps_em({period}) failed: {exc}")
        return 0

    if df is None or df.empty:
        return 0

    now = datetime.utcnow().isoformat() + "Z"
    report_period_str = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
    rows = []

    for _, r in df.iterrows():
        code = str(r["代码"]).zfill(6)
        # "现金分红-现金分红比例" is expressed per 10 shares (e.g. 1.6 means
        # "10派1.6元"); convert to a true per-share amount.
        per_10_shares = r.get("现金分红-现金分红比例")
        if per_10_shares is None or pd.isna(per_10_shares):
            continue
        per_share_div = float(per_10_shares) / 10.0
        if per_share_div <= 0:
            continue

        ex_div_date = r.get("除权除息日")
        record_date = r.get("股权登记日")
        status = parse_dividend_status(r.get("方案进度"))

        rows.append(
            {
                "id": f"{code}-{report_period_str}",
                "code": code,
                "report_period": report_period_str,
                "per_share_div": per_share_div,
                "record_date": str(record_date) if record_date and not pd.isna(record_date) else None,
                "ex_div_date": str(ex_div_date) if ex_div_date and not pd.isna(ex_div_date) else None,
                "pay_date": None,
                "status": status,
                "bonus_share_ratio": float(r.get("送转股份-送转比例") or 0) or 0,
                "transfer_ratio": float(r.get("送转股份-转股比例") or 0) or 0,
                "payout_ratio": None,
                "source": "akshare_fhps_em",
                "created_at": now,
                "updated_at": now,
            }
        )

    if rows:
        d1.upsert_rows(f"{prefix}dividends", rows)
    return len(rows)


def collect_fundamentals_snapshot(d1: D1Client, prefix: str) -> None:
    """Best-effort market cap / PE / PB / industry snapshot.

    ak.stock_zh_a_spot_em() depends on Eastmoney's numbered push2 subdomains
    (e.g. 82.push2.eastmoney.com), which are known to be unreachable from
    some sandboxed environments (see PRD.md 5.2/5.3). A failure here must
    never abort the rest of the collection run.
    """
    try:
        eastmoney_limiter.wait()
        df = ak.stock_zh_a_spot_em()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"stock_zh_a_spot_em() failed (fundamentals snapshot skipped): {exc}")
        return

    if df is None or df.empty:
        log.warning("stock_zh_a_spot_em() returned no data")
        return

    now = datetime.utcnow().isoformat() + "Z"
    updated = 0
    for _, r in df.iterrows():
        code = str(r["代码"]).zfill(6)
        market_cap = r.get("总市值")
        pe_ttm = r.get("市盈率-动态")
        pb = r.get("市净率")
        if market_cap is None or pd.isna(market_cap):
            continue
        d1.execute_sql(
            f"UPDATE {prefix}stocks SET market_cap = ?, pe_ttm = ?, pb = ?, updated_at = ? WHERE code = ?",
            [
                float(market_cap) / 1e8,  # AKShare returns raw CNY; store as 亿元
                float(pe_ttm) if pe_ttm is not None and not pd.isna(pe_ttm) else None,
                float(pb) if pb is not None and not pd.isna(pb) else None,
                now,
                code,
            ],
        )
        updated += 1

    log.info(f"Updated fundamentals snapshot for {updated} stocks")


def price_on_or_before(prices: list[dict], target_date: str) -> float | None:
    """prices must be sorted ascending by trade_date."""
    nearest = None
    for p in prices:
        if p["trade_date"] <= target_date:
            nearest = p["close"]
        else:
            break
    return nearest


def recompute_yields_for_stock(d1: D1Client, prefix: str, code: str) -> None:
    dividends = d1.execute_sql(
        f"""SELECT * FROM {prefix}dividends
            WHERE code = ? AND status = 'implemented'
            ORDER BY ex_div_date ASC""",
        [code],
    )
    implemented = [d for d in dividends if d.get("ex_div_date")]
    if not implemented:
        return

    prices = d1.execute_sql(
        f"SELECT trade_date, close FROM {prefix}stock_prices WHERE code = ? ORDER BY trade_date ASC",
        [code],
    )
    if not prices:
        return

    stock_row = d1.execute_sql(f"SELECT last_price FROM {prefix}stocks WHERE code = ?", [code])
    last_price = stock_row[0]["last_price"] if stock_row else None
    if last_price is None:
        last_price = prices[-1]["close"]

    now = datetime.utcnow().isoformat() + "Z"
    yield_rows = []

    for d in implemented:
        ex_date = d["ex_div_date"]
        price_at_date = price_on_or_before(prices, ex_date)
        if price_at_date is None:
            continue

        # Static yield anchored at this date: dividends from the single most
        # recent complete fiscal year as of this date.
        this_year = int(d["report_period"][:4])
        same_year_divs = [
            x["per_share_div"] for x in implemented if x["report_period"][:4] == str(this_year)
        ]
        static_yield = round(sum(same_year_divs) / price_at_date * 100, 2)

        # TTM yield anchored at this date: sum of dividends whose ex_div_date
        # falls within the trailing 12 months of this date.
        cutoff = (datetime.strptime(ex_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
        ttm_divs = [
            x["per_share_div"]
            for x in implemented
            if x["ex_div_date"] and cutoff <= x["ex_div_date"] <= ex_date
        ]
        ttm_yield = round(sum(ttm_divs) / price_at_date * 100, 2)

        yield_rows.append(
            {
                "code": code,
                "calc_date": ex_date,
                "price": price_at_date,
                "static_yield": static_yield,
                "ttm_yield": ttm_yield,
            }
        )

    if yield_rows:
        d1.upsert_rows(f"{prefix}dividend_yields", yield_rows)

    # Denormalized current yield onto the stocks row, anchored to last_price.
    latest_year = max(int(d["report_period"][:4]) for d in implemented)
    latest_year_divs = [
        x["per_share_div"] for x in implemented if x["report_period"][:4] == str(latest_year)
    ]
    current_static_yield = round(sum(latest_year_divs) / last_price * 100, 2)

    today_str = date.today().isoformat()
    cutoff = (date.today() - timedelta(days=365)).isoformat()
    ttm_divs = [
        x["per_share_div"] for x in implemented if x["ex_div_date"] and cutoff <= x["ex_div_date"] <= today_str
    ]
    current_ttm_yield = round(sum(ttm_divs) / last_price * 100, 2)

    consecutive_years = 0
    year = date.today().year
    report_years = {int(x["report_period"][:4]) for x in implemented}
    while year in report_years or (year - 1) in report_years:
        if year in report_years or (year - 1) in report_years:
            consecutive_years += 1
        year -= 1
        if consecutive_years > 30:  # safety valve
            break

    d1.execute_sql(
        f"""UPDATE {prefix}stocks SET
              static_yield = ?, ttm_yield = ?, yield_updated_at = ?,
              last_dividend_date = ?, consecutive_dividend_years = ?, updated_at = ?
            WHERE code = ?""",
        [
            current_static_yield,
            current_ttm_yield,
            now,
            implemented[-1]["ex_div_date"],
            consecutive_years,
            now,
            code,
        ],
    )


def main() -> None:
    prefix = get_table_prefix()
    d1 = D1Client()
    backfill_periods = int(os.environ.get("BACKFILL_PERIODS", "1"))

    sync_stock_list(d1, prefix)

    all_periods = report_periods(BACKFILL_YEARS)
    periods = all_periods[:backfill_periods]

    total_dividends = 0
    for period in periods:
        n = collect_dividends_for_period(d1, prefix, period)
        total_dividends += n
        log.info(f"Period {period}: {n} dividend records")

    log.info(f"Total dividend records collected: {total_dividends}")

    collect_fundamentals_snapshot(d1, prefix)

    codes = [
        normalize_ticker(r["code"])
        for r in d1.execute_sql(f"SELECT DISTINCT code FROM {prefix}dividends")
    ]
    log.info(f"Recomputing yields for {len(codes)} stocks with dividend history")
    for i, code in enumerate(codes, start=1):
        try:
            recompute_yields_for_stock(d1, prefix, code)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"{code}: yield computation failed: {exc}")
        if i % 100 == 0:
            log.info(f"Yield recompute progress: {i}/{len(codes)}")

    log.info("Done.")


if __name__ == "__main__":
    main()
