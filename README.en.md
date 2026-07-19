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
pnpm db:migrate:local   # create tables
pnpm db:seed:local      # load sample data (15 representative stocks with 10 years of simulated history)
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

### 1. Create Cloudflare resources

```bash
npx wrangler d1 create yieldlens
npx wrangler kv namespace create YIELDLENS_KV
```

Fill the resulting `database_id` and KV `id` into `worker/wrangler.toml` (or inject them via CI variables instead).

### 2. Initialize the production database

```bash
cd worker
pnpm db:migrate:remote
```

### 3. Deploy

```bash
pnpm deploy
```

After the first deploy, replace the placeholder `account_id`, `database_id`, and KV `id` in `worker/wrangler.toml` with real values (they're placeholders by default to avoid committing sensitive info).

## Data Collection

Data collection runs as Python scripts independent of Cloudflare, scheduled via GitHub Actions, writing to the database through the [Cloudflare D1 REST API](https://developers.cloudflare.com/api/operations/cloudflare-d1-query-database) (bypassing the Worker entirely).

### Test the collector scripts locally

```bash
cd python-collector
pip install -r requirements.txt
cp .env.example .env   # fill in your Cloudflare credentials
python collect_prices.py       # collect stock prices
python collect_dividends.py    # collect dividends / fundamentals
```

### Run unit tests

```bash
cd python-collector
python -m unittest discover -s tests
```

### Configure GitHub Actions

Add these to your repo's Settings → Secrets and variables → Actions → Secrets:

| Secret | Description |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Your Cloudflare account ID |
| `CLOUDFLARE_D1_DATABASE_ID` | D1 database ID |
| `CLOUDFLARE_API_TOKEN` | API Token with D1 read/write permissions |

Add this to Variables (optional):

| Variable | Description |
|---|---|
| `TABLE_PREFIX` | Database table prefix, must match `worker/wrangler.toml`; defaults to empty |

Two workflows run automatically on their own cron schedules once pushed:

- `.github/workflows/collect-prices.yml` — collects prices every 30 minutes during trading hours
- `.github/workflows/collect-dividends.yml` — collects dividends and fundamentals daily after market close

A one-time manual full historical backfill run is needed before launch (see "What to do next" below).

## Known Limitations

- **Market cap / industry snapshot endpoint unverified in production**: `collect_dividends.py` fetches market cap/PE/PB/industry via `ak.stock_zh_a_spot_em()`, which depends on Eastmoney's numbered subdomains — unreachable from the sandbox this project was built in. It's wrapped in try/except so a failure doesn't block dividend collection, but it needs verification on a real GitHub Actions runner.
- **HK Stock Connect not yet supported**: Currently A-share only; sourcing the HK Connect eligible-stock list still needs research (see PRD.md section 10).
- **No accounts, watchlist, or alerts yet**: These are phase-2 scope; this is a read-only, no-login MVP.
- **Price collection is intraday polling, not real-time**: Limited by GitHub Actions cron scheduling precision, with delays ranging from minutes to tens of minutes.

## License

[MIT](LICENSE)
