export interface Env {
  DB: D1Database;
  KV: KVNamespace;
  TABLE_PREFIX: string;
  ASSETS: Fetcher;
}

export type HonoEnv = {
  Bindings: Env;
};

export function getTablePrefix(env: Env): string {
  const raw = env.TABLE_PREFIX || "";
  if (!raw) return "";
  return raw.endsWith("_") ? raw : `${raw}_`;
}

export type Market = "A" | "HK";
export type DividendStatus = "proposal" | "implemented";

export interface Stock {
  code: string;
  name: string;
  market: Market;
  exchange: string;
  industry: string;
  total_shares: number | null;
  float_shares: number | null;
  is_st: number;
  list_date: string | null;
  last_price: number | null;
  price_updated_at: string | null;
  market_cap: number | null;
  pe_ttm: number | null;
  pb: number | null;
  static_yield: number | null;
  ttm_yield: number | null;
  yield_updated_at: string | null;
  last_dividend_date: string | null;
  consecutive_dividend_years: number;
  created_at: string;
  updated_at: string;
}

export interface StockPrice {
  code: string;
  trade_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  volume: number | null;
  amount: number | null;
}

export interface Dividend {
  id: string;
  code: string;
  report_period: string;
  per_share_div: number;
  record_date: string | null;
  ex_div_date: string | null;
  pay_date: string | null;
  status: DividendStatus;
  bonus_share_ratio: number | null;
  transfer_ratio: number | null;
  payout_ratio: number | null;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface DividendYield {
  code: string;
  calc_date: string;
  price: number;
  static_yield: number | null;
  ttm_yield: number | null;
}

export interface StockListParams {
  page: number;
  pageSize: number;
  sort: "ttm_yield" | "static_yield" | "market_cap";
  order: "asc" | "desc";
  industry?: string;
  market?: Market;
  minYield?: number;
  maxYield?: number;
  minMarketCap?: number;
  maxMarketCap?: number;
  minConsecutiveYears?: number;
  excludeSt?: boolean;
}
