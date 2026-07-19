"""Request throttling for third-party data endpoints.

AKShare's A-share functions ultimately call the same public Eastmoney /
Sina / cninfo HTTP endpoints that TradingAgents-astock's ``_em_get()``
throttles directly. Since we call AKShare rather than raw HTTP, we can't
intercept every outbound request, but we *can* throttle between our own
calls into AKShare functions, which is the practical equivalent for a
collector that iterates over thousands of stocks.
"""

from __future__ import annotations

import random
import time


class RateLimiter:
    """Enforces a minimum interval (plus jitter) between calls."""

    def __init__(self, min_interval: float = 1.0, jitter: float = 0.5):
        self.min_interval = min_interval
        self.jitter = jitter
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            elapsed = time.monotonic() - self._last_call
            target = self.min_interval + random.uniform(0, self.jitter)
            remaining = target - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_call = time.monotonic()


# Shared singletons — one per upstream data source so unrelated sources
# don't throttle each other.
eastmoney_limiter = RateLimiter(min_interval=1.0, jitter=0.5)
sina_limiter = RateLimiter(min_interval=0.5, jitter=0.3)
cninfo_limiter = RateLimiter(min_interval=0.8, jitter=0.4)
