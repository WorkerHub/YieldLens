"""Mirrors worker/src/types.ts's getTablePrefix() so the Python collector
and the Worker agree on table names when TABLE_PREFIX is set.
"""

from __future__ import annotations

import os


def get_table_prefix() -> str:
    raw = os.environ.get("TABLE_PREFIX", "")
    if not raw:
        return ""
    return raw if raw.endswith("_") else f"{raw}_"
