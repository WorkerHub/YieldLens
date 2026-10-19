"""Table-prefix normalisation shared by the Python collector.

This must stay byte-for-byte equivalent to the Worker's ``getTablePrefix()``
and to ``worker/scripts/table-prefix.mjs`` (used by the migrate/seed scripts):

* empty / unset            -> ``""``
* ``yl``                   -> ``yl_``
* ``yl_``                  -> ``yl_``
* invalid identifiers      -> ValueError

The stricter identifier rule (must start with a letter or underscore) avoids
generating SQL such as ``123_stocks`` when ``TABLE_PREFIX=123``.
"""

from __future__ import annotations

import os
import re

_TABLE_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_table_prefix(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not _TABLE_PREFIX_RE.match(value):
        raise ValueError(
            f"Invalid TABLE_PREFIX {value!r}: must match {_TABLE_PREFIX_RE.pattern}"
        )
    return value if value.endswith("_") else f"{value}_"


def get_table_prefix() -> str:
    return normalize_table_prefix(os.environ.get("TABLE_PREFIX", ""))
