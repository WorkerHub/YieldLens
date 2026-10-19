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
| 测试与本地端到端 | 已完成 | 86 个 Python 单元测试 + SQLite 本地端到端脚本 + Node 前缀测试 |

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
- workflow 的 `workflow_dispatch` 增加 `mode` 选择，避免默认值误用；
  `BACKFILL_PERIODS` 只在 `mode=backfill` 时注入，日常运行不再打印误导性提示。
- 上游异常不再被 `collect_dividends_for_period()` 吞掉：`main()` 逐报告期计数，
  全部报告期失败时写 `system_settings.collect_dividends_last_failure` 并
  `sys.exit(1)`，不再用旧数据重算收益率后“假绿”。

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
- 成交量单位统一为**股**：快照接口的 `成交量` 是“手”，写入前 ×100；逐股日线
  本身就是股。否则同一列会因运行模式相差 100 倍。
- 基本面：总市值/PE/PB 全部清洗。
- `D1Client.execute_sql()` 在序列化前检查每个参数，NaN/Inf 抛
  `D1ParamError`，错误信息包含参数下标和 SQL 前缀。

**测试**：

- `tests/test_dividend_cleaning.py`：NaN 送转 → 0.0，NaN 状态 → proposal，
  Timestamp 日期 → `YYYY-MM-DD`，无现金分红行跳过；上游失败向上抛出，由
  `main()` 计数并在全部报告期失败时退出非 0；
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
- 交易日历优先从新浪获取，并缓存到 `system_settings.trading_calendar`。上游
  和缓存都不可用时 **fail closed**：跳过 snapshot/close 并写
  `collect_prices_last_failure`，不再用“工作日”猜测写节假日行情。
- `resolve_price_mode()`：`auto` 在 15:05（北京时间）后自动切到 `close`。
- workflow 增加 15:10、15:40 两次收盘运行。

**测试**（`tests/test_collect_prices.py`）：

- watermark 等于今天时仍然重抓，`start_date = today - 5 天`；
- 快照写入今日 OHLC 并更新 `last_price`；
- NaN/Inf 的 OHLC 清洗为 `None`；快照成交量“手”×100 后与日线“股”同量级；
- 14:00 → snapshot，15:10 → close；
- 交易日历上游失败但 D1 有缓存时继续；无缓存时 `is_trading_day()` 返回
  `None`，snapshot/close 跳过、写失败标记并退出非 0，不会伪造节假日行情。

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
  `stocks` 更新（1 个值列 + 2 个静态列）→ 32 行/语句）。
- **回退/回填**：保留逐股 `stock_zh_a_daily`，但：
  - `history` 模式：每日游标 `price_history_cursor:<date>`，默认 600 只/片；
  - `backfill` 模式：持久游标 `price_backfill_cursor`，默认 **30 只/片**
    （10 年约 2400 根日线/只，8 列 → 12 行/语句，约 200 次 D1 请求/只；
    30 只约 5–8 分钟，给 25 分钟上限留足余量）；
  - 每处理 **10 只** checkpoint 一次游标和进度，并设 **18 分钟软截止**；
    到点主动停止并保存游标，Actions SIGTERM 时最多丢最后不到 10 只；
  - 下一片从游标继续，支持断点续跑；
  - 单只失败只计数并跳过，失败代码写入 `failed_codes`；失败率 > 20% 时
    游标回退到第一只失败股票之前，下一轮重试，并退出非 0 让 Actions 标红；
  - 不再保留旧的 `BACKFILL_BATCH` 切片，避免它与全局游标互相冲突。
- workflow 增加 `concurrency`，避免上一轮未结束下一轮重叠；`mode=backfill`
  时默认片大小自动用 30，`history` 用 600。

**覆盖进度**：快照模式写 `price_snapshot_progress`；逐股模式写
`price_history_progress` / `price_backfill_progress`，包含日期、模式、已处理数、
总数、本片大小、成功/失败数、`failed_codes`、停止原因和是否完成。

### 收益率重算的续跑（H-09 的采集侧缓解）

**问题**：回填后每只股票会返回约 2400 行价格，5000 只股票 × 多次 D1 请求，
55 分钟分红 job 很可能跑不完；旧逻辑没有游标，超时后从第一只重来。

**改动**（`python-collector/collect_dividends.py`）：

- `run_yield_recompute()`：默认 500 只/片、40 分钟软截止、每 20 只 checkpoint；
- 游标 `yield_recompute_cursor`、进度 `yield_recompute_progress` 写入
  `system_settings`；完成时写 `yield_recompute_completed_at`；
- 只对 `status='implemented'` 的股票重算，减少无效请求；
- 单只失败只记录并继续，`failed_codes` 写入进度。

这仍是逐股 N+1 实现（H-09 的 SQL 批量化留待下一轮），但不会再“永远从第一只
开始”。

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
Ran 86 tests in 0.03s
OK
```

> unittest 的 “Ran … in …” 只统计测试执行时间，不包含导入 pandas/akshare 的
> 时间；整条命令的实际耗时约 1 秒。

覆盖：D1 参数上限、NaN/Inf、报告期、收盘价覆盖、分片 checkpoint/软截止、
失败游标回退、收益率重算续跑、交易日历 fail-closed、股票列表兜底、重试次数、
前缀规范化、920 代码段。

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
  price rows    : 7 (close={'600519': 1500.0, '000001': 11.2, '000002': 8.4})
  dividends     : 6 (latest report period 2026-06-30)
  yield rows    : 1
  intraday 1490 -> close 1500 overwrite: OK
  volume unit   : snapshot=1000.0, daily=1000.0 shares
  backfill cursor: 000002 after 2 one-stock runs
  snapshot calls: 8; per-stock history calls: 2
  stock-list fallback: primary reset -> Eastmoney snapshot -> D1 rows
```

该脚本使用真实的 `D1Client.upsert_rows()` / `update_rows()` 批处理，并在
`LocalD1.execute_sql()` 内显式调用 `_validate_params()`，所以 100 参数上限和
NaN/Inf 拒绝在 e2e 中也会真实执行；只是把 HTTP 换成内存 SQLite。日期固定为
2026-10-19 注入 `main(today=...)`，不再依赖运行当天。脚本覆盖 B-01、B-03、
B-04（盘中 1490 → 收盘 1500）、B-05（快照 + backfill 游标续跑）、B-06、报告期
选择、成交量单位、股票列表兜底和 NaN 清洗。

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

### 5.1 准备 Cloudflare 资源并填 ID

D1 使用团队共享数据库，**不要再执行 `wrangler d1 create`**；只需为本项目
创建 KV：

```bash
npx wrangler kv namespace create YIELDLENS_KV
```

部署 Worker 时把共享 `database_id`、KV `id`、`account_id` 填入本地
`worker/wrangler.toml`（或使用不提交的 `wrangler.production.toml`）。仓库里
当前仍是占位符，不要提交真实 ID。共享 D1 的建表用 5.3 的 workflow，不需要
wrangler 的 `database_id`。

### 5.2 配置组织级 Variables / Secrets

组织已提供共享配置，YieldLens 不再需要仓库级 Secret：

| 类型 | 名称 | 说明 |
|---|---|---|
| Variable | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 账户 ID（组织级，所有仓库共用） |
| Variable | `D1_DATABASE_ID` | 共享 D1 数据库 ID；workflow 内映射为 `CLOUDFLARE_D1_DATABASE_ID` |
| Secret | `CLOUDFLARE_API_TOKEN` | 具备 D1 读写权限的 Token（组织级） |

`TABLE_PREFIX` **不使用**组织级变量：两个采集 workflow 固定为 `YL`，与
`worker/wrangler.toml` 的 `[vars] TABLE_PREFIX = "YL"` 一致。即使组织里存在
同名变量也不会被采集任务使用，避免共享数据库下串前缀。

**核验结果**：本机 `gh` 账号只有 `read:org` / `repo` / `workflow` scope，
`gh variable list --org WorkerHub` 和 `gh secret list --org WorkerHub` 均返回
403（需要 `admin:org`），无法从 CLI 读取组织级名称与可见性；仓库级
variables/secrets 列表为空，说明没有仓库级覆盖。请组织管理员在 GitHub 组织
**Settings → Secrets and variables → Actions** 中确认上述名称存在，并且对
YieldLens 仓库可见。

### 5.3 初始化生产库（共享 D1 + `YL` 前缀）

推荐使用新增的 **Apply D1 Schema** workflow（`.github/workflows/apply-schema.yml`）：

1. 合并到 `main` 后，打开 Actions → **Apply D1 Schema** → **Run workflow**；
2. `prefix` 保持默认 `YL`（`YL` 和 `YL_` 都会归一化为 `YL_`）；
3. 可选勾选 `dry_run`，只打印语句、不写数据库；
4. 日志逐条输出 `OK` / `FAIL` 与失败原因；任一语句失败时 job 失败。

等价命令：

```bash
gh workflow run apply-schema.yml -f prefix=YL
```

该 workflow 通过 D1 HTTP API 执行 `worker/src/db/schema.sql`，不依赖
`wrangler.toml` 里的占位 ID，也不会执行 `seed.sql`。schema 全部是
`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`，可重复运行。

本地仍可用 wrangler 验证（生产不要执行 seed）：

```bash
cd worker
TABLE_PREFIX=YL pnpm db:migrate:local
TABLE_PREFIX=YL pnpm db:seed:local   # 仅本地样例数据
```

`TABLE_PREFIX` 的规则：`YL` 和 `YL_` 都表示 `YL_`；必须是合法 SQL 标识符，
不能以数字开头。建表、seed、迁移、Worker 查询、Python 采集使用同一规则。

### 5.4 首次全量回填

1. **建表**：GitHub Actions → **Apply D1 Schema** → **Run workflow**，
   `prefix=YL`（默认值）。
2. **分红/基本面**：GitHub Actions → **Collect Dividends and Fundamentals** →
   **Run workflow** → `mode=backfill`，`backfill_periods=20`。
3. **股价历史**：GitHub Actions → **Collect Stock Prices** →
   **Run workflow** → `mode=backfill`，`batch_size` 留空即用 30 只/片。
   每轮在 18 分钟软截止或处理完当前片后保存游标，重复运行直到
   `YL_system_settings.price_backfill_completed_at` 有值（每次从游标继续）。
4. 日常：两个 workflow 恢复 `daily` / `auto` 即可；价格任务在交易时段每 30 分钟
   跑一次快照，15:10 / 15:40 跑收盘覆盖。

### 5.5 验证生产数据

```sql
-- 今日是否写入了收盘价
SELECT code, last_price, price_updated_at
FROM YL_stocks
WHERE code = '600519';

-- 今日价格行
SELECT trade_date, close FROM YL_stock_prices
WHERE code = '600519' AND trade_date = date('now', '+8 hours');

-- 采集进度/游标
SELECT key, value FROM YL_system_settings
WHERE key LIKE 'price_%' OR key LIKE 'yield_%' OR key LIKE 'collect_%' OR key = 'trading_calendar';
```

`price_snapshot_progress` / `price_history_progress` /
`price_backfill_progress` / `yield_recompute_progress` 可用来判断覆盖是否完成。

---

## 6. 本轮未做 / 已知限制

- **股息率口径未改**：静态股息率的完整财年口径（H-01）、连续分红年数（H-03）、
  历史序列按事件打点（H-02）仍按原逻辑，留待下一轮。
- **分红重算仍是逐股 N+1**（H-09）：本轮已加 `yield_recompute_cursor`、
  500 只/片、40 分钟软截止和每 20 只 checkpoint，不会因超时永远从头开始；
  但 SQL 批量化仍未做，首次全量回填后的收益率重算需要分多天完成。
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

---

## 8. 共享 D1 与组织级配置（`YL` 前缀）

### 8.1 背景与配置来源

Cloudflare 免费额度只有 10 个 D1 数据库，团队决定所有项目共用一个 D1，
每个项目使用独立表前缀。YieldLens 的前缀固定为 `YL`，最终表名为
`YL_stocks`、`YL_stock_prices`、`YL_dividends`、`YL_dividend_yields`、
`YL_system_settings`。

| 类型 | 名称 | 用途 |
|---|---|---|
| 组织级 Variable | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 账户 ID |
| 组织级 Variable | `D1_DATABASE_ID` | 共享 D1 数据库 ID |
| 组织级 Secret | `CLOUDFLARE_API_TOKEN` | D1 读写 Token |

两个采集 workflow 的环境变量已改为：

```yaml
CLOUDFLARE_ACCOUNT_ID: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}
CLOUDFLARE_D1_DATABASE_ID: ${{ vars.D1_DATABASE_ID }}
CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
TABLE_PREFIX: "YL"
```

注意组织级变量名是 `D1_DATABASE_ID`，不是 `CLOUDFLARE_D1_DATABASE_ID`；
workflow 只做名称映射，Python 采集器仍然读取 `CLOUDFLARE_D1_DATABASE_ID`。
`TABLE_PREFIX` 故意不读组织级变量：共享数据库下每个项目的前缀必须独立，
读取组织级同名变量会让所有项目串表。

### 8.2 三处前缀归一化一致

| 位置 | 输入 `YL` 的结果 | 实现 |
|---|---|---|
| Worker 运行时 | `YL_` | `worker/src/types.ts#getTablePrefix()` |
| Python 采集器 | `YL_` | `python-collector/common/table_prefix.py` |
| 建表/seed 脚本 | `YL_` | `worker/scripts/table-prefix.mjs` |

`worker/wrangler.toml` 的 `[vars] TABLE_PREFIX` 已改为 `"YL"`，因此 Worker
部署后查询的就是 `YL_*` 表。Node 与 Python 测试都增加了 `YL -> YL_` 断言。

### 8.3 生产建表入口

新增 `.github/workflows/apply-schema.yml`：

- `workflow_dispatch` 手动触发，`prefix` 默认 `YL`，可选 `dry_run`；
- 使用组织级 variables/secret，通过 D1 HTTP API 写入，不依赖
  `wrangler.toml` 的占位 ID；
- 调用 `python-collector/tools/apply_schema.py`，复用
  `common/d1_client.py` 的鉴权、重试与错误处理；
- 逐条执行 `worker/src/db/schema.sql` 并打印 `OK` / `FAIL` 及失败原因；
- 只执行 schema，不执行 `worker/seed.sql`；
- schema 本身是 `IF NOT EXISTS`，可重复运行。

本地无凭证检查生成的 SQL：

```bash
cd python-collector
python tools/apply_schema.py --prefix YL --dry-run
```

### 8.4 Worker 部署仍需要 `database_id`

组织级 variables 不会自动替换 `wrangler.toml` 中的占位符，本轮也没有改部署
链路。部署前仍需把共享数据库的 `database_id` 注入 Worker 配置：

1. 从组织设置或 Cloudflare 控制台取得 `D1_DATABASE_ID`（有 `admin:org` 权限
   时可执行 `gh variable get D1_DATABASE_ID --org WorkerHub`）；
2. 在本地把 `worker/wrangler.toml` 的 `account_id` / `database_id` 换成真实值
   后执行 `pnpm deploy`，部署完成后还原占位符；或复制一份**不提交**的
   `worker/wrangler.production.toml`，填入真实 ID 后执行
   `cd worker && npx wrangler deploy --config wrangler.production.toml`；
3. KV namespace ID 仍需单独填写（共享 D1 不影响 KV）；
4. 不要把真实 account ID / database ID 提交到仓库。

有 `admin:org` 权限时，可以从组织变量生成临时配置（KV id 仍需手动替换）：

```bash
D1_ID=$(gh variable get D1_DATABASE_ID --org WorkerHub)
ACCOUNT_ID=$(gh variable get CLOUDFLARE_ACCOUNT_ID --org WorkerHub)
sed -e "s/CLOUDFLARE_ACCOUNT_ID_PLACEHOLDER/${ACCOUNT_ID}/" \
    -e "s/D1_DATABASE_ID_PLACEHOLDER/${D1_ID}/" \
    worker/wrangler.toml > worker/wrangler.production.toml
# 把 worker/wrangler.production.toml 里的 KV_NAMESPACE_ID_PLACEHOLDER
# 替换成真实 KV id，然后：
cd worker && npx wrangler deploy --config wrangler.production.toml
rm wrangler.production.toml
```

`database_name` 只是创建/展示用的名称，实际绑定以 `database_id` 为准。

### 8.5 本轮验证

- `python -m unittest discover -s tests`：通过；
- `python tools/local_e2e.py`：通过；
- `pnpm test:worker`、`pnpm --filter worker typecheck`、`pnpm build`：通过；
- `TABLE_PREFIX=YL` 生成 schema 与 seed，检查到 `YL_*` 表名且无
  `{prefix}` 残留；
- 未对生产 D1 执行任何写入；组织级变量/密钥名称因权限不足未能从 CLI 核验，
  已在 5.2 说明。
