import type { Dividend } from "../../types";

export async function listDividends(
  db: D1Database,
  prefix: string,
  code: string,
  limit = 50,
): Promise<Dividend[]> {
  const result = await db
    .prepare(
      `SELECT * FROM ${prefix}dividends
       WHERE code = ?
       ORDER BY ex_div_date DESC NULLS LAST, report_period DESC
       LIMIT ?`,
    )
    .bind(code, limit)
    .all<Dividend>();
  return result.results;
}
