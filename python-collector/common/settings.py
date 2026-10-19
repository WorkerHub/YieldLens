"""Small helpers for the ``{prefix}system_settings`` key/value table.

Used for collector cursors, coverage progress and health markers so a sharded
run can resume where the previous one stopped.
"""

from __future__ import annotations

import json
from typing import Any

from common.d1_client import D1Client


def get_setting(d1: D1Client, prefix: str, key: str, default: str | None = None) -> str | None:
    rows = d1.execute_sql(
        f"SELECT value FROM {prefix}system_settings WHERE key = ?",
        [key],
    )
    return rows[0]["value"] if rows else default


def set_setting(d1: D1Client, prefix: str, key: str, value: str, now: str) -> None:
    d1.execute_sql(
        f"INSERT OR REPLACE INTO {prefix}system_settings (key, value, updated_at) VALUES (?, ?, ?)",
        [key, value, now],
    )


def get_json_setting(d1: D1Client, prefix: str, key: str) -> dict[str, Any] | None:
    raw = get_setting(d1, prefix, key)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def set_json_setting(
    d1: D1Client, prefix: str, key: str, value: dict[str, Any], now: str
) -> None:
    set_setting(d1, prefix, key, json.dumps(value, ensure_ascii=False, sort_keys=True), now)
