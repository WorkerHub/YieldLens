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
python collect_prices.py       # 采集股价
python collect_dividends.py    # 采集分红/基本面
```

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

两个 workflow 会在推送后按各自的 cron 调度自动运行：

- `.github/workflows/collect-prices.yml` — 交易时段内每30分钟采集股价
- `.github/workflows/collect-dividends.yml` — 每日收盘后采集分红与基本面数据

首次上线需要手动触发一次全量历史回填（见下方"接下来要做什么"）。

## 已知限制

- **市值/行业快照接口未在生产环境验证**：`collect_dividends.py` 中通过 `ak.stock_zh_a_spot_em()` 获取市值/PE/PB/行业数据，该接口依赖东方财富的动态子域名，在开发本项目的沙盒环境中不可访问；已做 try/except 兜底，失败不影响分红数据采集，但需要在真实 GitHub Actions Runner 上验证。
- **港股通标的暂未支持**：当前仅覆盖A股，港股通的标的名单获取方式仍需调研（详见 PRD.md 第10节）。
- **账号系统、自选股、提醒推送均未实现**：属于第二期范围，当前为免登录只读MVP。
- **股价采集为交易时段内轮询而非实时**：受 GitHub Actions cron 调度精度限制，存在数分钟至数十分钟延迟。

## 许可证

[MIT](LICENSE)
