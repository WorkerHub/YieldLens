"""Numeric / date cleaning for values coming from AKShare.

AKShare returns pandas objects, so "missing" shows up in several shapes:
``None``, ``NaN``, ``NaT``, ``numpy.nan`` and sometimes the string ``"-"``.
If any of those reach the D1 REST request body, ``requests`` serialises the
JSON with ``allow_nan=False`` and fails before the request is even sent.
Everything that crosses the AKShare -> D1 boundary must go through these
helpers.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

_DATE_PREFIX_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def is_missing(value: Any) -> bool:
    """True for None / NaN / NaT / pandas.NA-like sentinels."""
    if value is None:
        return True
    # pandas.NA / pandas.NaT raise on truthiness, so handle them explicitly.
    try:
        import pandas as pd  # local import keeps this module importable on its own

        if value is pd.NA or value is pd.NaT:
            return True
        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return True
    except (ImportError, TypeError, ValueError):
        pass
    # float("nan") is != itself.
    try:
        if value != value:  # noqa: PLR0124 - deliberate NaN/NaT check
            return True
    except (TypeError, ValueError):
        pass
    try:
        return isinstance(value, float) and (math.isnan(value) or math.isinf(value))
    except TypeError:
        return False


def to_float(value: Any, default: float | None = None) -> float | None:
    """Convert an AKShare cell to a finite float, or ``default``.

    ``"-"``, empty strings, NaN, +/-Inf and unparseable values all become
    ``default``. This is the single cleaning point used by every collector.
    """
    if is_missing(value):
        return default
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text or text in {"-", "--", "—", "None", "nan", "NaN"}:
            return default
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def to_int(value: Any, default: int | None = None) -> int | None:
    number = to_float(value)
    if number is None:
        return default
    return int(number)


def clean_date(value: Any) -> str | None:
    """Return a ``YYYY-MM-DD`` string, or None for missing/invalid values.

    AKShare mixes ``datetime.date``, ``pandas.Timestamp`` (which carries a
    ``00:00:00`` time part) and plain strings. Storing the raw ``str()`` of a
    Timestamp breaks the lexicographic comparisons used for trade/ex-div
    dates, so normalise here.
    """
    if is_missing(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nat", "nan", "none"}:
        return None
    match = _DATE_PREFIX_RE.match(text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return None
