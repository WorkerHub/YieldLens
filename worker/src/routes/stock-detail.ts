import { Hono } from "hono";
import { findStockByCode } from "../db/queries/stocks";
import { listDividends } from "../db/queries/dividends";
import { listPrices } from "../db/queries/prices";
import { listYields, ttmYieldPercentile, type YieldRange } from "../db/queries/yields";
import type { HonoEnv } from "../types";
import { getTablePrefix } from "../types";

export const stockDetailRoutes = new Hono<HonoEnv>();

const VALID_RANGES: YieldRange[] = ["1y", "3y", "5y", "10y", "all"];

stockDetailRoutes.get("/:code", async (c) => {
  const prefix = getTablePrefix(c.env);
  const code = c.req.param("code");
  const stock = await findStockByCode(c.env.DB, prefix, code);
  if (!stock) {
    return c.json({ error: "Stock not found" }, 404);
  }

  let ttmYieldPercentileValue: number | null = null;
  if (stock.ttm_yield !== null) {
    ttmYieldPercentileValue = await ttmYieldPercentile(c.env.DB, prefix, code, stock.ttm_yield);
  }

  return c.json({ data: { ...stock, ttm_yield_percentile: ttmYieldPercentileValue } });
});

stockDetailRoutes.get("/:code/prices", async (c) => {
  const prefix = getTablePrefix(c.env);
  const code = c.req.param("code");
  const start = c.req.query("start");
  const end = c.req.query("end");
  const data = await listPrices(c.env.DB, prefix, code, start, end);
  return c.json({ data });
});

stockDetailRoutes.get("/:code/yields", async (c) => {
  const prefix = getTablePrefix(c.env);
  const code = c.req.param("code");
  const rangeParam = c.req.query("range");
  const range: YieldRange = VALID_RANGES.includes(rangeParam as YieldRange)
    ? (rangeParam as YieldRange)
    : "5y";
  const data = await listYields(c.env.DB, prefix, code, range);
  return c.json({ data, range });
});

stockDetailRoutes.get("/:code/dividends", async (c) => {
  const prefix = getTablePrefix(c.env);
  const code = c.req.param("code");
  const data = await listDividends(c.env.DB, prefix, code);
  return c.json({ data });
});
