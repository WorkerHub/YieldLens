import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  applyTablePrefix,
  normalizeTablePrefix,
  resolveTablePrefix,
} from "./table-prefix.mjs";

const workerDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("normalizeTablePrefix mirrors the Worker and Python rules", () => {
  assert.equal(normalizeTablePrefix(""), "");
  assert.equal(normalizeTablePrefix("   "), "");
  assert.equal(normalizeTablePrefix("yl"), "yl_");
  assert.equal(normalizeTablePrefix("yl_"), "yl_");
  assert.equal(normalizeTablePrefix("_tenant"), "_tenant_");
});

test("normalizeTablePrefix rejects invalid SQL identifiers", () => {
  assert.throws(() => normalizeTablePrefix("123"), /Invalid TABLE_PREFIX/);
  assert.throws(() => normalizeTablePrefix("yl-db"), /Invalid TABLE_PREFIX/);
  assert.throws(() => normalizeTablePrefix("yl; drop table"), /Invalid TABLE_PREFIX/);
});

test("resolveTablePrefix prefers a non-empty env value", () => {
  assert.equal(resolveTablePrefix({ env: { TABLE_PREFIX: "yl" } }), "yl_");
  assert.equal(resolveTablePrefix({ env: { TABLE_PREFIX: "yl_" } }), "yl_");
});

test("applyTablePrefix replaces every placeholder", () => {
  const sql = "CREATE TABLE {prefix}stocks (...); CREATE INDEX idx_{prefix}x ON {prefix}stocks(x);";
  const applied = applyTablePrefix(sql, "yl_");
  assert.match(applied, /CREATE TABLE yl_stocks/);
  assert.match(applied, /idx_yl_x ON yl_stocks/);
  assert.ok(!applied.includes("{prefix}"));
});

test("schema generated with TABLE_PREFIX=yl is internally consistent", () => {
  const schema = readFileSync(resolve(workerDir, "src/db/schema.sql"), "utf8");
  const applied = applyTablePrefix(schema, "yl_");
  assert.match(applied, /CREATE TABLE IF NOT EXISTS yl_stocks/);
  assert.match(applied, /CREATE TABLE IF NOT EXISTS yl_stock_prices/);
  assert.match(applied, /CREATE TABLE IF NOT EXISTS yl_dividends/);
  assert.match(applied, /CREATE TABLE IF NOT EXISTS yl_dividend_yields/);
  assert.match(applied, /CREATE TABLE IF NOT EXISTS yl_system_settings/);
  assert.ok(!applied.includes("{prefix}"));
});

test("seed generated with TABLE_PREFIX=yl targets prefixed tables", () => {
  const seed = readFileSync(resolve(workerDir, "seed.sql"), "utf8");
  const applied = applyTablePrefix(seed, "yl_");
  assert.match(applied, /INSERT INTO yl_stocks/);
  assert.match(applied, /INSERT INTO yl_stock_prices/);
  assert.ok(!applied.includes("{prefix}"));
});
