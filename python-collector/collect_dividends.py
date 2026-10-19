#!/usr/bin/env python3
"""Dividend + fundamentals collector — run once daily after market close.

Steps:
  1. Sync the full A-share stock list (code/name) with upstream fallbacks
     (see common/stock_list.py).
  2. Bulk-fetch dividend records for the requested report periods via
     ak.stock_fhps_em(), which returns the whole market in one call per period.
  3. Best-effort market cap / PE / PB snapshot via ak.stock_zh_a_spot_em().
  4. Recompute static/TTM dividend yield time series (`dividend_yields`) and
     the denormalized current-yield columns on `stocks`, using dividend
     history plus the price history collected by collect_prices.py.

Modes (``COLLECT_MODE``):
  - ``daily`` (default): the three most recently disclosed report periods.
  - ``backfill``: the last 10 years (20 periods), or ``BACKFILL_PERIODS``.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import akshare as ak

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # local runs can use python-collector/.env; CI injects real env vars
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - requirements.txt installs it
    pass

from common.d1_client import D1Client
from common.numeric import clean_date, to_float
from common.rate_limiter import eastmoney_limiter
from common.retry import call_with_retries
from common.stock_list import sync_stock_list
from common.table_prefix import get_table_prefix
from common.ticker_utils import normalize_ticker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_dividends")

BACKFILL_YEARS = 10
DAILY_REPORT_PERIODS = 3
VALID_MODES = {"daily", "backfill"}
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def beijing_today() -> date:
    return datetime.now(BEIJING_TZ).date()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def latest_disclosed_report_period(today: date | None = None) -> str:
    """Return the most recent report period that can plausibly have data.

    A-share annual reports (``YYYY1231``) are due by April 30 of the following
    year; half-year reports (``YYYY0630``) are due by August 31. From July 1 we
    therefore start from the current year's half-year period (early reporters
    are already publishing), otherwise from the previous year's annual period.
    """
    current = today or beijing_today()
    if (current.month, current.day) > (6, 30):
        return f"{current.year}0630"
    return f"{current.year - 1}1231"


def report_periods(years: int, today: date | None = None) -> list[str]:
    """Return ``2 * years`` report periods, most recent disclosed first."""
    if years <= 0:
        return []
    end = latest_disclosed_report_period(today)
    year = int(end[:4])
    month = int(end[4:6])
    periods: list[str] = []
    for _ in range(years * 2):
        if month == 6:
            periods.append(f"{year}0630")
            year -= 1
            month = 12
        else:
            periods.append(f"{year}1231")
            month = 6
    return periods


def daily_report_periods(count: int = DAILY_REPORT_PERIODS, today: date | None = None) -> list[str]:
    """Daily incremental window: at least the last three report periods."""
    periods = report_periods(max(2, (count + 1) // 2 + 1), today)
    return periods[:count]


DIVIDEND_STATUS_MAP = {
    "实施分配": "implemented",
    "实施": "implemented",
    "预案": "proposal",
    "股东大会通过": "proposal",
    "董事会预案": "proposal",
}


def parse_dividend_status(raw: object) -> str:
    if not isinstance(raw, str):
        # pandas NaN/NaT and None all land here.
        return "proposal"
    text = raw.strip()
    if not text:
        return "proposal"
    return DIVIDEND_STATUS_MAP.get(text, "implemented" if "实施" in text else "proposal")


def fetch_dividend_frame(period: str):
    def _fetch():
        eastmoney_limiter.wait()
        return ak.stock_fhps_em(date=period)

    return call_with_retries(
        _fetch,
        description=f"ak.stock_fhps_em(date={period})",
        max_attempts=3,
        logger=log,
    )


def collect_dividends_for_period(d1: D1Client, prefix: str, period: str) -> int:
    try:
        df = fetch_dividend_frame(period)
    except Exception as exc:  # noqa: BLE001 - one period must not abort the run
        log.warning("stock_fhps_em(%s) failed after retries: %s", period, exc)
        return 0

    if df is None or df.empty:
        log.info("Report period %s: upstream returned no rows", period)
        return 0

    now = utc_now_iso()
    report_period_str = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
    rows = []

    for _, record in df.iterrows():
        try:
            code = normalize_ticker(str(record["代码"]).split(".")[0].zfill(6))
        except ValueError:
            continue
        # "现金分红-现金分红比例" is expressed per 10 shares (e.g. 1.6 means
        # "10派1.6元"); convert to a true per-share amount.
        per_10_shares = to_float(record.get("现金分红-现金分红比例"))
        if per_10_shares is None:
            continue
        per_share_div = round(per_10_shares / 10.0, 6)
        if per_share_div <= 0:
            continue

        # NaN/None/Inf -> 0.0 for the bonus/transfer columns. Without this,
        # float(NaN) survives `or 0` and requests raises InvalidJSONError
        # before the D1 request is sent.
        bonus_share_ratio = to_float(record.get("送转股份-送转比例"), 0.0) or 0.0
        transfer_ratio = to_float(record.get("送转股份-转股比例"), 0.0) or 0.0

        rows.append(
            {
                "id": f"{code}-{report_period_str}",
                "code": code,
                "report_period": report_period_str,
                "per_share_div": per_share_div,
                "record_date": clean_date(record.get("股权登记日")),
                "ex_div_date": clean_date(record.get("除权除息日")),
                "pay_date": None,
                "status": parse_dividend_status(record.get("方案进度")),
                "bonus_share_ratio": bonus_share_ratio,
                "transfer_ratio": transfer_ratio,
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
    """Best-effort market cap / PE / PB snapshot, written in bulk.

    A failure here must never abort the rest of the collection run.
    """
    try:

        def _fetch():
            eastmoney_limiter.wait()
            return ak.stock_zh_a_spot_em()

        df = call_with_retries(
            _fetch,
            description="ak.stock_zh_a_spot_em() (fundamentals snapshot)",
            max_attempts=3,
            logger=log,
        )
    except Exception as exc:  # noqa: BLE001 - optional enrichment
        log.warning("stock_zh_a_spot_em() failed (fundamentals snapshot skipped): %s", exc)
        return

    if df is None or df.empty:
        log.warning("stock_zh_a_spot_em() returned no data")
        return

    now = utc_now_iso()
    updates = []
    for _, record in df.iterrows():
        try:
            code = normalize_ticker(str(record["代码"]).split(".")[0].zfill(6))
        except ValueError:
            continue
        market_cap = to_float(record.get("总市值"))
        if market_cap is None:
            continue
        updates.append(
            {
                "code": code,
                "market_cap": market_cap / 1e8,  # AKShare returns raw CNY; store as 亿元
                "pe_ttm": to_float(record.get("市盈率-动态")),
                "pb": to_float(record.get("市净率")),
            }
        )

    if updates:
        d1.update_rows(
            f"{prefix}stocks",
            "code",
            updates,
            ["market_cap", "pe_ttm", "pb"],
            static_values={"updated_at": now},
        )
    log.info("Updated fundamentals snapshot for %d stocks", len(updates))


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
    if last_price is None or last_price <= 0:
        last_price = prices[-1]["close"]
    if not last_price or last_price <= 0:
        return

    now = utc_now_iso()
    yield_rows = []

    for d in implemented:
        ex_date = d["ex_div_date"]
        price_at_date = price_on_or_before(prices, ex_date)
        if price_at_date is None:
            continue

        # Static yield anchored at this date: dividends from the single most
        # recent complete fiscal year as of this date. (Metric definition is
        # intentionally unchanged in this round.)
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


def resolve_collect_mode() -> str:
    mode = os.environ.get("COLLECT_MODE", "daily").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid COLLECT_MODE {mode!r}; expected one of {sorted(VALID_MODES)}")
    return mode


def main() -> None:
    prefix = get_table_prefix()
    d1 = D1Client()
    mode = resolve_collect_mode()

    sync_stock_list(d1, prefix, log)

    if mode == "backfill":
        all_periods = report_periods(BACKFILL_YEARS)
        requested = int(os.environ.get("BACKFILL_PERIODS", str(len(all_periods))))
        periods = all_periods[: max(1, requested)]
    else:
        periods = daily_report_periods(DAILY_REPORT_PERIODS)
        if os.environ.get("BACKFILL_PERIODS"):
            log.info(
                "COLLECT_MODE=daily ignores BACKFILL_PERIODS=%s; use COLLECT_MODE=backfill",
                os.environ["BACKFILL_PERIODS"],
            )

    log.info("Collect mode=%s, report periods=%s", mode, periods)

    total_dividends = 0
    failed_periods = 0
    for period in periods:
        try:
            n = collect_dividends_for_period(d1, prefix, period)
        except Exception as exc:  # noqa: BLE001 - keep collecting other periods
            failed_periods += 1
            log.error("Period %s failed: %s", period, exc)
            continue
        total_dividends += n
        log.info("Period %s: %d dividend records", period, n)

    log.info("Total dividend records collected: %d", total_dividends)

    try:
        collect_fundamentals_snapshot(d1, prefix)
    except Exception as exc:  # noqa: BLE001 - optional enrichment
        log.warning("Fundamentals snapshot failed: %s", exc)

    codes = [
        normalize_ticker(r["code"])
        for r in d1.execute_sql(f"SELECT DISTINCT code FROM {prefix}dividends")
    ]
    log.info("Recomputing yields for %d stocks with dividend history", len(codes))
    for i, code in enumerate(codes, start=1):
        try:
            recompute_yields_for_stock(d1, prefix, code)
        except Exception as exc:  # noqa: BLE001 - one stock must not abort the batch
            log.warning("%s: yield computation failed: %s", code, exc)
        if i % 100 == 0:
            log.info("Yield recompute progress: %d/%d", i, len(codes))

    if failed_periods:
        log.warning("%d of %d report periods failed; see warnings above", failed_periods, len(periods))
    log.info("Done.")


if __name__ == "__main__":
    main()
