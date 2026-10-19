# 首次 D1 建表与真实采集记录（2026-10-19）

## 结论摘要

- **建表成功**：共享 D1 中已创建 5 张 `YL_` 表，13 条语句全部 OK；重复执行同样 13/13 OK，确认幂等。
- **D1 读写链路已验证**：分红采集在失败前成功执行了 `SELECT code, name, is_st FROM YL_stocks`，说明账号、库 ID、令牌和表结构都可用，且该表当前为空。
- **真实采集未跑通**：分红日常采集在写库前被上游股票列表接口阻断（GitHub Runner 访问深交所/上交所/东方财富均被重置），未写入任何数据行。
- **股价采集未执行**：它与分红采集共用 `sync_stock_list()` 引导逻辑，`YL_stocks` 为空时会在同一位置中止，因此按任务约束没有跳过失败继续执行。
- 未触碰共享库中 `YL_` 前缀之外的任何表，未打印或提交任何凭证。

## 运行环境

| 项 | 值 |
| --- | --- |
| 仓库 | `WorkerHub/YieldLens` |
| 分支 / commit | `main` / `a7202a2` |
| 运行窗口 | 2026-10-19 12:26Z–12:30Z（北京时间 20:26–20:30） |
| 凭证来源 | 组织级 variables / secrets |
| 可见性核对 | 采集日志中 `CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_D1_DATABASE_ID` 均为非空值，`CLOUDFLARE_API_TOKEN` 显示为 `***` |

## 步骤 1：确认工作流存在

```text
$ gh workflow view apply-schema.yml
Apply D1 Schema - apply-schema.yml
ID: 389382538
Total runs 0
```

工作流存在且为 active，此前从未运行过。退出码 0。

## 步骤 2：dry-run 建表

| 项 | 值 |
| --- | --- |
| 命令 | `gh workflow run apply-schema.yml -f prefix=YL -f dry_run=true` |
| run | [39252075715](https://github.com/WorkerHub/YieldLens/actions/runs/39252075715) |
| 耗时 | 24s（12:26:29Z → 12:26:53Z） |
| 结果 | success，`gh run watch --exit-status` 退出码 0 |

关键日志：

```text
schema : /home/runner/work/YieldLens/YieldLens/worker/src/db/schema.sql
prefix : 'YL_' (13 statement(s))
[01/13] DRY  CREATE TABLE IF NOT EXISTS YL_stocks (...)
[02/13] DRY  CREATE INDEX IF NOT EXISTS idx_YL_stocks_ttm_yield ON YL_stocks(ttm_yield DESC)
[03/13] DRY  CREATE INDEX IF NOT EXISTS idx_YL_stocks_static_yield ON YL_stocks(static_yield DESC)
[04/13] DRY  CREATE INDEX IF NOT EXISTS idx_YL_stocks_industry ON YL_stocks(industry)
[05/13] DRY  CREATE INDEX IF NOT EXISTS idx_YL_stocks_market_cap ON YL_stocks(market_cap DESC)
[06/13] DRY  CREATE TABLE IF NOT EXISTS YL_stock_prices (...)
[07/13] DRY  CREATE INDEX IF NOT EXISTS idx_YL_stock_prices_code_date ON YL_stock_prices(code, trade_date DESC)
[08/13] DRY  CREATE TABLE IF NOT EXISTS YL_dividends (...)
[09/13] DRY  CREATE INDEX IF NOT EXISTS idx_YL_dividends_code_date ON YL_dividends(code, ex_div_date DESC)
[10/13] DRY  CREATE INDEX IF NOT EXISTS idx_YL_dividends_report_period ON YL_dividends(report_period)
[11/13] DRY  CREATE TABLE IF NOT EXISTS YL_dividend_yields (...)
[12/13] DRY  CREATE INDEX IF NOT EXISTS idx_YL_dividend_yields_code ON YL_dividend_yields(code, calc_date DESC)
[13/13] DRY  CREATE TABLE IF NOT EXISTS YL_system_settings (...)
dry run: no statements were sent to D1
```

核对结果：`YL_stocks`、`YL_stock_prices`、`YL_dividends`、`YL_dividend_yields`、`YL_system_settings` 全部带 `YL_` 前缀；日志中 `{prefix}` 残留计数为 0。dry-run 通过。

## 步骤 3：正式建表

### 第一次执行

| 项 | 值 |
| --- | --- |
| 命令 | `gh workflow run apply-schema.yml -f prefix=YL` |
| run | [39252190197](https://github.com/WorkerHub/YieldLens/actions/runs/39252190197) |
| 耗时 | 23s（12:27:24Z → 12:27:47Z） |
| 结果 | success，退出码 0 |

关键日志（每条语句一行，节选首尾）：

```text
prefix : 'YL_' (13 statement(s))
[01/13] OK   CREATE TABLE IF NOT EXISTS YL_stocks (...) (0 row(s))
...
[13/13] OK   CREATE TABLE IF NOT EXISTS YL_system_settings (...) (0 row(s))
OK: applied 13 statement(s) to the shared D1 database
```

13/13 全部 OK，无 FAIL。

### 第二次执行（幂等验证）

| 项 | 值 |
| --- | --- |
| run | [39252266417](https://github.com/WorkerHub/YieldLens/actions/runs/39252266417) |
| 耗时 | 26s（12:28:00Z → 12:28:26Z） |
| 结果 | success，退出码 0 |

```text
[01/13] OK ... [13/13] OK
OK: applied 13 statement(s) to the shared D1 database
```

无 `FAIL`，无报错，幂等验证通过。

## 步骤 4：分红首次采集（daily）——失败

| 项 | 值 |
| --- | --- |
| 命令 | `gh workflow run collect-dividends.yml -f mode=daily` |
| run | [39252379351](https://github.com/WorkerHub/YieldLens/actions/runs/39252379351) |
| 耗时 | 62s（12:28:53Z → 12:29:55Z） |
| 结果 | failure，采集进程退出码 1 |

关键日志：

```text
CLOUDFLARE_ACCOUNT_ID: <非空，已脱敏>
CLOUDFLARE_D1_DATABASE_ID: <非空，已脱敏>
CLOUDFLARE_API_TOKEN: ***
TABLE_PREFIX: YL
COLLECT_MODE: daily

WARNING ak.stock_info_a_code_name() failed on attempt 1/3: Expecting value: line 1 column 1 (char 0)
WARNING ak.stock_info_a_code_name() failed on attempt 2/3: Expecting value: line 1 column 1 (char 0)
ERROR   ak.stock_info_a_code_name() failed on attempt 3/3: Expecting value: line 1 column 1 (char 0)
WARNING Stock list primary source failed, trying snapshot fallback
WARNING ak.stock_zh_a_spot_em() (stock-list fallback) failed on attempt 1/3:
        ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
...
ERROR   ak.stock_zh_a_spot_em() (stock-list fallback) failed on attempt 3/3
RuntimeError: Stock list unavailable and YL_stocks is empty; cannot bootstrap:
        primary source failed: ...; snapshot fallback failed: ...
##[error]Process completed with exit code 1.
```

### 根因判断

1. 主来源 `ak.stock_info_a_code_name()` 依赖深交所/上交所页面，在 GitHub 托管 Runner（美国出口）上被对端重置或返回非 JSON。
2. 备用来源 `ak.stock_zh_a_spot_em()`（东方财富）同样被对端断开，3 次重试均失败。
3. 代码的第三层兜底是“读取 D1 中已有股票列表”，但 `YL_stocks` 是新建空表，无法兜底，于是按设计中止。
4. **不是 D1 配置问题**：中止前 `sync_stock_list()` 已成功执行 `SELECT code, name, is_st FROM YL_stocks`（`common/stock_list.py` 第 136 行），否则报错会是 D1 查询错误；实际报错是“表为空且上游不可用”。
5. 这是持续性环境问题，不是偶发：该仓库分红 workflow 最近 15 次运行全部失败，历史成功记录为 0；股价 workflow 自 2026-07-20 起也没有成功记录。
6. 本机直连对照：东方财富接口 HTTP 200、Sina 接口 HTTP 200，说明代码本身可用，差异在 Runner 出口网络/对端封禁。

## 步骤 5：股价首次采集——未执行

按任务约束“某一步失败不要跳过继续往下跑”，未触发 `collect-prices.yml`。

从代码路径可以确定它会同样失败：`collect_prices.py` 的 `main()` 在任何快照/历史采集之前先调用 `sync_stock_list(d1, prefix, log)`（第 496 行），而 `YL_stocks` 为空且上游不可用时会抛出同一个 `RuntimeError`。因此这一步不是独立可绕过的。

## 步骤 6：数据覆盖情况

| 表 | 行数 | 依据 |
| --- | --- | --- |
| `YL_stocks` | 0 | 采集器 `SELECT` 成功且返回空集，随后报 “YL_stocks is empty” |
| `YL_stock_prices` | 0 | 采集未进入快照/历史写入分支 |
| `YL_dividends` | 0 | 采集在股票列表引导阶段中止 |
| `YL_dividend_yields` | 0 | 同上 |
| `YL_system_settings` | 0 | 同上（尚未写入任何游标/进度） |

> 说明：这里没有伪造数字。除 `YL_stocks` 的“空集”由代码路径直接证明外，其余表为“未发生写入”的推断。若需在 D1 控制台最终确认，请执行：

```sql
SELECT COUNT(*) FROM YL_stocks;
SELECT COUNT(*) FROM YL_stock_prices;
SELECT COUNT(*) FROM YL_dividends;
SELECT COUNT(*) FROM YL_dividend_yields;
SELECT COUNT(*) FROM YL_system_settings;
-- 确认只建了 YL_ 表
SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'YL_*' ORDER BY name;
```

## 还缺什么

1. **可用的上游网络出口**：需要能稳定访问深交所/上交所/东方财富的 Runner 或代理。
2. **首次股票列表引导**：`YL_stocks` 必须先有数据，后续采集才能在上游抖动时走 D1 兜底。
3. **分红与股价的首批真实数据**：daily、backfill、snapshot 三步都尚未产生数据行。

## 下一步建议

1. **首选**：给采集 workflow 配置中国境内出口（self-hosted runner，或为 Actions 配置代理），然后按顺序重跑：
   ```bash
   gh workflow run collect-dividends.yml -f mode=daily
   gh workflow run collect-dividends.yml -f mode=backfill -f backfill_periods=20
   gh workflow run collect-prices.yml -f mode=snapshot
   ```
2. **临时引导方案**：先在一个能访问上游的环境（例如本机）用同一套环境变量运行一次 `python collect_dividends.py`，把股票列表写入 `YL_stocks`；之后 GitHub Runner 即使上游抖动也能走 D1 兜底继续采集。但若分红数据接口本身也被封，仍需解决出口问题。
3. **若必须留在 GitHub 托管 Runner**：需要单独立项评估可稳定访问的替代数据源或镜像，并做完整验证；不要用 `worker/seed.sql` 的样例数据填充生产表。
4. 建表本身已完成，不需要重复执行 `apply-schema.yml`；后续若修改 schema，直接复跑即可（已验证幂等）。
