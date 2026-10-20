# YieldLens

[中文](./README.md) | English

A lightweight dividend-yield tracking tool for Chinese A-share stocks — quickly screen high-yield stocks, track historical dividend yield trends, and view dividend payout history.

> See [PRD.md](./PRD.md) (Chinese) for the full product requirements background. This is the MVP (first-phase) implementation: A-share ranking/filtering, stock detail, historical yield trends, dividend history, and search — read-only, no login required. Accounts, watchlist, and notification alerts are planned for a later phase.

## Features

- **Dividend Yield Ranking/Filtering** — Sort the entire A-share market by static/TTM dividend yield, with filters for industry, market cap, yield range, consecutive dividend years, and ST/*ST exclusion
- **Stock Search** — Fuzzy search by code or name
- **Yield Overview** — Static yield, TTM (trailing-twelve-month) yield, current price, and historical percentile (how the current yield compares to the last N years of history)
- **Historical Yield Trend Chart** — 1/3/5/10-year and all-time ranges, static and TTM yield overlaid with price
- **Dividend History Detail** — Report period, proposal/implemented status, per-share dividend, record date, ex-dividend date, payment date, payout ratio

## Tech Stack

- **Backend**: Cloudflare Workers + Hono.js (TypeScript)
- **Database**: Cloudflare D1 (SQLite)
- **Cache**: Cloudflare KV
- **Frontend**: React 19 + Vite + Tailwind CSS v4 + Recharts
- **Data Collection**: Python + AKShare, scheduled via GitHub Actions and written to D1 (Cloudflare Workers can't run Python — see [PRD.md](./PRD.md) section 7.2.1)
- **Deployment**: GitHub Actions + Wrangler

## Project Structure

```
YieldLens/
├── worker/            # Cloudflare Worker API (Hono.js + D1 + KV)
├── web/                # React frontend (Vite)
├── python-collector/   # Data collection scripts (AKShare + D1 REST API)
├── .github/workflows/  # Scheduled data collection jobs
└── PRD.md              # Full product requirements doc (Chinese)
```

## Local Development

### Prerequisites

- Node.js >= 22
- pnpm
- Python >= 3.10 (only needed for the data collection scripts)

### 1. Install dependencies

```bash
pnpm install
```

### 2. Initialize the local database

```bash
cd worker
pnpm db:migrate:local   # create tables (uses TABLE_PREFIX=YL from wrangler.toml)
pnpm db:seed:local      # load sample data (15 representative stocks with 10 years of simulated history)

# Default table names are YL_stocks / YL_stock_prices / ...;
# to try a different prefix locally, pass the same value to both commands:
TABLE_PREFIX=yl pnpm db:migrate:local
TABLE_PREFIX=yl pnpm db:seed:local
```

### 3. Start the dev servers

```bash
# from the project root
pnpm dev          # starts both frontend (5173) and Worker (8787)
pnpm dev:web      # frontend only
pnpm dev:worker   # Worker only
```

Visit `http://localhost:5173` to view the app.

### 4. Type checking

```bash
pnpm typecheck
```

## Deploying to Cloudflare

All team projects share one D1 database. YieldLens uses the fixed prefix `YL`,
so its tables are `YL_stocks`, `YL_stock_prices`, `YL_dividends`,
`YL_dividend_yields`, and `YL_system_settings`. **Do not create a separate D1
database for YieldLens.**

### 1. Organization-level shared configuration

These are provided by the GitHub organization and must be visible to the
YieldLens repository:

| Type | Name | Purpose |
|---|---|---|
| Variable | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID |
| Variable | `D1_DATABASE_ID` | Shared D1 database ID |
| Secret | `CLOUDFLARE_API_TOKEN` | API token with D1 read/write access |

The collector workflows map `D1_DATABASE_ID` to
`CLOUDFLARE_D1_DATABASE_ID` and pin `TABLE_PREFIX=YL`. KV is still
project-specific:

```bash
npx wrangler kv namespace create YIELDLENS_KV
```

### 2. Initialize the production database

After the changes are on `main`, run **Apply D1 Schema** manually from GitHub
Actions:

- Actions → **Apply D1 Schema** → **Run workflow**;
- keep `prefix` at its default `YL` (`YL` and `YL_` both mean `YL_`);
- optionally enable `dry_run` to print statements without writing.

Or from the command line:

```bash
gh workflow run apply-schema.yml -f prefix=YL
```

The workflow executes `worker/src/db/schema.sql` through the D1 HTTP API,
prints a result for every statement, is safe to re-run, and never applies
`seed.sql`.

### 3. Deploy

Organization variables are not substituted into `wrangler.toml`, so the Worker
still needs the shared `database_id` before deployment. Fill the real
`account_id`, `database_id`, and KV `id` into `worker/wrangler.toml` locally,
or copy it to an untracked `wrangler.production.toml` and run
`npx wrangler deploy --config wrangler.production.toml`. `database_name` is
display-only; the binding is driven by `database_id`.

```bash
pnpm deploy
```

The repository keeps placeholders in `wrangler.toml` to avoid committing real
IDs.

## Data Collection

Data collection runs as Python scripts independent of Cloudflare, scheduled via GitHub Actions, writing to the database through the [Cloudflare D1 REST API](https://developers.cloudflare.com/api/operations/cloudflare-d1-query-database) (bypassing the Worker entirely).

### Test the collector scripts locally

```bash
cd python-collector
pip install -r requirements.txt
cp .env.example .env   # fill in your Cloudflare credentials
python collect_prices.py       # collect stock prices (PRICE_MODE=auto by default)
python collect_dividends.py    # collect dividends / fundamentals (COLLECT_MODE=daily)

# Credential-free end-to-end check (SQLite-backed D1 + AKShare fixtures):
python tools/local_e2e.py
```

Price modes (`PRICE_MODE`): `auto` (full-market snapshot intraday, `close` after
15:05 Beijing time), `snapshot`, `close`, `history`, `backfill`. Per-stock
`history`/`backfill` runs are sharded (default 600 history / 30 backfill stocks),
checkpointed every 10 stocks with an 18-minute soft deadline, and resume from a
cursor in `system_settings`.

Dividend modes (`COLLECT_MODE`): `daily` (last three disclosed report periods)
or `backfill` (`BACKFILL_PERIODS`, default 20 periods ≈ 10 years).

### Run unit tests

```bash
cd python-collector
python -m unittest discover -s tests
```

### Configure GitHub Actions

Both collector workflows use the organization-level shared configuration
described in “Deploying to Cloudflare”:

| Type | Name | Purpose |
|---|---|---|
| Org Variable | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID |
| Org Variable | `D1_DATABASE_ID` | Shared D1 database ID |
| Org Secret | `CLOUDFLARE_API_TOKEN` | API token with D1 read/write access |

`TABLE_PREFIX` is deliberately not read from an organization variable: both
collector workflows pin it to `YL`, matching
`worker/wrangler.toml`'s `[vars] TABLE_PREFIX = "YL"`.

Three workflows take effect once the changes are on the default branch
(`main`):

- `.github/workflows/apply-schema.yml` — manual production schema application (default prefix `YL`)
- `.github/workflows/collect-prices.yml` — full-market snapshot every 30 minutes during trading hours, with forced close-price runs at 15:10 / 15:40
- `.github/workflows/collect-dividends.yml` — dividends and fundamentals daily after market close

Both collector workflows run on the team's self-hosted runner
(`runs-on: [self-hosted, yieldlens]`, machine `P3685.local`); the schema
workflow stays on a GitHub-hosted runner. Installation, service management,
the public-repository guard and troubleshooting are documented in
[docs/self-hosted-runner.md](./docs/self-hosted-runner.md).

If `Collect Stock Prices` was disabled, open **Actions → Collect Stock Prices →
Enable workflow** (or run `gh workflow enable collect-prices.yml`).

First-launch order:

1. **Create tables**: Actions → **Apply D1 Schema** → `prefix=YL`;
2. **Dividend/fundamental backfill**: **Collect Dividends and Fundamentals** →
   `mode=backfill`, `backfill_periods=20`;
3. **Price history backfill**: **Collect Stock Prices** → `mode=backfill`,
   re-run until `YL_system_settings.price_backfill_completed_at` is set.

See [docs/collector-fix-notes.md](./docs/collector-fix-notes.md) for details.

## Known Limitations

- **Market cap / industry snapshot endpoint unverified in production**: `collect_dividends.py` fetches market cap/PE/PB/industry via `ak.stock_zh_a_spot_em()`, which depends on Eastmoney's numbered subdomains — unreachable from the sandbox this project was built in. It's wrapped in try/except so a failure doesn't block dividend collection, but it needs verification on a real GitHub Actions runner.
- **HK Stock Connect not yet supported**: Currently A-share only; sourcing the HK Connect eligible-stock list still needs research (see PRD.md section 10).
- **No accounts, watchlist, or alerts yet**: These are phase-2 scope; this is a read-only, no-login MVP.
- **Price collection is intraday polling, not real-time**: Limited by GitHub Actions cron scheduling precision, with delays ranging from minutes to tens of minutes.

## License

[MIT](LICENSE)
