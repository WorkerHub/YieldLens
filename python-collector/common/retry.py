"""Retry helper for flaky third-party (AKShare) endpoints.

GitHub-hosted runners occasionally get ``Connection reset by peer`` from the
Shenzhen exchange endpoint used by ``ak.stock_info_a_code_name()``. A single
reset used to abort the whole collection job, so every upstream call now goes
through this exponential-backoff wrapper and logs the reason + attempt number.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 2.0
DEFAULT_MAX_DELAY = 30.0
DEFAULT_JITTER = 0.5


class RetryExhausted(RuntimeError):
    """Raised when an upstream call still fails after every retry."""

    def __init__(self, description: str, attempts: int, last_error: Exception):
        self.description = description
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"{description} failed after {attempts} attempts: {last_error}")


def call_with_retries(
    func: Callable[[], T],
    *,
    description: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter: float = DEFAULT_JITTER,
    logger: logging.Logger | None = None,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Call ``func`` with exponential backoff, re-raising the last error.

    Attempts are logged (1-based) so the Actions log makes the failure reason
    and retry count explicit, e.g. ``attempt 2/4 failed: Connection reset``.
    """
    log = logger or logging.getLogger(__name__)
    sleep_fn = sleep or time.sleep
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - upstream errors are varied
            last_error = exc
            if attempt >= max_attempts:
                log.error("%s failed on attempt %d/%d: %s", description, attempt, max_attempts, exc)
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1))) + random.uniform(0, jitter)
            log.warning(
                "%s failed on attempt %d/%d: %s; retrying in %.1fs",
                description,
                attempt,
                max_attempts,
                exc,
                delay,
            )
            sleep_fn(delay)

    assert last_error is not None
    raise RetryExhausted(description, max_attempts, last_error) from last_error
