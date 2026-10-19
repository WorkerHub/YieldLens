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
pnpm db:migrate:local   # 建表
pnpm db:seed:local      # 灌入样例数据（15只代表性股票，含10年模拟历史数据）

# 使用非空表前缀时，建表和 seed 也必须带同一个前缀：
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

### 1. 创建 Cloudflare 资源

```bash
npx wrangler d1 create yieldlens
npx wrangler kv namespace create YIELDLENS_KV
```

将输出的 `database_id` 和 KV `id` 填入 `worker/wrangler.toml`（或改为通过 CI 变量注入）。

### 2. 初始化生产数据库

```bash
cd worker
pnpm db:migrate:remote
```

### 3. 部署

```bash
pnpm deploy
```

首次部署后，`worker/wrangler.toml` 中的 `account_id`、`database_id`、KV `id` 需要替换为真实值（当前为占位符，避免误提交敏感信息）。

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

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 账户 ID |
| `CLOUDFLARE_D1_DATABASE_ID` | D1 数据库 ID |
| `CLOUDFLARE_API_TOKEN` | 需要 D1 读写权限的 API Token |

在 Variables 中添加（可选）：

| Variable | 说明 |
|---|---|
| `TABLE_PREFIX` | 数据库表前缀，需与 `worker/wrangler.toml` 中的一致，默认为空 |

两个 workflow 合并到默认分支（`main`）后按各自的 cron 调度运行（cron 不会在
普通 push 时触发）：

- `.github/workflows/collect-prices.yml` — 交易时段内每 30 分钟采集全市场快照，
  15:10 / 15:40 强制覆盖收盘价
- `.github/workflows/collect-dividends.yml` — 每日收盘后采集分红与基本面数据

如果 `Collect Stock Prices` 在 Actions 页面被禁用，打开 **Actions → 左侧
Collect Stock Prices → Enable workflow** 重新启用（或执行
`gh workflow enable collect-prices.yml`）。

首次上线需要手动触发一次全量历史回填：分红任务选择 `mode=backfill`；股价任务
选择 `mode=backfill`，重复运行直到 `system_settings.price_backfill_completed_at`
有值。完整步骤见 [docs/collector-fix-notes.md](./docs/collector-fix-notes.md)。

## 已知限制

- **市值/行业快照接口未在生产环境验证**：`collect_dividends.py` 中通过 `ak.stock_zh_a_spot_em()` 获取市值/PE/PB/行业数据，该接口依赖东方财富的动态子域名，在开发本项目的沙盒环境中不可访问；已做 try/except 兜底，失败不影响分红数据采集，但需要在真实 GitHub Actions Runner 上验证。
- **港股通标的暂未支持**：当前仅覆盖A股，港股通的标的名单获取方式仍需调研（详见 PRD.md 第10节）。
- **账号系统、自选股、提醒推送均未实现**：属于第二期范围，当前为免登录只读MVP。
- **股价采集为交易时段内轮询而非实时**：受 GitHub Actions cron 调度精度限制，存在数分钟至数十分钟延迟。全市场主路径依赖 `ak.stock_zh_a_spot_em()` 快照接口；不可用时回退到逐股分片，覆盖需要多次运行完成。
- **分红重算仍是逐股进行**：首次 10 年全量回填 + 全市场收益率重算可能接近 55 分钟 job 上限，后续可拆分为独立任务。

## 许可证

[MIT](LICENSE)
