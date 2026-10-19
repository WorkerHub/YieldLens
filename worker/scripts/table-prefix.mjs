/**
 * Single source of truth for TABLE_PREFIX normalisation on the Node side.
 *
 * It must match worker/src/types.ts#getTablePrefix() and
 * python-collector/common/table_prefix.py:
 *   ""      -> ""
 *   "yl"    -> "yl_"
 *   "yl_"   -> "yl_"
 *   "123"   -> error (would generate an invalid SQL identifier)
 */

import { readFileSync } from "node:fs";

export const TABLE_PREFIX_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;

export function normalizeTablePrefix(raw) {
  const value = String(raw ?? "").trim();
  if (!value) return "";
  if (!TABLE_PREFIX_PATTERN.test(value)) {
    throw new Error(
      `Invalid TABLE_PREFIX ${JSON.stringify(value)}: must match ${TABLE_PREFIX_PATTERN}`,
    );
  }
  return value.endsWith("_") ? value : `${value}_`;
}

export function applyTablePrefix(sql, prefix) {
  return sql.replaceAll("{prefix}", prefix);
}

/**
 * Read TABLE_PREFIX from wrangler.toml so `pnpm db:migrate:*` uses the same
 * value the Worker will see at runtime when the env var is not exported.
 */
export function readWranglerPrefix(wranglerPath) {
  const text = readFileSync(wranglerPath, "utf8");
  const match = text.match(/^\s*TABLE_PREFIX\s*=\s*"([^"]*)"\s*$/m);
  return match ? match[1] : "";
}

export function resolveTablePrefix({ env = process.env, wranglerPath } = {}) {
  if (env.TABLE_PREFIX !== undefined && env.TABLE_PREFIX !== "") {
    return normalizeTablePrefix(env.TABLE_PREFIX);
  }
  if (wranglerPath) {
    return normalizeTablePrefix(readWranglerPrefix(wranglerPath));
  }
  return "";
}
