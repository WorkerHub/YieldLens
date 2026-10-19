"""A-share trading-day check.

Writing a "today" price row on a weekend or public holiday would stamp the
previous session's close with a non-trading date, so the snapshot collector
asks AKShare's Sina trading calendar first. If that call fails we fall back to
the weekday heuristic (and log it) rather than blocking the whole run.
"""

from __future__ import annotations

import logging
from datetime import date

import akshare as ak

from common.retry import call_with_retries

_calendar_cache: set[date] | None = None


def reset_cache() -> None:
    global _calendar_cache
    _calendar_cache = None


def _load_calendar(logger: logging.Logger) -> set[date] | None:
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
    except Exception as exc:  # noqa: BLE001 - fall back to weekday heuristic
        logger.warning("Trading calendar unavailable (%s); using weekday heuristic", exc)
        return None
    if df is None or df.empty or "trade_date" not in df.columns:
        logger.warning("Trading calendar returned no usable rows; using weekday heuristic")
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
    _calendar_cache = dates
    return dates


def is_trading_day(day: date, logger: logging.Logger) -> bool:
    if day.weekday() >= 5:
        return False
    calendar = _load_calendar(logger)
    if calendar is None:
        return True
    return day in calendar
