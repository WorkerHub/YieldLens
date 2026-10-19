#!/usr/bin/env node
/**
 * Generate a prefix-applied SQL file from a `{prefix}` template.
 *
 * Usage: node scripts/build-sql.mjs <input.sql> <output.sql>
 *
 * TABLE_PREFIX is resolved in this order:
 *   1. the TABLE_PREFIX environment variable, when non-empty;
 *   2. `[vars] TABLE_PREFIX` in worker/wrangler.toml;
 *   3. empty string.
 *
 * This replaces the old `sed 's/{prefix}//g'` commands, which always produced
 * unprefixed tables even when the Worker queried `yl_stocks`.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { applyTablePrefix, resolveTablePrefix } from "./table-prefix.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const workerDir = resolve(here, "..");

const [inputArg, outputArg] = process.argv.slice(2);
if (!inputArg || !outputArg) {
  console.error("Usage: node scripts/build-sql.mjs <input.sql> <output.sql>");
  process.exit(1);
}

const input = resolve(process.cwd(), inputArg);
const output = resolve(process.cwd(), outputArg);

const prefix = resolveTablePrefix({
  env: process.env,
  wranglerPath: resolve(workerDir, "wrangler.toml"),
});

const template = readFileSync(input, "utf8");
const sql = applyTablePrefix(template, prefix);
if (sql.includes("{prefix}")) {
  console.error(`[db] ${input}: unresolved {prefix} placeholder remains`);
  process.exit(1);
}
writeFileSync(output, sql);
console.log(`[db] ${input} -> ${output} (TABLE_PREFIX=${prefix || "<empty>"})`);
