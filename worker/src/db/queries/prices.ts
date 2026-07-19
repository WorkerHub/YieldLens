import type { StockPrice } from "../../types";

export async function listPrices(
  db: D1Database,
  prefix: string,
  code: string,
  startDate?: string,
  endDate?: string,
): Promise<StockPrice[]> {
  const conditions = ["code = ?"];
  const binds: (string | number)[] = [code];

  if (startDate) {
    conditions.push("trade_date >= ?");
    binds.push(startDate);
  }
  if (endDate) {
    conditions.push("trade_date <= ?");
    binds.push(endDate);
  }

  const result = await db
    .prepare(
      `SELECT * FROM ${prefix}stock_prices
       WHERE ${conditions.join(" AND ")}
       ORDER BY trade_date ASC`,
    )
    .bind(...binds)
    .all<StockPrice>();

  return result.results;
}

export async function latestPrice(
  db: D1Database,
  prefix: string,
  code: string,
): Promise<StockPrice | null> {
  const result = await db
    .prepare(
      `SELECT * FROM ${prefix}stock_prices WHERE code = ? ORDER BY trade_date DESC LIMIT 1`,
    )
    .bind(code)
    .first<StockPrice>();
  return result ?? null;
}
