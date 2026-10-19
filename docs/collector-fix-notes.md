# YieldLens 数据采集链路修复说明（2026-10-19）

依据：审查报告 `docs/code-review-2026-10-19.md`（位于分支
`tds/conv-01a15352-493c-7b1e-9de4-eb4a9890c783`；本分支暂未包含该文件）中的
阻断项 B-01 ~ B-06、Actions 日志中的上游容错问题，以及“股价任务从未触发”的
排查要求。

本轮只改数据采集链路、必要的 schema/迁移脚本和采集 workflow：

- 未改 `web/`；
- 未改静态/TTM 股息率、连续分红年数、历史分位的计算口径；
- 未提交任何真实凭证，未硬编码 Cloudflare ID。

---

## 1. 结论摘要

| 编号 | 状态 | 修复要点 |
|---|---|---|
| B-01 D1 绑定参数上限 | 已修复 | `upsert_rows()` 按列数动态计算批大小，任何语句 ≤ 100 个绑定参数；`update_rows()` 同样受约束；测试逐条断言 |
| B-02 分红报告期 | 已修复 | 从“最近已披露”报告期开始；日常固定 3 个报告期；`daily` / `backfill` 显式拆分 |
| B-03 NaN/Inf | 已修复 | 新增统一数值清洗；D1 客户端发送前校验并给出参数下标；含 NaN 送转列的测试 |
| B-04 价格增量/收盘价 | 已修复 | 删除 `trade_date` 跳过逻辑；每次重抓包含今日的窗口；收盘快照强制覆盖；`price_updated_at` 为真实更新时间 |
| B-05 全市场吞吐/超时 | 已修复 | 主路径改为一次全市场快照；逐股回退/回填按分片 + 游标断点续跑；25 分钟内结束 |
| B-06 TABLE_PREFIX | 已修复 | 建表/seed/迁移统一由 `worker/scripts/build-sql.mjs` 生成；运行时规则同步收紧；非空前缀实测通过 |
| 上游接口容错 | 已修复 | 指数退避重试；股票列表三级兜底；单只股票失败跳过并汇总；日志记录原因与尝试次数 |
| 股价任务从未触发 | 已查明并修复 | GitHub 上该 workflow 状态为 `disabled_manually`（2026-07-20 被手动禁用），已重新启用 |
| 测试与本地端到端 | 已完成 | 74 个 Python 单元测试 + SQLite 本地端到端脚本 + Node 前缀测试 |

---

## 2. 逐项改动

### B-01 D1 单查询 100 个绑定参数

**问题**：旧 `DEFAULT_BATCH_SIZE = 150`，`stocks` 21 列时一条 INSERT 有 3150 个
参数，D1 直接拒绝，写链路完全不可用。

**改动**（`python-collector/common/d1_client.py`）：

- 新增 `MAX_BOUND_PARAMS = 100`。
- `upsert_rows()`：
  - `max_rows_per_statement = 100 // len(columns)`；
  - 即使调用方传入更大的 `batch_size` 也会被钳制；
  - 发请求前 `assert len(params) <= 100`；
  - 校验表名/列名是合法 SQL 标识符，校验每行列集合一致。
- 新增 `update_rows()`：用 `CASE key WHEN ? THEN ? ...` 批量更新，参数预算按
  `2 * 列数 + 1` 计算，替代“每只股票一次 UPDATE”的 N+1 写法。
- `execute_sql()` 对任何超过 100 个参数的语句直接抛 `D1ParamError`。

**测试**（`tests/test_d1_client.py`）：

- 21 列 × 250 行：每条语句 4 行、84 个参数、63 次请求；
- 8 列 × 250 行：每条语句 12 行、96 个参数、21 次请求；
- 传入 `batch_size=1000` 会被钳制；
- 逐条断言每个请求的 `params` 长度 ≤ 100；
- 500 行 `update_rows()` 每条语句 33 行、16 次请求，参数 ≤ 100。

### B-02 分红报告期选择

**问题**：旧逻辑从“当前年 1231”开始，默认只取 1 个报告期；2026 年查询
`20261231` 在 2026 年内没有数据，日常任务基本 0 条。

**改动**（`python-collector/collect_dividends.py`）：

- `latest_disclosed_report_period(today)`：
  - 7 月 1 日及以后：当前年 `0630`（半年报陆续披露）；
  - 7 月 1 日以前：上一年 `1231`（年报截止 4 月 30 日）。
- `report_periods(years)` 从该日期开始按半年倒推，返回 `2 * years` 个报告期。
- `daily_report_periods(3)`：日常至少覆盖最近 3 个报告期。
- `COLLECT_MODE=daily|backfill`：
  - `daily` 固定 3 个报告期，忽略 `BACKFILL_PERIODS` 并打印提示；
  - `backfill` 使用 `BACKFILL_PERIODS`（默认 20，约 10 年）。
- workflow 的 `workflow_dispatch` 增加 `mode` 选择，避免默认值误用。

**测试**（`tests/test_report_periods.py`）：

- 2026-10-19 → `20260630, 20251231, 20250630`；
- 2026-04-20 → `20251231, 20250630, 20241231`；
- 10 年回填 20 个报告期、降序、无重复；
- 非法 `COLLECT_MODE` 报错。

### B-03 NaN/Inf 清洗与 D1 参数校验

**问题**：`float(NaN) or 0` 仍是 `NaN`，`requests` 在 `prepare_body()` 用
`json.dumps(..., allow_nan=False)`，整批在发请求前崩溃。

**改动**：

- 新增 `common/numeric.py`：
  - `is_missing()` 覆盖 `None`、`NaN`、`Inf`、`pd.NA`、`pd.NaT`、`"-"`；
  - `to_float()` / `to_int()` 统一清洗；
  - `clean_date()` 把 `datetime.date`、`pandas.Timestamp`、`YYYY/MM/DD` 统一为
    `YYYY-MM-DD`，避免 `Timestamp` 的 `00:00:00` 污染字典序比较。
- 分红：`per_share_div`、`bonus_share_ratio`、`transfer_ratio` 全部走
  `to_float()`；送转缺失写 0.0；`parse_dividend_status()` 对非字符串（NaN）
  返回 `proposal`。
- 价格：开高低收、成交量/额全部清洗；`close <= 0` 的行跳过。
- 基本面：总市值/PE/PB 全部清洗。
- `D1Client.execute_sql()` 在序列化前检查每个参数，NaN/Inf 抛
  `D1ParamError`，错误信息包含参数下标和 SQL 前缀。

**测试**：

- `tests/test_dividend_cleaning.py`：NaN 送转 → 0.0，NaN 状态 → proposal，
  Timestamp 日期 → `YYYY-MM-DD`，无现金分红行跳过，上游失败不中断；
- `tests/test_d1_client.py`：NaN/Inf 在 HTTP 之前被拒绝；
- `tests/test_common_utils.py`：数值/日期清洗边界。

**真实上游验证**：`stock_fhps_em("20251231")` 共 3651 行，`送转股份-送转比例`
NaN 比例 **99.62%**，`送转股份-转股比例` NaN 比例 **89.35%**。这确认 B-03 是
第一次真实数据就会触发的阻断项。

用真实 20251231 数据跑 `collect_dividends_for_period()`（D1 换成会检查
NaN/Inf 的内存替身）：上游 3651 行 → 清洗后写入 **3620 行**（31 行无现金分红
被跳过），`bonus_share_ratio=0.0` 的有 3606 行，日期统一为 `YYYY-MM-DD`，
没有 NaN/Inf 进入请求体；状态分布 `implemented=3619, proposal=1`。

### B-04 价格增量与收盘价

**问题**：旧逻辑 `(today - trade_date).days < 1` 直接跳过；盘中写入后，收盘
运行不会再修正。

**改动**（`python-collector/collect_prices.py`）：

- 删除“今天已更新”的跳过逻辑。
- 逐股回退模式每次从 `watermark - 5 天` 开始（无历史时 1 年），窗口**始终包含
  今天**；收盘后重抓今日并 `INSERT OR REPLACE` 覆盖。
- 快照主路径每次都会 upsert 今日行；`close` 模式在 15:05 后运行，写入的就是
  收盘快照。
- `stocks.last_price` / `price_updated_at` 只在拿到“今天”的行情时更新；
  没有今日行情（停牌/节假日）不会伪造更新时间。
- 09:30 前不写快照，避免把昨收当成今日行情；非交易日（含节假日）直接跳过。
- `resolve_price_mode()`：`auto` 在 15:05（北京时间）后自动切到 `close`。
- workflow 增加 15:10、15:40 两次收盘运行。

**测试**（`tests/test_collect_prices.py`）：

- watermark 等于今天时仍然重抓，`start_date = today - 5 天`；
- 快照写入今日 OHLC 并更新 `last_price`；
- NaN/Inf 的 OHLC 清洗为 `None`；
- 14:00 → snapshot，15:10 → close。

**端到端验收**：`tools/local_e2e.py` 在 `PRICE_MODE=close` 下运行后，
`stocks.last_price` 等于快照收盘价（600519=1500.0、000001=11.2、000002=8.4），
`price_updated_at` 非空。

### B-05 全市场吞吐与 25 分钟超时

**问题**：逐股调用新浪日线，约 0.65 秒/只，5000+ 只约 54 分钟，超过 25 分钟
job 上限。

**改动**：

- **主路径**：`ak.stock_zh_a_spot_em()` 一次返回全市场快照，写入当日
  `stock_prices` 并批量刷新 `stocks.last_price`。5000 只股票只需 1 次上游调用；
  D1 写入按 100 参数上限分批（`stock_prices` 8 列 → 12 行/语句，
  `stocks` 更新 → 33 行/语句）。
- **回退/回填**：保留逐股 `stock_zh_a_daily`，但：
  - `history` 模式：每日游标 `price_history_cursor:<date>`，默认 600 只/片；
  - `backfill` 模式：持久游标 `price_backfill_cursor`，默认 300 只/片；
  - 每片处理完写 `price_history_progress` / `price_backfill_progress`；
  - 下一片从游标继续，支持断点续跑；
  - 单只失败只计数并跳过，失败率 > 20% 时任务退出非 0，Actions 标红。
- workflow 增加 `concurrency`，避免上一轮未结束下一轮重叠。
- 旧的 `BACKFILL_BATCH` 分片方式仍兼容，但新配置推荐游标。

**覆盖进度**：快照模式写 `price_snapshot_progress`；逐股模式写
`price_history_progress` / `price_backfill_progress`，包含日期、模式、已处理数、
总数、本片大小、成功/失败数、是否完成。

### B-06 TABLE_PREFIX 一致性

**问题**：运行时 `getTablePrefix()` 把 `yl` 规范化为 `yl_`，但旧迁移用
`sed 's/{prefix}//g'` 永远生成无前缀表；`seed.sql` 也硬编码无前缀表名。

**改动**：

- 新增 `worker/scripts/table-prefix.mjs`：
  - `normalizeTablePrefix()` 与 Worker/Python 同一规则；
  - 只允许 `^[A-Za-z_][A-Za-z0-9_]*$`，拒绝 `123`、`yl-db` 等非法标识符；
  - `readWranglerPrefix()` 在环境变量未设置时读取 `worker/wrangler.toml` 的
    `[vars] TABLE_PREFIX`。
- 新增 `worker/scripts/build-sql.mjs`：把 `{prefix}` 模板生成实际 SQL，并检查
  没有残留占位符。
- `worker/package.json` 的 `db:migrate:local` / `db:migrate:remote` /
  `db:seed:local` 全部改用该脚本。
- `worker/seed.sql` 的所有 `INSERT INTO <table>` 改为 `{prefix}<table>`。
- `worker/src/index.ts` 的前缀校验改为 `^[A-Za-z_][A-Za-z0-9_]*$`；
  `getTablePrefix()` 增加 `trim()`，与 Python 一致。
- `python-collector/common/table_prefix.py` 增加同样的校验和 `trim()`。

**非空前缀验证**（实际执行）：

```bash
cd worker
TABLE_PREFIX=yl node scripts/build-sql.mjs src/db/schema.sql /tmp/schema-yl.sql
TABLE_PREFIX=yl node scripts/build-sql.mjs seed.sql /tmp/seed-yl.sql
sqlite3 /tmp/yl-test.db < /tmp/schema-yl.sql
sqlite3 /tmp/yl-test.db < /tmp/seed-yl.sql
```

结果：生成 `yl_stocks`、`yl_stock_prices`、`yl_dividends`、
`yl_dividend_yields`、`yl_system_settings`；seed 写入 15 只股票、1905 条价格；
没有 `{prefix}` 残留。

### 上游接口容错（Actions 日志）

**问题**：`ak.stock_info_a_code_name()` 在 GitHub Runner 上被对端
`ConnectionResetError(104)`，旧 `main()` 没有兜底，整个任务失败。

**改动**：

- 新增 `common/retry.py`：指数退避（2s、4s、8s…，带 jitter），日志写明
  `attempt N/M failed: <reason>`；重试耗尽抛 `RetryExhausted`，包含尝试次数。
- 新增 `common/stock_list.py`：
  1. 主来源 `stock_info_a_code_name()`（3 次重试）；
  2. 备用来源 `stock_zh_a_spot_em()`（3 次重试）；
  3. 再失败则使用 D1 中已有股票列表；
  4. 只有“三个来源都失败且表为空”才终止。
- 所有 AKShare 调用（分红、快照、日线、交易日历）都走重试。
- 逐股采集 `try/except`，单只失败只记录并计数；分红按报告期隔离，单个报告期
  失败不影响其他报告期。
- 同时刷新已有股票的 `name` / `is_st`（ST 标记由名称含 `ST` 推导），避免兜底
  列表长期不更新。

### 股价任务从未触发

**排查结论**：不是 cron 配置无效，也不是 YAML 语法问题。GitHub API 显示：

```json
{
  "name": "Collect Stock Prices",
  "path": ".github/workflows/collect-prices.yml",
  "state": "disabled_manually",
  "updated_at": "2026-07-20T13:55:28.000+08:00"
}
```

该 workflow 在 2026-07-20 被手动禁用，之后 40 次运行全部是分红任务。

**修复**：

- 已通过 `gh workflow enable collect-prices.yml` 重新启用；
- 新 workflow 保留 cron，并增加 15:10 / 15:40 收盘运行、`workflow_dispatch`
  的 `mode` 选择、`concurrency` 和最小 `permissions`。

**如果以后再次被禁用**，在 GitHub 仓库页面：

1. 打开 **Actions** 标签；
2. 左侧选择 **Collect Stock Prices**；
3. 点击右侧 **Enable workflow**（或页面顶部提示条里的 **Enable workflow**）。

命令行等价操作：

```bash
gh workflow enable collect-prices.yml
```

注意：cron 只在默认分支（`main`）上按计划运行；本次代码合并到 `main` 后，
新逻辑才会在定时任务中生效。

---

## 3. 报告“待验证”项的实际结论

验证时间：2026-10-19（A 股收盘后），本机直连 AKShare。

| 编号 | 结论 |
|---|---|
| V-01 | `ak.stock_fhps_em(date="20261231")` 当前不是返回空表，而是抛 `TypeError: 'NoneType' object is not subscriptable`。新逻辑日常不再查询未来报告期；单报告期异常被捕获，不会中断整批。 |
| V-02 | 收盘后 `ak.stock_zh_a_daily("sh600519", 今天, 今天)` 返回 1 行，`close=1324.97`。盘中行为无法在收盘后验证；新设计不再依赖“盘中日线是否返回今日行”，收盘主路径使用全市场快照并强制覆盖。 |
| V-03 | `stock_fhps_em("20251231")` 的 `方案进度` NaN 数为 0；代码仍保留非字符串防御。 |
| V-04 | `stock_info_a_code_name()` 返回 5575 行，其中 **351 个 920xxx** 北交所代码。已修复 `infer_exchange()`（920 → BJ），并验证 `ak.stock_zh_a_daily("bj920001", 今天, 今天)` 可用。 |
| V-05 | 旧表达式 `github.event.inputs.backfill_batch != ''` 在 schedule 事件下取值不可靠。已改为显式 `mode` 输入 + `github.event.inputs.mode || 'auto'/'daily'` 兜底，schedule 事件为空时使用默认值。 |
| V-06 | `除权除息日` 返回 `datetime.date`（如 `datetime.date(2026, 3, 5)`），不带时间；`clean_date()` 同时兼容 `date`、`Timestamp` 和字符串。 |
| V-07 | `ak.stock_zh_a_daily("bj920001", 今天, 今天)` 返回 1 行，北交所日线可用。 |
| V-08 | `stock_fhps_em("20251231")` 共 3651 行，同一代码无重复行；`600519` 在该报告期 1 行。当前 `code-report_period` 主键在该报告期安全，但不能保证所有报告期（特别分红/多次中期）永远唯一，后续可再评估。 |
| V-09 | `送转股份-送转比例` NaN 比例 99.62%，`送转股份-转股比例` NaN 比例 89.35%，确认 B-03 是真实阻断项。 |

> 说明：D1 的 100 个绑定参数限制和 `requests` 的 `allow_nan=False` 已分别由
> 官方文档/源码确认，本轮通过测试直接覆盖。

---

## 4. 本地验证命令与结果

### 4.1 环境

```bash
cd python-collector
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 4.2 Python 单元测试

```bash
cd python-collector
python -m unittest discover -s tests
```

结果：

```text
Ran 74 tests in 0.018s
OK
```

覆盖：D1 参数上限、NaN/Inf、报告期、收盘价覆盖、分片游标、股票列表兜底、
重试次数、前缀规范化、920 代码段。

### 4.3 本地端到端（SQLite 模拟 D1，无 Cloudflare 凭证）

```bash
cd python-collector
python tools/local_e2e.py
```

结果：

```text
local e2e OK
  prefix tables : yl_dividend_yields, yl_dividends, yl_stock_prices, yl_stocks, yl_system_settings
  stocks        : 3
  price rows    : 3 (close={'600519': 1500.0, '000001': 11.2, '000002': 8.4})
  dividends     : 6 (latest report period 2026-06-30)
  yield rows    : 1
  snapshot calls: 4; per-stock history calls: 0
  stock-list fallback: primary reset -> Eastmoney snapshot -> D1 rows
```

该脚本使用真实的 `D1Client.upsert_rows()` / `update_rows()` 批处理与参数校验，
只是把 HTTP 换成内存 SQLite；因此同时验证了 B-01、B-03、B-04、B-05、B-06、
报告期选择、股票列表兜底和 NaN 清洗。

### 4.4 前缀迁移/seed 验证

```bash
cd worker
TABLE_PREFIX=yl node scripts/build-sql.mjs src/db/schema.sql /tmp/schema-yl.sql
TABLE_PREFIX=yl node scripts/build-sql.mjs seed.sql /tmp/seed-yl.sql
sqlite3 /tmp/yl-test.db < /tmp/schema-yl.sql
sqlite3 /tmp/yl-test.db < /tmp/seed-yl.sql
```

结果：5 张 `yl_*` 表，`yl_stocks=15`，`yl_stock_prices=1905`。

### 4.5 TypeScript / Node

```bash
pnpm --filter worker typecheck
pnpm test:worker
```

结果：`tsc --noEmit` 通过；6 个前缀测试通过。

### 4.6 已知的非本轮问题

`pnpm lint` 仍有 1 个既有 warning：
`worker/src/db/queries/stocks.ts:3` 的 `prefix` 参数未使用（报告 L-01），本轮
未改读路径。

---

## 5. 上线前仍需用户完成的事项

### 5.1 创建 Cloudflare 资源并填 ID

```bash
npx wrangler d1 create yieldlens
npx wrangler kv namespace create YIELDLENS_KV
```

把 `database_id`、KV `id`、`account_id` 填入 `worker/wrangler.toml`（或改用
CI 变量注入）。仓库里当前仍是占位符，不要提交真实 ID。

### 5.2 配置 GitHub Secrets

仓库 **Settings → Secrets and variables → Actions**：

| 类型 | 名称 | 说明 |
|---|---|---|
| Secret | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 账户 ID |
| Secret | `CLOUDFLARE_D1_DATABASE_ID` | D1 数据库 ID |
| Secret | `CLOUDFLARE_API_TOKEN` | 具备 D1 读写权限的 Token |
| Variable（可选） | `TABLE_PREFIX` | 必须与 `worker/wrangler.toml` 的 `[vars] TABLE_PREFIX` 一致 |

**注意**：2026-10-18 的 Actions 日志显示这三个 Secret 当前为空值
（`CLOUDFLARE_ACCOUNT_ID:` 等后面没有值），即使代码修好，任务仍会在写 D1 时
失败。请先补齐。

### 5.3 初始化生产库（前缀必须一致）

```bash
cd worker
TABLE_PREFIX=yl pnpm db:migrate:remote   # 示例；不设前缀则用 wrangler.toml 的值
```

`TABLE_PREFIX` 的规则：`yl` 和 `yl_` 都表示 `yl_`；必须是合法 SQL 标识符，
不能以数字开头。建表、seed、迁移、Worker 查询、Python 采集现在使用同一规则。

### 5.4 首次全量回填

1. **分红/基本面**：GitHub Actions → **Collect Dividends and Fundamentals** →
   **Run workflow** → `mode=backfill`，`backfill_periods=20`。
2. **股价历史**：GitHub Actions → **Collect Stock Prices** →
   **Run workflow** → `mode=backfill`，`batch_size=300`。重复运行直到
   `system_settings.price_backfill_completed_at` 有值（每次从游标继续）。
3. 日常：两个 workflow 恢复 `daily` / `auto` 即可；价格任务在交易时段每 30 分钟
   跑一次快照，15:10 / 15:40 跑收盘覆盖。

### 5.5 验证生产数据

```sql
-- 今日是否写入了收盘价
SELECT code, last_price, price_updated_at
FROM yl_stocks
WHERE code = '600519';

-- 今日价格行
SELECT trade_date, close FROM yl_stock_prices
WHERE code = '600519' AND trade_date = date('now', '+8 hours');

-- 采集进度/游标
SELECT key, value FROM yl_system_settings
WHERE key LIKE 'price_%' OR key LIKE 'collect_%';
```

`price_snapshot_progress` / `price_history_progress` /
`price_backfill_progress` 可用来判断覆盖是否完成。

---

## 6. 本轮未做 / 已知限制

- **股息率口径未改**：静态股息率的完整财年口径（H-01）、连续分红年数（H-03）、
  历史序列按事件打点（H-02）仍按原逻辑，留待下一轮。
- **分红重算仍是逐股 N+1**（H-09）：首次 10 年回填 + 全市场重算可能接近
  55 分钟上限；本轮未重构为 SQL 批量计算，建议下一轮单独处理。
- **快照接口依赖东方财富**：若 `stock_zh_a_spot_em()` 不可用，会回退到逐股
  分片；此时当日全市场覆盖需要多次运行完成，进度见 `price_history_progress`。
- **盘中快照语义**：主路径在盘中写入的是实时快照价；收盘 15:10 / 15:40 会强制
  覆盖为收盘价。若产品只需要收盘价，可把盘中 cron 删除，只保留收盘运行。
- **未连接真实 D1/KV**：本轮验收使用 SQLite 模拟 D1；真实 D1 的迁移/写入需要
  用户完成 5.1 ~ 5.4 后再跑一次。
- **V-02 盘中行为未验证**：验证时已收盘；新设计不依赖该行为。

---

## 7. 与审查报告不一致之处

- 报告称 `worker/seed.sql` “硬编码无前缀表名”。本轮已把 seed 改为
  `{prefix}` 模板；旧描述只适用于修复前。
- 报告 V-01 预期 `stock_fhps_em("20261231")` “为空”；实际 AKShare 抛
  `TypeError`。两种情况下新代码都不会中断整批。
- 报告 B-05 建议“提高 timeout 或拆 job”。本轮选择更彻底的主路径：一次全市场
  快照 + 逐股回退分片，保持 25 分钟上限不变。
- 报告称股价 workflow “从未触发”；实际原因是 GitHub 上被手动禁用
  （`disabled_manually`），不是 cron/YAML 配置错误。
