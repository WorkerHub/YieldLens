# YieldLens

[English](./README.en.md) | 中文

一个轻量级的中国股票（A股）股息率查看工具，帮助你快速筛选高股息标的、追踪历史股息率走势、查看分红明细。

> 完整需求背景见 [PRD.md](./PRD.md)。当前为 MVP 第一期实现：A股排行/筛选、个股详情、历史股息率走势、分红明细、搜索，免登录只读。账号系统、自选股、提醒推送、港股通为后续迭代内容。

## 功能特性

- **股息率排行/筛选** — 全市场A股按静态/TTM股息率排序，支持行业、市值、股息率区间、连续分红年数、排除ST/*ST等筛选条件
- **个股搜索** — 按代码或名称模糊搜索
- **股息率概览** — 静态股息率、TTM股息率、当前股价、历史分位（当前股息率高于近N年多少比例的历史时间）
- **历史股息率走势图** — 1年/3年/5年/10年/全部时间范围，静态与TTM股息率叠加股价对比
- **分红历史明细** — 报告期、预案/实施状态、每股分红、股权登记日、除权除息日、派息日、股息支付率

## 技术栈

- **后端**: Cloudflare Workers + Hono.js (TypeScript)
- **数据库**: Cloudflare D1 (SQLite)
- **缓存**: Cloudflare KV
- **前端**: React 19 + Vite + Tailwind CSS v4 + Recharts
- **数据采集**: Python + AKShare，通过 GitHub Actions 定时任务写入 D1（Cloudflare Workers 无法运行 Python，详见 [PRD.md 7.2.1](./PRD.md)）
- **部署**: GitHub Actions + Wrangler

## 目录结构

```
YieldLens/
├── worker/            # Cloudflare Worker API（Hono.js + D1 + KV）
├── web/                # React 前端（Vite）
├── python-collector/   # 数据采集脚本（AKShare + D1 REST API）
├── .github/workflows/  # 数据采集定时任务
└── PRD.md              # 完整产品需求文档
```

## 本地开发

### 前置要求

- Node.js >= 22
- pnpm
- Python >= 3.10（仅数据采集脚本需要）

### 1. 安装依赖

```bash
pnpm install
```

### 2. 初始化本地数据库

```bash
cd worker
pnpm db:migrate:local   # 建表（默认读取 wrangler.toml 的 TABLE_PREFIX=YL）
pnpm db:seed:local      # 灌入样例数据（15只代表性股票，含10年模拟历史数据）

# 默认表名为 YL_stocks / YL_stock_prices / ...；
# 如需临时改用其他前缀，建表和 seed 必须带同一个值：
TABLE_PREFIX=yl pnpm db:migrate:local
TABLE_PREFIX=yl pnpm db:seed:local
```

### 3. 启动开发环境

```bash
# 回到项目根目录
pnpm dev          # 同时启动前端(5173)与Worker(8787)
pnpm dev:web      # 仅前端
pnpm dev:worker   # 仅Worker
```

访问 `http://localhost:5173` 查看应用。

### 4. 类型检查

```bash
pnpm typecheck
```

## 部署到 Cloudflare

团队所有项目共用同一个 D1 数据库，YieldLens 使用固定前缀 `YL`，表名为
`YL_stocks`、`YL_stock_prices`、`YL_dividends`、`YL_dividend_yields`、
`YL_system_settings`。**不要为 YieldLens 单独创建 D1 数据库。**

### 1. 组织级共享配置

以下配置由 GitHub 组织提供，并需对 YieldLens 仓库可见：

| 类型 | 名称 | 说明 |
|---|---|---|
| Variable | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 账户 ID |
| Variable | `D1_DATABASE_ID` | 共享 D1 数据库 ID |
| Secret | `CLOUDFLARE_API_TOKEN` | 具备 D1 读写权限的 API Token |

采集 workflow 会把 `D1_DATABASE_ID` 映射为 `CLOUDFLARE_D1_DATABASE_ID`，并固定
`TABLE_PREFIX=YL`。KV 仍需为本项目单独创建：

```bash
npx wrangler kv namespace create YIELDLENS_KV
```

### 2. 初始化生产数据库

合并到 `main` 后，在 GitHub Actions 中手动运行 **Apply D1 Schema**：

- Actions → **Apply D1 Schema** → **Run workflow**；
- `prefix` 保持默认 `YL`（`YL` / `YL_` 都表示 `YL_`）；
- 可选勾选 `dry_run` 只打印语句、不写数据库。

或使用命令行：

```bash
gh workflow run apply-schema.yml -f prefix=YL
```

该 workflow 通过 D1 HTTP API 执行 `worker/src/db/schema.sql`，逐条输出结果，
可重复运行，且不会执行 `seed.sql`。

### 3. 部署

组织级 variables 不会自动替换 `wrangler.toml` 的占位符，部署前仍需把共享
数据库的 `database_id`（以及 `account_id`、KV `id`）填入本地
`worker/wrangler.toml`，或复制一份不提交的 `wrangler.production.toml` 后用
`npx wrangler deploy --config wrangler.production.toml`。`database_name` 只是
展示名称，实际绑定以 `database_id` 为准。

```bash
pnpm deploy
```

当前仓库保留占位符，避免误提交真实 ID。

## 数据采集

数据采集是独立于 Cloudflare 的 Python 脚本，通过 GitHub Actions 定时任务运行，经 [Cloudflare D1 REST API](https://developers.cloudflare.com/api/operations/cloudflare-d1-query-database) 写入数据库（不经过 Worker）。

### 本地测试采集脚本

```bash
cd python-collector
pip install -r requirements.txt
cp .env.example .env   # 填入 Cloudflare 凭证
python collect_prices.py       # 采集股价（默认 PRICE_MODE=auto）
python collect_dividends.py    # 采集分红/基本面（默认 COLLECT_MODE=daily）

# 不依赖 Cloudflare 凭证的本地端到端验证（SQLite 模拟 D1 + AKShare fixture）：
python tools/local_e2e.py
```

价格采集模式（`PRICE_MODE`）：

- `auto`（默认）：盘中用一次全市场快照，15:05（北京时间）后自动切到收盘覆盖；
- `snapshot` / `close`：强制快照 / 收盘快照；
- `history` / `backfill`：逐股日线回退，按 `PRICE_BATCH_SIZE` 分片（默认
  history 600 只、backfill 30 只），每 10 只 checkpoint，18 分钟软截止；
  游标存 `system_settings`，可断点续跑。

分红采集模式（`COLLECT_MODE`）：`daily`（默认，最近 3 个已披露报告期）或
`backfill`（`BACKFILL_PERIODS`，默认 20 个报告期 ≈ 10 年）。

### 运行单元测试

```bash
cd python-collector
python -m unittest discover -s tests
```

### 配置 GitHub Actions

两个采集 workflow 使用组织级共享配置（见上文“部署到 Cloudflare”）：

| 类型 | 名称 | 说明 |
|---|---|---|
| 组织级 Variable | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 账户 ID |
| 组织级 Variable | `D1_DATABASE_ID` | 共享 D1 数据库 ID |
| 组织级 Secret | `CLOUDFLARE_API_TOKEN` | 需要 D1 读写权限的 API Token |

`TABLE_PREFIX` 不读取组织级变量：两个采集 workflow 固定为 `YL`，与
`worker/wrangler.toml` 的 `[vars] TABLE_PREFIX = "YL"` 保持一致。

三个 workflow 合并到默认分支（`main`）后生效：

- `.github/workflows/apply-schema.yml` — 手动执行生产建表（默认前缀 `YL`）
- `.github/workflows/collect-prices.yml` — 交易时段内每 30 分钟采集全市场快照，
  15:10 / 15:40 强制覆盖收盘价
- `.github/workflows/collect-dividends.yml` — 每日收盘后采集分红与基本面数据

两个采集 workflow 运行在团队自建 runner 上（`runs-on: [self-hosted, yieldlens]`，
机器 `P3685.local`），建表 workflow 仍使用 GitHub 托管 runner。安装位置、服务
管理、公开仓库的安全守卫与排障见 [docs/self-hosted-runner.md](./docs/self-hosted-runner.md)。

如果 `Collect Stock Prices` 在 Actions 页面被禁用，打开 **Actions → 左侧
Collect Stock Prices → Enable workflow** 重新启用（或执行
`gh workflow enable collect-prices.yml`）。

首次上线顺序：

1. **建表**：Actions → **Apply D1 Schema** → `prefix=YL`；
2. **分红/基本面回填**：**Collect Dividends and Fundamentals** →
   `mode=backfill`，`backfill_periods=20`；
3. **股价历史回填**：**Collect Stock Prices** → `mode=backfill`，重复运行直到
   `YL_system_settings.price_backfill_completed_at` 有值。

完整步骤见 [docs/collector-fix-notes.md](./docs/collector-fix-notes.md)。

## 已知限制

- **市值/行业快照接口未在生产环境验证**：`collect_dividends.py` 中通过 `ak.stock_zh_a_spot_em()` 获取市值/PE/PB/行业数据，该接口依赖东方财富的动态子域名，在开发本项目的沙盒环境中不可访问；已做 try/except 兜底，失败不影响分红数据采集，但需要在真实 GitHub Actions Runner 上验证。
- **港股通标的暂未支持**：当前仅覆盖A股，港股通的标的名单获取方式仍需调研（详见 PRD.md 第10节）。
- **账号系统、自选股、提醒推送均未实现**：属于第二期范围，当前为免登录只读MVP。
- **股价采集为交易时段内轮询而非实时**：受 GitHub Actions cron 调度精度限制，存在数分钟至数十分钟延迟。全市场主路径依赖 `ak.stock_zh_a_spot_em()` 快照接口；不可用时回退到逐股分片，覆盖需要多次运行完成。
- **分红重算仍是逐股进行**：首次 10 年全量回填 + 全市场收益率重算可能接近 55 分钟 job 上限，后续可拆分为独立任务。

## 许可证

[MIT](LICENSE)
