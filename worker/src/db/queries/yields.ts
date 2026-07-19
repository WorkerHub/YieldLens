import type { DividendYield } from "../../types";

export type YieldRange = "1y" | "3y" | "5y" | "10y" | "all";

export function rangeToStartDate(range: YieldRange): string | null {
  if (range === "all") return null;
  const years = { "1y": 1, "3y": 3, "5y": 5, "10y": 10 }[range];
  const d = new Date();
  d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

export async function listYields(
  db: D1Database,
  prefix: string,
  code: string,
  range: YieldRange,
): Promise<DividendYield[]> {
  const startDate = rangeToStartDate(range);
  const conditions = ["code = ?"];
  const binds: (string | number)[] = [code];

  if (startDate) {
    conditions.push("calc_date >= ?");
    binds.push(startDate);
  }

  const result = await db
    .prepare(
      `SELECT * FROM ${prefix}dividend_yields
       WHERE ${conditions.join(" AND ")}
       ORDER BY calc_date ASC`,
    )
    .bind(...binds)
    .all<DividendYield>();

  return result.results;
}

/**
 * Returns the percentage of historical ttm_yield observations at or below
 * the given current yield, i.e. "current yield is higher than X% of the
 * last N years' history".
 */
export async function ttmYieldPercentile(
  db: D1Database,
  prefix: string,
  code: string,
  currentTtmYield: number,
): Promise<number | null> {
  const result = await db
    .prepare(
      `SELECT
         COUNT(*) as total,
         SUM(CASE WHEN ttm_yield <= ? THEN 1 ELSE 0 END) as below_or_equal
       FROM ${prefix}dividend_yields
       WHERE code = ? AND ttm_yield IS NOT NULL`,
    )
    .bind(currentTtmYield, code)
    .first<{ total: number; below_or_equal: number }>();

  if (!result || result.total === 0) return null;
  return Math.round((result.below_or_equal / result.total) * 100);
}
