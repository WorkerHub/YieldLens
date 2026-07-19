import type { Stock, StockListParams } from "../../types";

function buildWhereClause(prefix: string, params: StockListParams): {
  where: string;
  binds: (string | number)[];
} {
  const conditions: string[] = [];
  const binds: (string | number)[] = [];

  if (params.market) {
    conditions.push("market = ?");
    binds.push(params.market);
  }
  if (params.industry) {
    conditions.push("industry = ?");
    binds.push(params.industry);
  }
  if (params.minYield !== undefined) {
    conditions.push(`${params.sort} >= ?`);
    binds.push(params.minYield);
  }
  if (params.maxYield !== undefined) {
    conditions.push(`${params.sort} <= ?`);
    binds.push(params.maxYield);
  }
  if (params.minMarketCap !== undefined) {
    conditions.push("market_cap >= ?");
    binds.push(params.minMarketCap);
  }
  if (params.maxMarketCap !== undefined) {
    conditions.push("market_cap <= ?");
    binds.push(params.maxMarketCap);
  }
  if (params.minConsecutiveYears !== undefined) {
    conditions.push("consecutive_dividend_years >= ?");
    binds.push(params.minConsecutiveYears);
  }
  if (params.excludeSt) {
    conditions.push("is_st = 0");
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
  return { where, binds };
}

const SORT_COLUMNS = new Set(["ttm_yield", "static_yield", "market_cap"]);

export async function listStocks(
  db: D1Database,
  prefix: string,
  params: StockListParams,
): Promise<Stock[]> {
  const sortCol = SORT_COLUMNS.has(params.sort) ? params.sort : "ttm_yield";
  const order = params.order === "asc" ? "ASC" : "DESC";
  const { where, binds } = buildWhereClause(prefix, params);
  const offset = (params.page - 1) * params.pageSize;

  const result = await db
    .prepare(
      `SELECT * FROM ${prefix}stocks
       ${where}
       ORDER BY ${sortCol} ${order} NULLS LAST
       LIMIT ? OFFSET ?`,
    )
    .bind(...binds, params.pageSize, offset)
    .all<Stock>();

  return result.results;
}

export async function countStocks(
  db: D1Database,
  prefix: string,
  params: StockListParams,
): Promise<number> {
  const { where, binds } = buildWhereClause(prefix, params);
  const result = await db
    .prepare(`SELECT COUNT(*) as total FROM ${prefix}stocks ${where}`)
    .bind(...binds)
    .first<{ total: number }>();
  return result?.total ?? 0;
}

export async function findStockByCode(
  db: D1Database,
  prefix: string,
  code: string,
): Promise<Stock | null> {
  const result = await db
    .prepare(`SELECT * FROM ${prefix}stocks WHERE code = ?`)
    .bind(code)
    .first<Stock>();
  return result ?? null;
}

export async function searchStocks(
  db: D1Database,
  prefix: string,
  query: string,
  limit = 20,
): Promise<Stock[]> {
  const result = await db
    .prepare(
      `SELECT * FROM ${prefix}stocks
       WHERE code LIKE ? OR name LIKE ?
       ORDER BY ttm_yield DESC NULLS LAST
       LIMIT ?`,
    )
    .bind(`${query}%`, `%${query}%`, limit)
    .all<Stock>();
  return result.results;
}

export async function distinctIndustries(
  db: D1Database,
  prefix: string,
): Promise<string[]> {
  const result = await db
    .prepare(
      `SELECT DISTINCT industry FROM ${prefix}stocks WHERE industry != '' ORDER BY industry`,
    )
    .all<{ industry: string }>();
  return result.results.map((r) => r.industry);
}
