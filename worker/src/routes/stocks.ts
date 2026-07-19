import { Hono } from "hono";
import { countStocks, distinctIndustries, listStocks, searchStocks } from "../db/queries/stocks";
import { getCached, rankingCacheKey, setCached } from "../services/cache";
import type { HonoEnv, StockListParams } from "../types";
import { getTablePrefix } from "../types";

export const stockRoutes = new Hono<HonoEnv>();

function parseListParams(query: Record<string, string | undefined>): StockListParams {
  const page = Math.max(1, Number.parseInt(query.page ?? "1", 10) || 1);
  const pageSize = Math.min(200, Math.max(1, Number.parseInt(query.pageSize ?? "50", 10) || 50));
  const sort = (["ttm_yield", "static_yield", "market_cap"].includes(query.sort ?? "")
    ? query.sort
    : "ttm_yield") as StockListParams["sort"];
  const order = query.order === "asc" ? "asc" : "desc";

  return {
    page,
    pageSize,
    sort,
    order,
    industry: query.industry || undefined,
    market: query.market === "HK" ? "HK" : query.market === "A" ? "A" : undefined,
    minYield: query.min_yield ? Number.parseFloat(query.min_yield) : undefined,
    maxYield: query.max_yield ? Number.parseFloat(query.max_yield) : undefined,
    minMarketCap: query.min_market_cap ? Number.parseFloat(query.min_market_cap) : undefined,
    maxMarketCap: query.max_market_cap ? Number.parseFloat(query.max_market_cap) : undefined,
    minConsecutiveYears: query.min_consecutive_years
      ? Number.parseInt(query.min_consecutive_years, 10)
      : undefined,
    excludeSt: query.exclude_st === "true",
  };
}

stockRoutes.get("/", async (c) => {
  const prefix = getTablePrefix(c.env);
  const query = c.req.query();
  const params = parseListParams(query);

  const cacheKey = rankingCacheKey(params as unknown as Record<string, unknown>);
  const cached = await getCached<{ data: unknown[]; total: number; page: number; pageSize: number }>(
    c.env.KV,
    cacheKey,
  );
  if (cached) {
    return c.json(cached);
  }

  const [data, total] = await Promise.all([
    listStocks(c.env.DB, prefix, params),
    countStocks(c.env.DB, prefix, params),
  ]);

  const payload = { data, total, page: params.page, pageSize: params.pageSize };
  await setCached(c.env.KV, cacheKey, payload);
  return c.json(payload);
});

stockRoutes.get("/search", async (c) => {
  const prefix = getTablePrefix(c.env);
  const q = c.req.query("q");
  if (!q || q.trim().length === 0) {
    return c.json({ data: [] });
  }
  const data = await searchStocks(c.env.DB, prefix, q.trim());
  return c.json({ data });
});

stockRoutes.get("/industries", async (c) => {
  const prefix = getTablePrefix(c.env);
  const data = await distinctIndustries(c.env.DB, prefix);
  return c.json({ data });
});
