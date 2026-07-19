-- YieldLens database schema
-- Table names use {prefix} placeholder, replaced at deploy/seed time with the
-- TABLE_PREFIX env var value (see getTablePrefix() in src/types.ts).
-- MVP scope only: market master data, price history, dividends, and the
-- precomputed yield time series. Accounts/watchlist/alerts are phase 2.

-- ── Market master data ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS {prefix}stocks (
  code         TEXT PRIMARY KEY,          -- 6-digit A-share code, e.g. '600519'
  name         TEXT NOT NULL,             -- stock name, e.g. '贵州茅台'
  market       TEXT NOT NULL DEFAULT 'A', -- 'A' only for MVP; 'HK' phase-2
  exchange     TEXT NOT NULL DEFAULT '',  -- 'SH', 'SZ', 'BJ'
  industry     TEXT NOT NULL DEFAULT '',  -- 申万一级行业, populated by dividend collector
  total_shares REAL,                      -- total shares outstanding (亿股)
  float_shares REAL,                      -- float shares (亿股)
  is_st        INTEGER NOT NULL DEFAULT 0, -- 1 if ST/*ST
  list_date    TEXT,                      -- listing date YYYY-MM-DD
  last_price   REAL,                      -- most recent close price (updated by price collector)
  price_updated_at TEXT,                  -- ISO timestamp of last price update
  market_cap   REAL,                      -- total market cap in 亿元 (snapshot, updated daily)
  pe_ttm       REAL,                      -- PE TTM (snapshot)
  pb           REAL,                      -- PB (snapshot)
  static_yield REAL,                      -- pre-computed static dividend yield (%)
  ttm_yield    REAL,                      -- pre-computed TTM dividend yield (%)
  yield_updated_at TEXT,                  -- ISO timestamp when yields were last computed
  last_dividend_date TEXT,                -- ex-dividend date of most recent dividend
  consecutive_dividend_years INTEGER NOT NULL DEFAULT 0, -- years of consecutive dividends
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_{prefix}stocks_ttm_yield
  ON {prefix}stocks(ttm_yield DESC);
CREATE INDEX IF NOT EXISTS idx_{prefix}stocks_static_yield
  ON {prefix}stocks(static_yield DESC);
CREATE INDEX IF NOT EXISTS idx_{prefix}stocks_industry
  ON {prefix}stocks(industry);
CREATE INDEX IF NOT EXISTS idx_{prefix}stocks_market_cap
  ON {prefix}stocks(market_cap DESC);

-- ── Price history ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS {prefix}stock_prices (
  code         TEXT NOT NULL,             -- FK to {prefix}stocks.code
  trade_date   TEXT NOT NULL,             -- YYYY-MM-DD
  open         REAL,
  high         REAL,
  low          REAL,
  close        REAL NOT NULL,
  volume       REAL,                      -- shares traded
  amount       REAL,                      -- turnover in CNY
  PRIMARY KEY (code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_{prefix}stock_prices_code_date
  ON {prefix}stock_prices(code, trade_date DESC);

-- ── Dividend records ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS {prefix}dividends (
  id                   TEXT PRIMARY KEY,  -- UUID
  code                 TEXT NOT NULL,     -- FK to {prefix}stocks.code
  report_period        TEXT NOT NULL,     -- e.g. '2023-12-31' (fiscal year end)
  per_share_div        REAL NOT NULL DEFAULT 0, -- cash dividend per share (税前, CNY)
  record_date          TEXT,             -- 股权登记日 YYYY-MM-DD
  ex_div_date          TEXT,             -- 除权除息日 YYYY-MM-DD
  pay_date             TEXT,             -- 派息日 YYYY-MM-DD
  status               TEXT NOT NULL DEFAULT 'implemented', -- 'proposal' | 'implemented'
  bonus_share_ratio    REAL DEFAULT 0,   -- 送股比例 (per 10 shares)
  transfer_ratio       REAL DEFAULT 0,   -- 转增比例 (per 10 shares)
  payout_ratio         REAL,             -- 分红支付率 (%)
  source               TEXT NOT NULL DEFAULT 'akshare_fhps_em', -- data source tag
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_{prefix}dividends_code_date
  ON {prefix}dividends(code, ex_div_date DESC);
CREATE INDEX IF NOT EXISTS idx_{prefix}dividends_report_period
  ON {prefix}dividends(report_period);

-- ── Pre-computed yield time series ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS {prefix}dividend_yields (
  code          TEXT NOT NULL,
  calc_date     TEXT NOT NULL,            -- YYYY-MM-DD, typically the ex_div_date
  price         REAL NOT NULL,            -- closing price on calc_date
  static_yield  REAL,                     -- last full fiscal year div / price * 100
  ttm_yield     REAL,                     -- trailing 12-month div / price * 100
  PRIMARY KEY (code, calc_date)
);

CREATE INDEX IF NOT EXISTS idx_{prefix}dividend_yields_code
  ON {prefix}dividend_yields(code, calc_date DESC);

-- ── Misc settings / collector health markers ───────────────────────────────

CREATE TABLE IF NOT EXISTS {prefix}system_settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
