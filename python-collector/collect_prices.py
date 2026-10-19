#!/usr/bin/env python3
"""OHLCV collector for the whole A-share market.

Modes (``PRICE_MODE`` env var, see .env.example):
  - ``auto`` (default): a bulk snapshot during/after the session; ``close``
    after 15:05 Beijing time, otherwise ``snapshot``.
  - ``snapshot``: one ``ak.stock_zh_a_spot_em()`` call writes today's OHLC for
    every stock — this is what keeps the full market inside the 25-minute job
    limit (the old per-stock Sina loop needed ~54 minutes).
  - ``close``: same bulk snapshot, run after the close. It always re-fetches
    and overwrites today's row, so an intraday snapshot can never prevent the
    final closing price from being stored.
  - ``history``: per-stock ``stock_zh_a_daily`` fallback, sharded across runs
    with a daily cursor. Used when the snapshot endpoint is unavailable.
  - ``backfill``: ~10 years per stock, sharded with a persistent cursor.

Both per-stock modes write a cursor + coverage progress to ``system_settings``
so a run that hits the job timeout resumes instead of starting over.
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
from common.rate_limiter import eastmoney_limiter, sina_limiter
from common.retry import call_with_retries
from common.settings import get_setting, set_json_setting, set_setting
from common.stock_list import sync_stock_list
from common.table_prefix import get_table_prefix
from common.ticker_utils import code_to_akshare_daily_prefix, normalize_ticker
from common.trading_calendar import is_trading_day

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_prices")

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
BACKFILL_YEARS = 10
BACKFILL_TOTAL_BATCHES = 50
DEFAULT_HISTORY_BATCH_SIZE = 600
DEFAULT_BACKFILL_BATCH_SIZE = 300
STOCKS_PER_LOG = 50
MAX_FAILURE_RATE = 0.2
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
CLOSE_HOUR = 15
CLOSE_MINUTE = 5

VALID_MODES = {"auto", "snapshot", "close", "history", "backfill"}


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def beijing_today() -> date:
    return beijing_now().date()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_price_mode(
    env_mode: str | None = None,
    now: datetime | None = None,
) -> str:
    """Resolve ``auto`` into ``snapshot``/``close`` using Beijing time."""
    mode = (env_mode if env_mode is not None else os.environ.get("PRICE_MODE", "auto")).strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid PRICE_MODE {mode!r}; expected one of {sorted(VALID_MODES)}")
    if mode == "backfill":
        return mode
    if mode != "auto":
        return mode
    if os.environ.get("BACKFILL_MODE", "false").lower() == "true":
        return "backfill"
    current = now or beijing_now()
    if current.weekday() < 5 and (current.hour, current.minute) >= (CLOSE_HOUR, CLOSE_MINUTE):
        return "close"
    return "snapshot"


def get_watermark(d1: D1Client, prefix: str, code: str) -> str | None:
    rows = d1.execute_sql(
        f"SELECT trade_date FROM {prefix}stock_prices WHERE code = ? ORDER BY trade_date DESC LIMIT 1",
        [code],
    )
    return rows[0]["trade_date"] if rows else None


def fetch_ohlcv(code: str, start_date: str, end_date: str):
    symbol = code_to_akshare_daily_prefix(code)

    def _fetch():
        sina_limiter.wait()
        return ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=end_date)

    return call_with_retries(
        _fetch,
        description=f"ak.stock_zh_a_daily({symbol}, {start_date}..{end_date})",
        max_attempts=3,
        logger=log,
    )


def fetch_spot_snapshot():
    def _fetch():
        eastmoney_limiter.wait()
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            raise RuntimeError("ak.stock_zh_a_spot_em() returned no rows")
        return df

    return call_with_retries(
        _fetch,
        description="ak.stock_zh_a_spot_em() (full-market snapshot)",
        max_attempts=3,
        logger=log,
    )


def _price_rows_from_history_frame(df, code: str) -> list[dict]:
    rows: list[dict] = []
    for _, record in df.iterrows():
        trade_date = clean_date(record.get("date"))
        close = to_float(record.get("close"))
        if not trade_date or close is None or close <= 0:
            continue
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "open": to_float(record.get("open")),
                "high": to_float(record.get("high")),
                "low": to_float(record.get("low")),
                "close": close,
                "volume": to_float(record.get("volume")),
                "amount": to_float(record.get("amount")),
            }
        )
    rows.sort(key=lambda r: r["trade_date"])
    return rows


def _price_row_from_snapshot(record, code: str, trade_date: str) -> dict | None:
    close = to_float(record.get("最新价"))
    if close is None or close <= 0:
        return None
    return {
        "code": code,
        "trade_date": trade_date,
        "open": to_float(record.get("今开")),
        "high": to_float(record.get("最高")),
        "low": to_float(record.get("最低")),
        "close": close,
        "volume": to_float(record.get("成交量")),
        "amount": to_float(record.get("成交额")),
    }


def write_spot_snapshot(
    d1: D1Client,
    prefix: str,
    df,
    *,
    trade_date: date | None = None,
    now_iso: str | None = None,
) -> dict:
    """Write one snapshot row per stock and refresh stocks.last_price.

    The snapshot is always upserted, so a later ``close`` run overwrites an
    intraday row with the final closing price instead of skipping it.
    """
    target_date = (trade_date or beijing_today()).isoformat()
    now = now_iso or utc_now_iso()

    price_rows: list[dict] = []
    latest: list[dict] = []
    skipped = 0
    for _, record in df.iterrows():
        raw_code = record.get("代码")
        if raw_code is None:
            skipped += 1
            continue
        try:
            code = normalize_ticker(str(raw_code).split(".")[0].zfill(6))
        except ValueError:
            skipped += 1
            continue
        row = _price_row_from_snapshot(record, code, target_date)
        if row is None:
            skipped += 1
            continue
        price_rows.append(row)
        latest.append({"code": code, "last_price": row["close"]})

    if not price_rows:
        log.warning("Snapshot contained no usable price rows (skipped %d)", skipped)
        return {"rows": 0, "updated": 0, "skipped": skipped, "trade_date": target_date}

    written = d1.upsert_rows(f"{prefix}stock_prices", price_rows)
    updated = d1.update_rows(
        f"{prefix}stocks",
        "code",
        latest,
        ["last_price"],
        static_values={"price_updated_at": now, "updated_at": now},
    )
    log.info(
        "Snapshot %s: wrote %d price rows, refreshed %d stocks, skipped %d",
        target_date,
        written,
        updated,
        skipped,
    )
    return {
        "rows": written,
        "updated": updated,
        "skipped": skipped,
        "trade_date": target_date,
    }


def collect_one_stock(
    d1: D1Client,
    prefix: str,
    code: str,
    backfill_mode: bool,
) -> bool:
    """Fetch one stock's daily bars. Returns False only on an upstream failure.

    The old watermark check (``(today - trade_date).days < 1 -> skip``) is
    gone: every run re-fetches a small overlap that always includes today, so
    an intraday snapshot cannot block the post-close correction.
    """
    today = beijing_today()

    if backfill_mode:
        start_date = today - timedelta(days=365 * BACKFILL_YEARS)
    else:
        watermark = get_watermark(d1, prefix, code)
        if watermark:
            try:
                watermark_date = date.fromisoformat(watermark[:10])
            except ValueError:
                watermark_date = today - timedelta(days=365)
            start_date = min(today, watermark_date - timedelta(days=5))
        else:
            # No history yet: one year is enough for the detail chart; the
            # dedicated backfill mode fills the full decade.
            start_date = today - timedelta(days=365)

    try:
        df = fetch_ohlcv(code, start_date.strftime("%Y%m%d"), today.strftime("%Y%m%d"))
    except Exception as exc:  # noqa: BLE001 - one stock must not abort the batch
        log.warning("%s: OHLCV fetch failed, skipping: %s", code, exc)
        return False

    if df is None or df.empty:
        log.info("%s: no new bars (suspended or delisted)", code)
        return True

    rows = _price_rows_from_history_frame(df, code)
    if not rows:
        log.warning("%s: upstream returned rows but no valid close price", code)
        return True

    d1.upsert_rows(f"{prefix}stock_prices", rows)

    latest = rows[-1]
    if latest["trade_date"] == today.isoformat():
        now = utc_now_iso()
        d1.execute_sql(
            f"UPDATE {prefix}stocks SET last_price = ?, price_updated_at = ?, updated_at = ? WHERE code = ?",
            [latest["close"], now, now, code],
        )
    return True


def _cursor_start(codes: list[str], cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return codes.index(cursor) + 1
    except ValueError:
        log.warning("Cursor %s no longer exists in the stock list; restarting from the top", cursor)
        return 0


def run_sharded_history(
    d1: D1Client,
    prefix: str,
    codes: list[str],
    *,
    mode: str,
    batch_size: int,
    today: date | None = None,
) -> dict:
    """Process one shard of the per-stock history fallback, with a cursor."""
    target_day = (today or beijing_today()).isoformat()
    is_backfill = mode == "backfill"
    cursor_key = "price_backfill_cursor" if is_backfill else f"price_history_cursor:{target_day}"
    progress_key = "price_backfill_progress" if is_backfill else "price_history_progress"
    now = utc_now_iso()

    cursor = get_setting(d1, prefix, cursor_key)
    start = _cursor_start(codes, cursor)
    if start >= len(codes):
        start = 0
        cursor = None
    batch = codes[start : start + max(1, batch_size)]

    success = 0
    failure = 0
    for index, code in enumerate(batch, start=1):
        try:
            ok = collect_one_stock(d1, prefix, code, is_backfill)
        except Exception as exc:  # noqa: BLE001 - one stock must not abort the shard
            log.warning("%s: collection failed, skipping: %s", code, exc)
            ok = False
        success += int(ok)
        failure += int(not ok)
        if index % STOCKS_PER_LOG == 0:
            log.info(
                "%s progress: %d/%d in this shard (%d failures so far)",
                mode,
                index,
                len(batch),
                failure,
            )

    completed = start + len(batch) >= len(codes)
    next_cursor = "" if completed else batch[-1]
    set_setting(d1, prefix, cursor_key, next_cursor, now)
    progress = {
        "date": target_day,
        "mode": mode,
        "processed": start + len(batch),
        "total": len(codes),
        "shard_start": start,
        "shard_size": len(batch),
        "success": success,
        "failure": failure,
        "completed": completed,
        "updated_at": now,
    }
    set_json_setting(d1, prefix, progress_key, progress, now)
    if is_backfill and completed:
        set_setting(d1, prefix, "price_backfill_completed_at", now, now)

    log.info(
        "%s shard done: %d/%d processed, %d ok, %d failed, completed=%s",
        mode,
        start + len(batch),
        len(codes),
        success,
        failure,
        completed,
    )
    return progress


def _record_failure(d1: D1Client, prefix: str, message: str) -> None:
    now = utc_now_iso()
    set_setting(d1, prefix, "collect_prices_last_failure", message, now)
    log.error(message)


def _legacy_batch_slice(codes: list[str]) -> list[str]:
    raw = os.environ.get("BACKFILL_BATCH")
    if raw is None or raw.strip() == "":
        return codes
    total = int(os.environ.get("BACKFILL_TOTAL_BATCHES", str(BACKFILL_TOTAL_BATCHES)))
    index = int(raw)
    if total <= 0 or index < 0 or index >= total:
        raise ValueError(f"Invalid BACKFILL_BATCH={index} for {total} batches")
    return [code for i, code in enumerate(codes) if i % total == index]


def main() -> None:
    prefix = get_table_prefix()
    d1 = D1Client()

    mode = resolve_price_mode()
    log.info("Price collection mode: %s (Beijing time %s)", mode, beijing_now().isoformat())

    sync_stock_list(d1, prefix, log)

    codes = [
        normalize_ticker(r["code"])
        for r in d1.execute_sql(f"SELECT code FROM {prefix}stocks WHERE market = 'A' ORDER BY code")
    ]
    log.info("Loaded %d A-share codes", len(codes))

    if mode in {"snapshot", "close"}:
        current = beijing_now()
        if not is_trading_day(current.date(), log):
            log.info("Not a trading day; snapshot collection skipped")
            return
        if (current.hour, current.minute) < (MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE):
            log.info("Before the 09:30 open; snapshot collection skipped to avoid stamping yesterday's close as today")
            return
        try:
            df = fetch_spot_snapshot()
            stats = write_spot_snapshot(d1, prefix, df)
            now = utc_now_iso()
            set_json_setting(
                d1,
                prefix,
                "price_snapshot_progress",
                {
                    "date": stats["trade_date"],
                    "mode": mode,
                    "rows": stats["rows"],
                    "updated": stats["updated"],
                    "skipped": stats["skipped"],
                    "completed": True,
                    "updated_at": now,
                },
                now,
            )
            if mode == "close":
                set_setting(d1, prefix, "price_close_date", beijing_today().isoformat(), now)
            return
        except Exception as exc:  # noqa: BLE001 - degrade to sharded per-stock mode
            log.error("Snapshot mode failed (%s); falling back to sharded history mode", exc)
            mode = "history"

    if mode == "backfill":
        codes = _legacy_batch_slice(codes)
        default_batch = DEFAULT_BACKFILL_BATCH_SIZE if os.environ.get("BACKFILL_BATCH") is None else len(codes)
    else:
        default_batch = DEFAULT_HISTORY_BATCH_SIZE
    batch_size = int(os.environ.get("PRICE_BATCH_SIZE", str(default_batch)))
    if batch_size <= 0:
        batch_size = default_batch

    progress = run_sharded_history(d1, prefix, codes, mode=mode, batch_size=batch_size)
    checked = progress["success"] + progress["failure"]
    if checked and progress["failure"] / checked > MAX_FAILURE_RATE:
        _record_failure(
            d1,
            prefix,
            f"{progress['failure']}/{checked} stocks failed in {mode} mode on {progress['date']}",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
