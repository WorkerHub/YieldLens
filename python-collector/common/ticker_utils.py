"""Stock code normalization utilities.

AKShare's functions are inconsistent about ticker format: some (e.g.
``stock_zh_a_daily``) require an ``sh``/``sz``/``bj`` prefix, while others
(e.g. ``stock_fhps_em``, ``stock_dividend_cninfo``) use the bare 6-digit
code. These helpers centralize the conversions so callers never have to
think about which function wants which format.
"""

from __future__ import annotations

import re

_BARE_CODE_RE = re.compile(r"^\d{6}$")
_PREFIXED_RE = re.compile(r"^(sh|sz|bj)(\d{6})$", re.IGNORECASE)
_SUFFIXED_RE = re.compile(r"^(\d{6})\.(sh|sz|bj)$", re.IGNORECASE)


def normalize_ticker(symbol: str) -> str:
    """Return the bare 6-digit code from any common input format.

    Accepts ``600519``, ``sh600519``, ``SH600519``, ``600519.SH``.
    Raises ``ValueError`` if the input doesn't look like an A-share code.
    """
    symbol = symbol.strip()
    if _BARE_CODE_RE.match(symbol):
        return symbol

    m = _PREFIXED_RE.match(symbol)
    if m:
        return m.group(2)

    m = _SUFFIXED_RE.match(symbol)
    if m:
        return m.group(1)

    raise ValueError(f"Unrecognized A-share ticker format: {symbol!r}")


def infer_exchange(code: str) -> str:
    """Return 'SH', 'SZ', or 'BJ' for a bare 6-digit A-share code.

    Based on standard A-share code range conventions:
    - 60xxxx, 68xxxx (STAR market), 90xxxx -> Shanghai
    - 8xxxxx, 4xxxxx, 920xxx -> Beijing Stock Exchange
    - everything else (00xxxx, 30xxxx, 20xxxx) -> Shenzhen
    """
    code = normalize_ticker(code)
    # 920xxx is the Beijing exchange code range introduced in 2024; it must be
    # checked before the generic 9xxxxx -> Shanghai rule.
    if code.startswith("920"):
        return "BJ"
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("8", "4")):
        return "BJ"
    return "SZ"


def code_to_akshare_daily_prefix(code: str) -> str:
    """Return the ``sh600519``/``sz000001`` format required by
    ``ak.stock_zh_a_daily()``.
    """
    code = normalize_ticker(code)
    exchange = infer_exchange(code)
    return f"{exchange.lower()}{code}"
