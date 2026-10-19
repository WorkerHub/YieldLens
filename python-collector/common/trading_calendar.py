"""A-share trading-day check with a D1-backed calendar cache.

Writing a "today" price row on a weekend or public holiday would stamp the
previous session's close with a non-trading date. The snapshot collector
therefore asks AKShare's Sina trading calendar first.

If the upstream calendar is unavailable, the last successful calendar is read
from ``system_settings``. When there is no cache either, the snapshot/close
path **fails closed** (skips writing and records a health marker) instead of
falling back to a weekday guess that would fabricate a holiday price row.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

import akshare as ak

from common.retry import call_with_retries

CALENDAR_SETTING_KEY = "trading_calendar"

_calendar_cache: set[date] | None = None


def reset_cache() -> None:
    global _calendar_cache
    _calendar_cache = None


def fetch_trading_calendar(logger: logging.Logger) -> set[date] | None:
    """Fetch the Sina trading calendar, or None when the upstream fails."""
    global _calendar_cache
    if _calendar_cache is not None:
        return _calendar_cache
    try:
        df = call_with_retries(
            lambda: ak.tool_trade_date_hist_sina(),
            description="ak.tool_trade_date_hist_sina()",
            max_attempts=2,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001 - caller decides whether cache/fail-closed
        logger.warning("Trading calendar unavailable (%s)", exc)
        return None
    if df is None or df.empty or "trade_date" not in df.columns:
        logger.warning("Trading calendar returned no usable rows")
        return None
    dates: set[date] = set()
    for value in df["trade_date"]:
        if isinstance(value, date):
            dates.add(value)
        else:
            try:
                dates.add(date.fromisoformat(str(value)[:10]))
            except ValueError:
                continue
    if not dates:
        logger.warning("Trading calendar parsed to zero dates")
        return None
    _calendar_cache = dates
    return dates


def _read_cached_calendar(d1, prefix: str, logger: logging.Logger) -> set[date] | None:
    try:
        rows = d1.execute_sql(
            f"SELECT value FROM {prefix}system_settings WHERE key = ?",
            [CALENDAR_SETTING_KEY],
        )
    except Exception as exc:  # noqa: BLE001 - cache is best effort
        logger.warning("Failed to read cached trading calendar: %s", exc)
        return None
    if not rows:
        return None
    try:
        payload = json.loads(rows[0]["value"])
        dates = {date.fromisoformat(value) for value in payload.get("dates", [])}
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Cached trading calendar is invalid: %s", exc)
        return None
    if not dates:
        return None
    logger.warning(
        "Using cached trading calendar from %s (%d dates)",
        payload.get("updated_at", "unknown"),
        len(dates),
    )
    return dates


def load_trading_calendar(d1, prefix: str, logger: logging.Logger) -> set[date] | None:
    """Return the calendar, preferring upstream and falling back to D1 cache."""
    calendar = fetch_trading_calendar(logger)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if calendar is not None:
        try:
            d1.execute_sql(
                f"INSERT OR REPLACE INTO {prefix}system_settings (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                [
                    CALENDAR_SETTING_KEY,
                    json.dumps(
                        {"dates": sorted(day.isoformat() for day in calendar), "updated_at": now}
                    ),
                    now,
                ],
            )
        except Exception as exc:  # noqa: BLE001 - caching must not block collection
            logger.warning("Failed to cache trading calendar: %s", exc)
        return calendar

    cached = _read_cached_calendar(d1, prefix, logger)
    if cached is not None:
        return cached
    logger.error("No trading calendar available and no cache; snapshot/close must fail closed")
    return None


def is_trading_day(
    day: date,
    logger: logging.Logger,
    calendar: set[date] | None = None,
) -> bool | None:
    """True/False for a known calendar, None when the calendar is unavailable.

    Weekends are always False and do not need the calendar.
    """
    if day.weekday() >= 5:
        return False
    if calendar is None:
        logger.warning("Trading calendar unavailable for %s", day)
        return None
    return day in calendar
