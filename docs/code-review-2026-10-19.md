# YieldLens 全仓代码审查报告（MVP 第一期）

- **审查对象**：`dca24d1 init version`（当前 `main`）
- **审查日期**：2026-10-19
- **审查方式**：只读静态审查 + 本地构建验证 + 关键函数逻辑复现；未连接生产 Cloudflare 资源，未修改任何业务代码、配置或依赖
- **交付物**：本报告；修复工作请另开任务

---

## 0. 概述

### 0.1 项目规模

| 范围 | 文件数 | 代码行数（约） |
|---|---:|---:|
| `worker/src`（TS） | 10 | 555 |
| `web/src`（TS/TSX） | 18 | 996 |
| `python-collector`（含 tests） | 11 | 989 |
| `worker/src/db/schema.sql` | 1 | 103 |
| `worker/seed.sql` | 1 | 2,221 |
| `.github/workflows` | 2 | 99 |
| 仓库 tracked 文件合计 | 63 | 5,552 |

规模不大，适合做全量审查；问题集中在**数据采集写入链路**和**股息率口径**，而不是框架或工程结构。

### 0.2 结论

**当前版本不能按 README 的描述上线。** 读路径（Worker API + React 前端）在本地 seed 数据下可以跑通，`pnpm typecheck` 与 `pnpm build` 均通过；但生产写路径存在多个阻断级缺陷：

1. D1 批量写入的绑定参数数量超过 Cloudflare 限制，采集器实际上无法写入任何数据；
2. 分红采集默认只查询“当前年度的 12 月 31 日”报告期，日常运行大概率 0 条；
3. 送转比例中的 `NaN` 会让 `requests` 在发送前直接抛异常，整批采集中断；
4. 价格增量逻辑在当日首次写入后跳过后续所有运行，收盘价无法保证被采集；
5. 全市场 5000+ 只股票在 25 分钟 job 超时内跑不完，收盘运行覆盖不全；
6. `TABLE_PREFIX` 非空时，迁移/seed 与运行时表名不一致，所有查询直接 500。

即使修好写入链路，核心指标仍有系统性偏差：静态股息率取错年度并存在前视、连续分红年数算法基本必错、历史股息率序列只在除权日打点、前端分红金额少显示 10 倍。工程化方面没有 CI、没有部署 workflow、Worker/Web 没有测试，README 与仓库实际多处不一致。

**安全方面没有发现可直接利用的 SQL 注入**：排序字段和排序方向有白名单，过滤条件使用绑定参数，`TABLE_PREFIX` 有正则校验，CORS 也不是通配符。主要风险是输入长度/缓存键滥用、公开 API 无限流、以及若干健壮性问题。

### 0.3 问题分布

| 级别 | 数量 |
|---|---:|
| 阻断级 | 6 |
| 高危 | 10 |
| 中危 | 18 |
| 低危/建议 | 24 |
| 待验证 | 9 |

### 0.4 本地验证记录

| 命令 | 结果 |
|---|---|
| `pnpm typecheck` | 通过（web `tsc -b --noEmit`、worker `tsc --noEmit`） |
| `pnpm lint` | 1 个 warning：`worker/src/db/queries/stocks.ts:3` 的 `prefix` 参数未使用；1 个 info：`biome.json` schema 2.5.0 与 CLI 2.5.4 不一致 |
| `pnpm build` | 通过；web 产物 679.15 kB（gzip 192.87 kB），Vite 提示超过 500 kB |
| Python 单元测试 | 未执行：审查环境没有安装 `akshare`/`pandas`；测试本身的问题见 M-16、H-10 |
| 关键逻辑复现 | 见 B-02、H-01、H-03 中的复现结果 |

### 0.5 严重级别定义

- **阻断级**：生产环境无法写入/无法上线，或核心数据完全错误，必须先修。
- **高危**：核心指标或主要功能错误、部署链路不可用、数据链路大概率超时/失败。
- **中危**：特定条件下出错、明显的用户体验/可维护性问题、安全加固缺口。
- **低危/建议**：不影响主流程的健壮性、可访问性、文档和清洁度问题。

### 0.6 做得好的地方

- **SQL 注入面控制得不错**：排序字段/方向有白名单（`routes/stocks.ts:12-15`、`queries/stocks.ts:46`），过滤值全部走绑定参数，`TABLE_PREFIX` 有正则校验（`index.ts:11-17`）；没有发现可直接利用的注入点。
- **密钥管理方向正确**：采集凭证只从环境变量读取（`d1_client.py:39-41`），`.env` 被 gitignore，`wrangler.toml` 没有提交真实 ID。
- **读路径类型/构建是通的**：`pnpm typecheck`、`pnpm build` 通过；前端 API 客户端集中、类型定义与后端基本对齐。
- **本地 seed 覆盖了 15 只股票、10 年价格/分红/收益率**，足以支撑前端联调（虽然数据是模拟的，且存在 L-12 的一致性问题）。

---

## 1. 阻断级

### B-01 D1 批量写入超过绑定参数上限，采集器无法写入任何数据

- **问题**：`D1Client.upsert_rows()` 默认每批 150 行，把整批拼成一条多值 `INSERT`。Cloudflare D1 的硬限制是**单条查询最多 100 个绑定参数**，而 150 行远超此限制。
- **位置**：`python-collector/common/d1_client.py:22`、`python-collector/common/d1_client.py:88-115`
- **证据**：
  - `DEFAULT_BATCH_SIZE = 150`（第 22 行）；`upsert_rows()` 在 `:107-114` 把 `batch_size` 行拼成一条 SQL，参数数量 = 行数 × 列数。
  - 各表的实际参数数量如下。官方限制：Cloudflare D1 Limits，“Maximum bound parameters per query = 100”，<https://developers.cloudflare.com/d1/platform/limits/>。

| 表 | 列数 | 150 行参数数 | 100 参数上限下每批最多行数 |
|---|---:|---:|---:|
| `stocks` | 21 | 3,150 | 4 |
| `dividends` | 14 | 2,100 | 7 |
| `stock_prices` | 8 | 1,200 | 12 |
| `dividend_yields` | 5 | 750 | 20 |

- **影响**：`bootstrap_stock_list()`、价格写入、分红写入、收益率写入全部会在第一批就失败。生产环境第一次运行价格采集就会在 `INSERT OR REPLACE INTO stocks ...` 处报错，后续所有数据都不会落库。这不是“性能问题”，是**写链路完全不可用**。
- **修复建议**：
  1. 按列数动态计算批大小：`batch_size = max(1, 100 // len(columns))`，并在 `upsert_rows()` 内断言 `len(columns) * len(batch) <= 100`。
  2. 对 `stocks` 这种 21 列的表，改为分批 4 行；或者改用 D1 的 batch/多语句接口，把多条小 INSERT 放进一次请求。
  3. 增加一个针对真实 D1 限制的单元测试（或至少断言参数数量），不要让 mock 测试继续给出“150 行没问题”的假信心（见 M-16）。

### B-02 分红采集默认只取未来报告期，日常运行 0 条

- **问题**：`report_periods()` 从“当前年份”开始，先追加 `{当前年}1231`，再追加 `{当前年}0630`；而 `BACKFILL_PERIODS` 默认是 1，于是每日任务只查询当前年度的 12 月 31 日报告期。该报告期的年报在次年才披露，当前日期下通常没有任何数据。
- **位置**：`python-collector/collect_dividends.py:93-102`、`python-collector/collect_dividends.py:335-340`、`.github/workflows/collect-dividends.yml:39`
- **证据**：
  - 2026-10-19 复现 `report_periods(10)[:6]` 得到：
    `['20261231', '20260630', '20251231', '20250630', '20241231', '20240630']`
  - 默认 `BACKFILL_PERIODS=1` 时只取 `['20261231']`。
  - AKShare 文档明确 `stock_fhps_em(date=...)` 的 `date` 是报告期（`XXXX0630` / `XXXX1231`），不是公告日：<https://akshare.akfamily.xyz/data/stock/stock.html>（“分红配送-东财”）。
  - 2026 年年报在 2027 年才披露，因此 2026 年内查询 `20261231` 应为空（建议在 runner 上按“待验证”清单确认一次）。
- **影响**：即使 B-01 修好，每日分红任务也不会写入任何新分红；`stocks.static_yield`/`ttm_yield` 只能基于旧数据重算，新分红永远进不来。首次上线如果没有手动跑 `BACKFILL_PERIODS=20`，整个应用的分红/股息率数据为空。
- **修复建议**：
  1. 报告期生成改为从“最近一个已披露的报告期”开始，例如按日期动态选择：当前年 6 月 30 日之后优先 `{当前年}0630`，否则 `{上一年}1231`；并保留前一年年报作为补充。
  2. 日常默认至少取 3 个报告期（当前年 0630、上一年 1231、上一年 0630），或让采集器先探测哪个报告期有数据再决定。
  3. 在 workflow 中把“日常增量”和“首次回填”拆成两个明确的输入/任务，避免默认值误用。

### B-03 送转比例中的 NaN 导致 JSON 序列化崩溃，整批采集中断

- **问题**：`bonus_share_ratio`/`transfer_ratio` 直接对 AKShare 的列做 `float(r.get(...) or 0) or 0`。当值为 `NaN` 时，`NaN or 0` 仍返回 `NaN`（`bool(NaN)` 为真），最终把 `NaN` 放进请求体。`requests` 在 `prepare_body()` 中使用 `json.dumps(..., allow_nan=False)`，会在发送前抛 `InvalidJSONError`。
- **位置**：`python-collector/collect_dividends.py:160-161`、`python-collector/collect_dividends.py:169-170`；`python-collector/common/d1_client.py:47-62`
- **证据**：
  - 代码：`"bonus_share_ratio": float(r.get("送转股份-送转比例") or 0) or 0`；`transfer_ratio` 同理。
  - `requests` 源码 `PreparedRequest.prepare_body()`：`body = complexjson.dumps(json, allow_nan=False)`，`ValueError` 被包装成 `InvalidJSONError`。
  - AKShare `stock_fhps_em` 文档示例中，送转相关列大量为 `NaN`（无送转的公司）；`送转股份-送转比例` 本身是否返回 `NaN` 见 V-09。
- **影响**：只要某一行没有送转且上游返回 `NaN`（大多数公司，待验证 V-09），整批 `upsert_rows()` 就会在 `requests.post()` 之前抛异常。`collect_dividends_for_period()` 没有捕获这个异常，`main()` 也没有兜底，整个分红采集任务直接失败。这是一个“第一次真实数据就可能触发”的阻断项。
- **修复建议**：
  1. 统一做数值清洗：`value = r.get("送转股份-送转比例"); bonus = 0.0 if value is None or pd.isna(value) else float(value)`。
  2. 在 `D1Client.execute_sql()` 里对 `params` 做一次 `NaN/Inf` 检查，转成 `None` 或直接报出带行号的业务错误，避免把问题拖到 HTTP 层。
  3. 为 `collect_dividends_for_period()` 增加一条“含 NaN 送转列”的单元测试。

### B-04 价格增量逻辑在当日首次写入后跳过后续运行，收盘价无法保证被采集

- **问题**：增量模式用 `trade_date` 作为水位线，只要 `(today - watermark).days < 1` 就直接 `return True`。这意味着当天一旦为该股票写入过一行（哪怕是盘中部分数据），当天后续所有 30 分钟运行都会跳过该股票；如果首次写入发生在盘中，写入的“收盘价”其实是盘中价，收盘后也不会再修正。
- **位置**：`python-collector/collect_prices.py:106-112`、`python-collector/collect_prices.py:119-150`；`.github/workflows/collect-prices.yml:18-28`
- **证据**：
  - `if (date.today() - watermark_date).days < 1: return True  # already up to date`（`:110-111`）。
  - `latest = rows[-1]` 后直接用 `latest["close"]` 更新 `stocks.last_price`（`:145-150`），没有区分盘中/收盘。
  - AKShare `stock_zh_a_daily` 是新浪日线接口，文档提示“多次获取容易封禁 IP”；盘中是否返回当日行需要实测（见“待验证”清单）。
- **影响**：
  - 若新浪日线在盘中返回当日行：09:30 首次运行写入盘中价，10:00 之后全部跳过，**收盘价永远不会被写入**，`last_price`、`static_yield`、`ttm_yield` 都基于错误价格。
  - 若盘中不返回当日行：30 分钟一次的调度实际上每次都在重复抓取昨日数据，盘中没有任何更新，与 README 的“交易时段内每 30 分钟采集股价”不符。
  - 两种情况下，`price_updated_at` 都会显示“已更新”，但数据可能是错的。
- **修复建议**：
  1. 不要用 `trade_date` 判断“今天是否已更新”；改用 `price_updated_at`，并且只在收盘后（如 15:05 CST）才认为当日数据是最终收盘价。
  2. 盘中运行只更新“实时价”字段（或明确标记为盘中快照），收盘运行强制重抓当日并覆盖为收盘价；`last_price` 只在收盘后写入。
  3. 如果产品只需要日线收盘价，直接取消盘中 30 分钟调度，改为收盘后一次采集，减少上游压力和歧义。

### B-05 全市场采集在 25 分钟 job 超时内跑不完，收盘运行覆盖不全

- **问题**：价格采集对每只股票串行调用新浪接口，限流器 `sina_limiter` 的最小间隔是 0.5s + 0-0.3s 抖动，平均约 0.65s/只；5000+ 只股票需要约 54 分钟，而 workflow 的 `timeout-minutes` 是 25。workflow 注释自己也承认了这一点。
- **位置**：`python-collector/common/rate_limiter.py:37-38`、`python-collector/collect_prices.py:163-184`、`.github/workflows/collect-prices.yml:8-15`、`.github/workflows/collect-prices.yml:38`
- **证据**：
  - `sina_limiter = RateLimiter(min_interval=0.5, jitter=0.3)`（`rate_limiter.py:38`）。
  - `for i, code in enumerate(codes, start=1): collect_one_stock(...)`（`collect_prices.py:179-180`），完全串行。
  - workflow 注释：“~5000+ sequential HTTP calls, which will exceed this job's 25-minute timeout”。
- **影响**：当日首轮冷启动只能覆盖约 40%-45% 的股票；15:00 收盘运行同样受 25 分钟限制，无法保证全市场收盘价都被采集。再叠加 B-04 的跳过逻辑，收盘价覆盖率会进一步下降。README 的“全市场 A 股”排行会包含大量过期价格。另外，workflow 用 `github.event.inputs.backfill_batch != ''` 判断是否进入回填模式，`schedule` 事件下的取值待验证（V-05）；如果误判为 true，每次定时运行都会执行 10 年回填 batch 0，问题更严重。
- **修复建议**：
  1. 用 `system_settings` 记录游标/批次，把增量采集也做成可断点续跑的分片（类似 `BACKFILL_BATCH`），每次只处理固定数量并在下次从游标继续。
  2. 或者改用一次返回全市场快照的接口（如 `stock_zh_a_spot_em`）获取最新价，再只对需要历史的股票做日线回填。
  3. 增加 `concurrency` 组，避免 25 分钟超时后下一轮与上一轮重叠。
  4. 在 workflow 中把超时提高到实际需要的时间，或拆分 job；不要保留一个已知跑不完的配置。

### B-06 TABLE_PREFIX 与迁移/seed 不一致，非空前缀直接 500

- **问题**：运行时 `getTablePrefix()` 会把 `TABLE_PREFIX=yl` 规范化为 `yl_`，查询 `yl_stocks`；但 `db:migrate:*` 用 `sed 's/{prefix}//g'` 永远替换成空串，建的是 `stocks`；`seed.sql` 也硬编码了无前缀表名。只要生产设置非空前缀，迁移/seed 与运行时表名就不一致。
- **位置**：`worker/package.json:10`、`worker/package.json:12`、`worker/src/types.ts:12-16`、`worker/src/db/schema.sql:9`、`worker/seed.sql:2`
- **证据**：
  - `getTablePrefix()` 的返回逻辑是：前缀不以 `_` 结尾时补一个 `_`（即 `raw + "_"`）。
  - 迁移命令：`sed 's/{prefix}//g' src/db/schema.sql > .schema.local.sql`。
  - 实测 `TABLE_PREFIX=yl` 时运行时 SQL 为 `SELECT * FROM yl_stocks`，而迁移产物是 `CREATE TABLE IF NOT EXISTS stocks`。
- **影响**：所有 Worker 查询报 `no such table: yl_stocks`，API 全部 500；采集器也会写 `yl_stocks`，但表根本没建。README 把 `TABLE_PREFIX` 描述为可选变量（`README.md:137`），实际是一个“一设就炸”的配置。
- **修复建议**：
  1. 用一个 Node/Python 脚本读取 `TABLE_PREFIX`，按与 `getTablePrefix()` 完全相同的规则生成 schema 和 seed，禁止再用固定空串的 `sed`。
  2. 或者删除 `TABLE_PREFIX` 特性，MVP 不需要多租户表前缀，减少配置面。
  3. 把 `index.ts` 的前缀校验从 `^[a-zA-Z0-9_]+$` 改为 `^[a-zA-Z_][a-zA-Z0-9_]*$`，避免 `TABLE_PREFIX=123` 生成 `123_stocks` 这种非法 SQL 标识符。
  4. 在 CI 中加一条 `TABLE_PREFIX=yl` 的迁移+查询冒烟测试。

---

## 2. 高危

### H-01 静态股息率取错年度，并且历史序列存在前视

- **问题**：历史序列中，`this_year = int(d["report_period"][:4])`，然后把该报告年度下**所有**已实施分红相加，不判断这些分红的除权日是否已经发生；当前值则取 `max(report_period 年份)`，如果最新一条是中期报告（`0630`），静态股息率只算中期分红，而不是最近一个完整财年。
- **位置**：`python-collector/collect_dividends.py:260-266`、`python-collector/collect_dividends.py:291-296`
- **证据**：
  - 历史：`same_year_divs = [x["per_share_div"] for x in implemented if x["report_period"][:4] == str(this_year)]`，没有 `x["ex_div_date"] <= ex_date` 约束。
  - 当前：`latest_year = max(int(d["report_period"][:4]) for d in implemented)`；`latest_year_divs` 只按报告年度过滤。
  - 复现：2026-09-01 的中期分红锚点下，若同一报告年度还有 2027-06-01 才除权的年度分红，静态股息率会把它算进去（示例：0.5 元中期 + 1.0 元年度 = 1.5 元，而不是 0.5 元）。
- **影响**：静态股息率是产品两个核心排序指标之一。对“中期 + 年度”分红的公司，历史曲线会出现前视（未来分红被提前计入），当前静态股息率在中期分红披露后会被低估（只算半年），排序和筛选结果都会错。
- **修复建议**：
  1. 明确定义“静态股息率 = 最近一个完整财年（`report_period` 以 `1231` 结尾且已结束）的每股分红合计 / 当前价”。
  2. 历史锚点只使用 `ex_div_date <= 锚点日期` 的分红；当前值固定取最近一个 `1231` 报告期，不要用 `max(年份)`。
  3. 在测试中覆盖“中期 + 年度”“只有中期”“跨年”三种场景。

### H-02 历史股息率序列只在除权日打点，分位按事件计数而非时间

- **问题**：`dividend_yields` 只在每一条已实施分红的除权日写入一行，`calc_date = ex_div_date`。股息率 = 分红 / 股价，股价每天变化，但序列只在分红事件日更新，导致“历史走势”实际上是一条每年一个点的阶梯线。历史分位也基于这些稀疏行做 `COUNT(*)`，不是按时间加权。
- **位置**：`python-collector/collect_dividends.py:254-289`、`worker/src/db/schema.sql:85-92`、`web/src/components/YieldChart.tsx:63-121`、`worker/src/db/queries/yields.ts:45-64`
- **证据**：
  - `for d in implemented: ... yield_rows.append({"calc_date": ex_date, ...})`（`:254-286`）。
  - schema 注释：“calc_date ... typically the ex_div_date”（`schema.sql:87`）。
  - 前端图表直接使用 `/stocks/:code/yields` 返回的 `price` 字段画股价线；`/stocks/:code/prices` 路由存在但前端从未调用。
  - 分位查询没有日期范围，只对 `dividend_yields` 行数做 `COUNT(*)`。
- **影响**：
  - 选“1 年”区间时，如果最近一年没有分红，图表直接“暂无历史数据”，即使股价和历史收益率数据是存在的。
  - 图表上的“股价对比”通常只有 10-20 个点（每年 1-2 次分红），不是真实股价走势。
  - “当前股息率高于近 N 年 X% 的历史时间”实际上是“高于 X% 的分红事件”，不是时间占比；对一年多次分红的公司还会被重复计数。
- **修复建议**：
  1. 生成日频或月频的 `dividend_yields` 序列：用滚动 12 个月分红 / 当日收盘价计算 TTM，用最近完整财年分红 / 当日收盘价计算静态。
  2. 如果不想存日频，就在读取时用 `stock_prices` + `dividends` 计算，或至少按月末采样。
  3. 分位改为对日频观测做 `COUNT`，并接受 `range` 参数；前端股价线改用 `/prices` 数据，或直接删掉股价叠加，避免误导。

### H-03 连续分红年数算法错误，基本必错

- **问题**：`consecutive_years` 的循环从当前年份开始，条件是 `year in report_years or (year - 1) in report_years`，然后无条件 `consecutive_years += 1` 并 `year -= 1`。这会导致两种错误：当年/上一年没有分红时直接返回 0；有连续年份时多算一年。
- **位置**：`python-collector/collect_dividends.py:305-313`
- **证据**：复现该循环（当前年 2026）：
  - `report_years = {2015..2024}` → 返回 `0`（实际应为 10）；
  - `report_years = {2015..2025}` → 返回 `12`（实际应为 11）；
  - `report_years = {2024}` → 返回 `0`（实际应为 1）。
- **影响**：`stocks.consecutive_dividend_years`、排行表“连续分红年”、以及 `min_consecutive_years` 筛选全部不可信。对一个以“连续分红”为卖点的工具，这是核心指标错误。
- **修复建议**：
  1. 先从 `report_years` 里取最大年份（或最近一个完整财年），然后逐年向下检查 `year in report_years`，遇到缺失立即停止；不要用 `year - 1` 的“或”条件。
  2. 明确“连续”是按报告年度还是按除权年度；中期/年度分红应合并为同一年。
  3. 增加 0 年、1 年、连续 10 年、中间断 1 年四组测试。

### H-04 前端分红方案金额少显示 10 倍

- **问题**：`per_share_div` 是**每股**分红，但 `describeDividendPlan()` 拼出的文案是“10股...派息 X 元”，直接把每股金额当成 10 股金额。
- **位置**：`web/src/components/DividendTable.tsx:6-11`
- **证据**：`describeDividendPlan()` 中先 `parts.push("派息" + d.per_share_div.toFixed(4) + "元")`，最后返回 `"10股" + parts.join("") + "(含税)"`。例如 seed 中 `600519-2016` 的 `per_share_div=24.633`，页面会显示“10股派息24.6330元”；按每股口径应显示 246.33 元/10 股。
- **影响**：用户看到的分红方案金额只有实际的 1/10，属于直接面向用户的事实错误；对高分红股票尤其明显。
- **修复建议**：改为 `(d.per_share_div * 10).toFixed(2)`，或者把文案改成“每股派息 X 元”。同时确认 `bonus_share_ratio`/`transfer_ratio` 的“每 10 股”口径是否一致。

### H-05 min_yield/max_yield 过滤绑定到当前排序字段

- **问题**：`buildWhereClause()` 对 `minYield`/`maxYield` 使用 `${params.sort} >= ?` / `${params.sort} <= ?`。当用户按市值排序时，`min_yield` 实际过滤的是 `market_cap`。
- **位置**：`worker/src/db/queries/stocks.ts:18-25`
- **证据**：`conditions.push(`${params.sort} >= ?`)`，而 `params.sort` 可以是 `ttm_yield`、`static_yield` 或 `market_cap`。
- **影响**：`/api/stocks?sort=market_cap&min_yield=5` 会变成“市值 >= 5 亿元”，而不是“股息率 >= 5%”，返回结果完全错误；前端同时提供排序和最低股息率输入，用户很容易触发。
- **修复建议**：股息率过滤永远使用明确的列（例如 `ttm_yield`），或者新增 `yield_field=ttm_yield|static_yield` 参数；不要复用 `sort`。同时校验 `min <= max`。

### H-06 industry 永远为空，行业筛选形同虚设

- **问题**：采集器在插入股票时把 `industry` 设为 `""`；`collect_fundamentals_snapshot()` 的文档字符串声称会更新“market cap / PE / PB / industry”，但实际 SQL 只更新 `market_cap`、`pe_ttm`、`pb`。AKShare 的 `stock_zh_a_spot_em()` 输出本身也没有行业列。
- **位置**：`python-collector/collect_dividends.py:11`、`python-collector/collect_dividends.py:174-214`、`python-collector/collect_prices.py:61`、`worker/src/db/schema.sql:14`、`web/src/pages/ranking/RankingPage.tsx:90-105`
- **证据**：
  - 所有写入 `industry` 的地方都是 `""`（`collect_dividends.py:59`、`collect_prices.py:61`）。
  - `collect_fundamentals_snapshot()` 的 UPDATE 只有 `market_cap`、`pe_ttm`、`pb`、`updated_at`（`:202-211`）。
  - 生产采集后 `distinctIndustries()` 返回空数组，前端下拉框只剩“全部行业”。
- **影响**：README 宣传的“行业筛选”完全不可用；个股详情的行业显示为“—”；排行表行业列全空。
- **修复建议**：增加行业数据源（如 `stock_board_industry_name_em` + 成分股，或 `stock_individual_info_em` 的“行业”字段），写入 `stocks.industry`；如果 MVP 不做行业，删除前端筛选和 README 描述。

### H-07 is_st 永远为 0，名称从不更新，ST 过滤和 ST 标记失效

- **问题**：`sync_stock_list()` 的文档字符串说会“pick up newly-listed stocks and renames”，但代码只把“不在 existing 集合里的 code”插入，已有股票的 `name`、`is_st` 永远不会更新。`collect_prices.bootstrap_stock_list()` 也把 `is_st` 固定为 0。
- **位置**：`python-collector/collect_dividends.py:45-90`、`python-collector/collect_dividends.py:82-88`、`python-collector/collect_prices.py:64`、`worker/src/db/queries/stocks.ts:38-40`、`web/src/components/StockTable.tsx:64-68`
- **证据**：`new_rows = [r for r in rows if r["code"] not in existing]`，只插入新代码；没有任何 `UPDATE ... SET name/is_st`。
- **影响**：`exclude_st=true` 过滤永远是空操作；ST/*ST 徽标永远不会出现；股票改名（如“ST 摘帽”）后仍显示旧名。README 的“排除 ST/*ST”筛选不可信。
- **修复建议**：对已存在的股票执行 `UPDATE stocks SET name=?, is_st=?`（`is_st` 可由名称包含 `ST`/`*ST` 推导，或使用专门的 ST 列表接口）；同时处理退市/停牌股票的清理或标记。

### H-08 部署脚本不可移植且会覆盖本地配置，首次部署按 README 无法完成

- **问题**：根 `deploy` 脚本使用 macOS 专属的 `sed -i ''`，在 Linux/CI 上会失败；部署成功后执行 `git checkout -- wrangler.toml`，会丢弃用户未提交的真实 `account_id`/`database_id`/KV id 修改。另外 `wrangler.toml` 仍是占位符，README 却写“首次部署后再替换”，而 `wrangler deploy` 在占位符状态下就会失败。
- **位置**：`package.json:16`、`worker/wrangler.toml:5`、`worker/wrangler.toml:17`、`worker/wrangler.toml:21`、`README.md:100`
- **证据**：
  - `"deploy": "pnpm build:web && sed -i '' ... && cd worker && pnpm deploy && git checkout -- wrangler.toml"`。
  - `account_id = "CLOUDFLARE_ACCOUNT_ID_PLACEHOLDER"`、`database_id = "D1_DATABASE_ID_PLACEHOLDER"`、`id = "KV_NAMESPACE_ID_PLACEHOLDER"`。
  - README：“首次部署后，`worker/wrangler.toml` 中的 ... 需要替换为真实值”。
- **影响**：
  - Linux/CI 上 `sed -i ''` 报错，部署直接失败；
  - 即使部署成功，`git checkout -- wrangler.toml` 会把用户刚填但未提交的真实 ID 还原成占位符，下一次部署又失败；
  - 按 README 的顺序，第一次部署根本不可能成功；
  - `APP_VERSION` 在 `wrangler.toml`/`env.d.ts` 中定义并在 deploy 时 sed，但代码从未使用（`worker/src/env.d.ts:2`，全仓 grep 无引用），是死代码。
- **修复建议**：
  1. 用 Node 脚本或 `perl -pi -e` 替代 macOS `sed`；或者用 `wrangler deploy --var APP_VERSION:$VERSION` 传入版本，不修改配置文件。
  2. 不要用 `git checkout --` 清理；生成临时配置文件（如 `wrangler.deploy.toml`）并显式传给 `wrangler deploy -c`。
  3. README 改为“先替换占位符，再部署”；把真实 ID 放在 CI 变量/secret 中，不要依赖本地未提交修改。
  4. 删除未使用的 `APP_VERSION`，或真正在 `/api/health` 中返回它。

### H-09 分红全量重算为 N+1 查询，55 分钟超时和 API 限流风险高

- **问题**：分红任务在写完分红后，对“有分红记录”的每只股票逐个执行 `recompute_yields_for_stock()`：每只股票至少 3 次 SELECT + 1 次 UPDATE + 1 次 upsert，并且把该股票全部历史价格拉回 Python。全市场约 5000 只股票，等于约 2.5 万-3 万次 D1 REST 请求（还要加上 `collect_fundamentals_snapshot()` 每只股票一次 UPDATE）。
- **位置**：`python-collector/collect_dividends.py:195-212`、`python-collector/collect_dividends.py:228-329`、`python-collector/collect_dividends.py:352-363`、`.github/workflows/collect-dividends.yml:20`
- **证据**：
  - `for _, r in df.iterrows(): ... d1.execute_sql(f"UPDATE ... WHERE code = ?", ...)`（`:195-212`），逐股票一次 HTTP。
  - `recompute_yields_for_stock()` 每次调用都重新查 `dividends`、`stock_prices`、`stocks`，并逐股票 `upsert_rows`（`:228-329`）。
  - workflow `timeout-minutes: 55`。
- **影响**：首次全量运行大概率在 55 分钟内跑不完，或在 Cloudflare API 速率限制处收到 429；收益率只更新了一部分股票，且没有断点/游标，下一次又从头发起。`D1Client` 只对网络异常和 429 做 3 次重试，之后直接抛错，整个任务失败。
- **修复建议**：
  1. 用 SQL 在 D1 内完成收益率计算（窗口函数/CTE），或者至少按股票批量查询，不要每只股票 3 次往返。
  2. 只重算“分红或价格发生变化”的股票，记录 `yield_updated_at`/水位线。
  3. 使用 D1 batch/临时表批量 UPDATE，减少请求数；给 D1 请求加限流和游标，支持断点续跑。
  4. 把“分红采集”和“收益率重算”拆成两个 job，分别设置超时和重试。

### H-10 没有 CI/部署 workflow，Worker/Web 没有测试，README 与仓库实际不符

- **问题**：仓库只有两个数据采集 workflow，没有 CI（lint/typecheck/test/build），也没有 README 声称的部署 workflow；`web`、`worker` 没有 `test` 脚本，Python 测试也没有覆盖真正的股息率计算。README 还引用不存在的 `PRD.md`，并宣称了未实现/未落地的筛选和字段。
- **位置**：`.github/workflows/collect-prices.yml`、`.github/workflows/collect-dividends.yml`、`package.json:6-18`、`web/package.json:6-10`、`worker/package.json:5-13`、`README.md:7`、`README.md:11`、`README.md:24`、`README.md:34`、`.gitignore:30`
- **证据**：
  - `.github/workflows/` 下只有 `collect-prices.yml` 和 `collect-dividends.yml`；没有 `ci.yml`/`deploy.yml`。
  - 根 `package.json` 没有 `test`；`web`/`worker` 也没有测试框架或测试脚本。
  - `python-collector/tests/test_yield_computation.py` 只测试 `price_on_or_before()`、`report_periods()` 的长度、`parse_dividend_status()`，没有测试静态/TTM/连续年数。
  - README 多处链接 `PRD.md`，但仓库没有该文件，且 `.gitignore:30` 明确忽略 `PRD.md`。
  - README 宣称“行业、市值、股息率区间、连续分红年数”筛选，前端只实现了行业、最低股息率、排除 ST（`RankingPage.tsx:88-128`）。
  - README 宣称“部署：GitHub Actions + Wrangler”，但没有部署 workflow。
- **影响**：类型错误、lint 警告、构建失败、核心算法回归都不会被 CI 拦住；新开发者按 README 找不到 PRD、找不到部署流程，也无法判断哪些功能真的可用。
- **修复建议**：
  1. 增加 `.github/workflows/ci.yml`：`pnpm install --frozen-lockfile`、`pnpm lint`、`pnpm typecheck`、`pnpm build`、Python `unittest`；至少对 `main` 和 PR 运行。
  2. 增加部署 workflow（`wrangler-action` 或显式 `pnpm deploy`），使用 GitHub Secrets/Variables 注入 account/database/KV id。
  3. 给 `web`/`worker` 增加最小测试（API 路由、查询构造、缓存键、组件渲染），给 Python 增加收益率/连续年数/NaN/分页参数测试。
  4. 更新 README：要么提交 `PRD.md`，要么删除链接；把未实现的功能标为“规划中”；补上真实部署步骤和已知限制。

---

## 3. 中危

### M-01 分页排序没有稳定次序，LIMIT/OFFSET 可能重复或漏行

- **问题**：排行查询只按 `${sortCol} ${order} NULLS LAST` 排序，没有唯一键作为次级排序。股息率相同或都为 NULL 的股票在翻页时顺序不稳定，`LIMIT/OFFSET` 可能重复或漏掉记录。
- **位置**：`worker/src/db/queries/stocks.ts:58-66`
- **影响**：用户翻页时看到重复股票或漏掉股票；导出/对比结果不可复现。
- **修复建议**：追加 `, code ASC` 作为稳定排序；如果数据量大，考虑基于 `(sort_value, code)` 的游标分页。

### M-02 未知 API GET 请求返回 SPA HTML 200，而不是 JSON 404

- **问题**：`app.get("*")` 的 SPA fallback 会捕获所有未匹配的 GET，包括 `/api/unknown`。`ASSETS.fetch()` 对不存在的 API 路径返回 404 后，代码又回退到 `/`，最终返回 `index.html` 和 200。
- **位置**：`worker/src/index.ts:38-42`
- **影响**：API 客户端收到 HTML 而不是 JSON 错误，`response.json()` 抛解析错误；监控/调试时误以为接口存在。对不存在的静态资源也会返回 HTML，导致 MIME 错误。
- **修复建议**：在 SPA fallback 之前注册 `app.all("/api/*", c => c.json({error:"Not Found"}, 404))`；fallback 只对 `Accept: text/html` 的导航请求生效，静态资源 404 保持 404。

### M-03 搜索/筛选输入没有长度限制，LIKE 通配符未转义，缓存键可被放大

- **问题**：
  - `q` 没有长度上限；D1 对 `LIKE`/`GLOB` pattern 的限制是 50 字节，超长查询会直接报错 500。
  - `q` 中的 `%`、`_` 没有转义，会被当作通配符（不是 SQL 注入，但语义错误）。
  - `industry` 是任意字符串，直接进入排行缓存键；攻击者可以用大量不同的 `industry` 值制造大量 KV key（每个 300 秒 TTL），增加 KV 写入成本和 D1 查询；超长 `industry` 还可能超过 KV key 512 字节限制，导致 `kv.put` 抛错。
  - `page` 没有上限，巨大的 `OFFSET` 会带来无谓的扫描。
- **位置**：`worker/src/routes/stocks.ts:10`、`worker/src/routes/stocks.ts:22`、`worker/src/routes/stocks.ts:59-67`、`worker/src/db/queries/stocks.ts:96-111`、`worker/src/services/cache.ts:22-30`
- **影响**：公开 API 可被低成本滥用；长输入导致 500；`%` 搜索返回不相关结果；KV 成本不可控。
- **修复建议**：限制 `q` 长度（如 32 字符/50 字节）并转义 `%`/`_`；`industry` 与 `distinctIndustries()` 结果做白名单校验；`page` 设置上限（如 1000）；对公开 API 加限流。

### M-04 前端缺少错误态，列表/图表/搜索存在请求竞态

- **问题**：
  - 排行页的 industries 请求和列表请求都没有 `.catch`，失败时产生未处理的 Promise rejection，页面停留在“共 0 只股票”或旧数据。
  - 排行页/图表/搜索都没有取消旧请求或忽略过期响应；快速切换筛选/区间/输入时，较慢的旧响应可能覆盖新响应。
  - 详情页把任何错误（500、网络错误、分红接口失败）都显示成“未找到该股票”，并且 `Promise.all` 让分红接口失败也导致整页 404。
- **位置**：`web/src/pages/ranking/RankingPage.tsx:25-48`、`web/src/components/YieldChart.tsx:29-35`、`web/src/components/SearchBar.tsx:16-31`、`web/src/pages/stock/StockDetailPage.tsx:18-31`
- **影响**：用户看到空白/旧数据/错误的“未找到”，无法区分“没有数据”和“服务故障”；快速操作时结果错乱。
- **修复建议**：为每个请求增加 `error` 状态和重试按钮；用 `AbortController` 或递增 request id 忽略过期响应；详情页用 `Promise.allSettled` 或分别处理 404 与 5xx。

### M-05 分红表把所有报告期都标成“年报”

- **问题**：`{d.report_period.slice(0, 4)}年报` 只取年份，不判断报告期是 6 月 30 日还是 12 月 31 日。采集器会抓取 `XXXX0630` 中期报告，因此中期分红会被标成“年报”。
- **位置**：`web/src/components/DividendTable.tsx:40`
- **影响**：用户无法区分中期分红和年度分红，分红历史口径错误。
- **修复建议**：根据 `report_period` 的月份显示“中报/年报”，或直接显示完整报告期日期。

### M-06 TTM 分位标签写死“近 10 年”，查询没有区间

- **问题**：详情页固定显示“当前 TTM 股息率高于近 10 年 X% 的历史时间”，但 `ttmYieldPercentile()` 没有日期过滤，统计的是 `dividend_yields` 全表；如果未来回填超过 10 年，分位口径会悄悄变化。另外分位是按分红事件计数，不是按时间。
- **位置**：`web/src/pages/stock/StockDetailPage.tsx:85-89`、`worker/src/db/queries/yields.ts:45-64`
- **影响**：文案与计算不一致；用户以为这是“近 10 年时间占比”，实际是“历史分红事件占比”。
- **修复建议**：给分位查询加 `range`/`startDate` 参数并与文案一致；或者把文案改成“高于历史 X% 的分红事件”。长期按 H-02 改为日频时间序列后再恢复“历史时间”表述。

### M-07 pay_date 和 payout_ratio 永远为空

- **问题**：`collect_dividends_for_period()` 把 `pay_date` 和 `payout_ratio` 固定为 `None`；前端分红表却有“派息日”“股息支付率”两列，README 也把它们列为功能。
- **位置**：`python-collector/collect_dividends.py:158`、`python-collector/collect_dividends.py:162`、`web/src/components/DividendTable.tsx:26-27`、`web/src/components/DividendTable.tsx:50`
- **影响**：两列永远显示“—”，用户会以为数据缺失；README 的功能描述不实。
- **修复建议**：用 `stock_dividend_cninfo`（含“派息日”）补齐 `pay_date`；用 `每股收益` 计算 `payout_ratio = per_share_div / eps * 100`，或删除这两列和 README 描述。

### M-08 PE(TTM) 实际展示的是动态市盈率

- **问题**：采集器取 `stock_zh_a_spot_em()` 的“市盈率-动态”写入 `pe_ttm`，前端标签写的是“PE(TTM)”。动态市盈率和 TTM 市盈率不是同一个口径。
- **位置**：`python-collector/collect_dividends.py:198`、`web/src/pages/stock/StockDetailPage.tsx:92`
- **影响**：估值指标标签错误，可能误导投资判断。
- **修复建议**：改标签为“PE(动态)”，或改用真正的 TTM 市盈率字段/接口。

### M-09 D1 客户端只重试网络异常和 429，不重试 5xx，也没有连接复用

- **问题**：`execute_sql()` 对 `requests.RequestException` 和 429 做重试，但对 Cloudflare 的 500/502/503/504 直接 `raise D1Error`；每次请求都用模块级 `requests.post`，没有 `Session`，无法复用连接。
- **位置**：`python-collector/common/d1_client.py:52-86`
- **影响**：Cloudflare API 的瞬时 5xx 会让整批采集失败；大量请求时 TLS 握手开销显著，进一步拉长任务时间。
- **修复建议**：对 5xx 也做指数退避重试（带 jitter，设置上限）；使用 `requests.Session`；区分可重试和不可重试的 4xx。

### M-10 采集失败不退出非 0，workflow 显示绿色，故障被静默吞掉

- **问题**：价格采集在失败率 >20% 时只写 `system_settings`，仍然正常退出；分红采集对每个报告期的异常只 `log.warning` 并返回 0，最终也正常退出。GitHub Actions 不会把任务标红，也没有告警。
- **位置**：`python-collector/collect_prices.py:188-198`、`python-collector/collect_dividends.py:122-126`、`python-collector/collect_dividends.py:357-363`
- **影响**：上游接口变更、限流、D1 故障都可能让数据静默停止更新；只有用户发现数据不对时才会知道。
- **修复建议**：设置失败阈值后 `sys.exit(1)` 或抛异常；在 workflow 中加失败通知（GitHub Issue/邮件/Slack）；把 `system_settings` 的健康标记暴露到 `/api/health` 或监控。

### M-11 KV 故障没有降级，缓存读写失败会让 API 500

- **问题**：`getCached()`/`setCached()` 没有 try/catch；KV 读失败会抛异常，KV 写失败会让已经查完 D1 的请求也返回 500。
- **位置**：`worker/src/services/cache.ts:3-20`、`worker/src/routes/stocks.ts:40-56`
- **影响**：KV 短暂不可用时，排行接口整体不可用；缓存本应是可选优化。
- **修复建议**：缓存读写包 try/catch，失败时记录日志并继续走 D1；`setCached` 可以不阻塞响应（`waitUntil` 或后台任务）。

### M-12 CORS 只允许同源，前后端分离部署会失败；credentials 没有实际用途

- **问题**：`origin` 回调只允许 `origin === new URL(c.req.url).origin`，即同源。README 的部署方式是把前端 assets 挂在同一个 Worker 上，所以当前能用；但如果前端部署到 Pages 或独立域名，所有 API 请求都会被浏览器拦截。`credentials: true` 在没有 Cookie/认证的只读 API 上没有意义。
- **位置**：`worker/src/index.ts:18-28`
- **影响**：部署拓扑一变就出现 CORS 故障；`credentials` 增加了不必要的预检约束。
- **修复建议**：用 `CORS_ORIGINS` 环境变量配置允许的来源；没有认证就移除 `credentials: true`；同源部署可以完全去掉 CORS 中间件。

### M-13 920xxx 北交所代码会被误判为上海

- **问题**：`infer_exchange()` 把 `9` 开头的代码一律判为 `SH`，但北交所自 2024 年起启用 `920xxx` 代码段；这些股票会生成 `sh920xxx`，导致价格接口取不到数据。
- **位置**：`python-collector/common/ticker_utils.py:40-53`
- **影响**：北交所新股/改码股票的价格和历史数据缺失；`exchange` 字段也错误。
- **修复建议**：把 `920` 前缀单独映射到 `BJ`；增加 `920xxx` 测试用例。建议在真实 `stock_info_a_code_name()` 上确认当前是否已有 920 段（见“待验证”清单）。

### M-14 方案进度为 NaN 时 `parse_dividend_status()` 会抛 TypeError

- **问题**：`if not raw` 对 `NaN` 为假（`bool(NaN)` 为真），随后执行 `"实施" in raw`，如果 `raw` 是 float 会抛 `TypeError: argument of type 'float' is not iterable`。
- **位置**：`python-collector/collect_dividends.py:105-117`
- **影响**：某个报告期只要有一行 `方案进度` 为 NaN，整个报告期采集中断。AKShare 文档示例中该列都是字符串，所以标记为待验证；但代码没有防御。
- **修复建议**：先判断类型：`if not isinstance(raw, str): return "proposal"`；或使用 `pd.isna(raw)`。

### M-15 两个采集 workflow 没有 concurrency，可能重叠运行

- **问题**：价格任务每 30 分钟触发一次、超时 25 分钟，分红任务每天一次、超时 55 分钟；workflow 没有 `concurrency` 组。GitHub Actions 的 cron 可能延迟，前一轮未结束时后一轮可能启动。
- **位置**：`.github/workflows/collect-prices.yml:35-39`、`.github/workflows/collect-dividends.yml:17-20`
- **影响**：两轮同时抓取上游、同时写 D1，增加限流和锁竞争；价格任务可能重复处理同一批股票。
- **修复建议**：增加 `concurrency: { group: collect-prices, cancel-in-progress: false }`（分红同理），让新任务排队而不是并行。

### M-16 测试没有覆盖真正的收益率逻辑，mock 测试还掩盖了 D1 参数限制

- **问题**：
  - `test_yield_computation.py` 名为“收益率计算”，实际只测试 `price_on_or_before()`、`report_periods()` 长度、`parse_dividend_status()`；没有静态/TTM/连续年数/前视/中期分红的测试。
  - `test_d1_client.py` 用 100 行 × 2 列 = 200 个参数测试批量写入，mock 掉了 `requests.post`，所以不会触发 D1 的 100 参数限制；它反而固化了“150/100 行一批没问题”的错误假设。
  - 没有 `collect_prices` 跳过逻辑、NaN 清洗、分页参数、缓存键的测试。
- **位置**：`python-collector/tests/test_yield_computation.py:20-64`、`python-collector/tests/test_d1_client.py:50-65`
- **影响**：B-01、B-02、B-03、H-01、H-03 这些核心问题都不会被现有测试发现。
- **修复建议**：按“一个 bug 一个回归测试”补：D1 参数上限、报告期默认值、NaN 送转、静态/TTM 年度口径、连续年数、价格跳过逻辑；Worker 侧补查询构造/缓存键测试。

### M-17 README 与仓库实际不一致，PRD.md 缺失且被 gitignore

- **问题**：README 多处链接 `PRD.md`，但仓库没有该文件，`.gitignore:30` 明确忽略它；README 还宣称了未实现的筛选、未落地的字段、并不存在的部署 workflow。
- **位置**：`README.md:7`、`README.md:11`、`README.md:23`、`README.md:24`、`README.md:34`、`README.md:100`、`README.md:137-142`、`README.md:148-151`、`.gitignore:30`、`README.en.md:7-34`
- **影响**：新成员无法按文档完成部署/开发；用户以为某些筛选/字段可用；PRD 链接 404。
- **修复建议**：决定 PRD 是否公开：公开就提交文件并移除 `.gitignore` 条目；不公开就删除所有链接，把关键需求摘要放进 README。把“规划中/未验证”明确标注，部署步骤按 H-08 修正。

### M-18 公开 API 没有限流和安全响应头

- **问题**：Worker 没有 `secureHeaders`（CSP、X-Frame-Options、Referrer-Policy 等），也没有请求限流；`/api/stocks/search` 不缓存，直接打 D1。
- **位置**：`worker/src/index.ts:8-35`、`worker/src/routes/stocks.ts:59-67`
- **影响**：搜索接口可被脚本高频调用，增加 D1/KV 成本；缺少基本的安全头，存在点击劫持/MIME 嗅探等低风险面。
- **修复建议**：加 `hono/secure-headers`；对 `/api/*` 做基于 IP/KV 或 Durable Object 的简单限流；给搜索加短 TTL 缓存或最小查询长度。

---

## 4. 低危 / 建议

| 编号 | 问题 | 位置 | 影响 | 修复建议 |
|---|---|---|---|---|
| L-01 | `buildWhereClause()` 的 `prefix` 参数未使用，lint 报 warning | `worker/src/db/queries/stocks.ts:3` | 噪声；暗示重构未完成 | 删除参数或改为 `_prefix` |
| L-02 | `rangeToStartDate()` 用本地时间 `setFullYear` 后转 UTC 字符串 | `worker/src/db/queries/yields.ts:5-11` | 时区边界可能差一天 | 用 UTC 日期或直接按年数减 365 天 |
| L-03 | `formatUpdatedAt()` 只显示时分，不显示日期/时区 | `web/src/lib/utils.ts:28-34` | 数据过期数天时仍显示“更新于 08:00” | 显示日期时间并标注时区 |
| L-04 | 排行表整行 `onClick` 打开详情，没有键盘/焦点支持；排序按钮没有 `aria-sort` | `web/src/components/StockTable.tsx:29-40`、`:54-59` | 键盘/读屏用户无法打开详情 | 用 `<Link>` 包裹名称，或给行加 `tabIndex`/`role`/`onKeyDown`；表头加 `aria-sort` |
| L-05 | 搜索框没有 `<label>`/`aria-label`，结果下拉不是 listbox，没有方向键/Escape | `web/src/components/SearchBar.tsx:59-85` | 可访问性不足 | 加 `aria-label`、`role="listbox"`/`option`、键盘导航 |
| L-06 | 图表没有文本替代/`aria-label` | `web/src/components/YieldChart.tsx:63-121` | 读屏用户无法理解图表 | 加 `role="img"` + `aria-label`，或提供数据表 |
| L-07 | Web 产物 679 kB（gzip 193 kB），Recharts 全量打包，无代码分割 | `web/vite.config.ts`、构建输出 | 首屏加载偏慢 | 对图表做 `React.lazy`/动态 import，或换轻量图表库 |
| L-08 | 死代码/未使用依赖：`latestPrice()`、`/stocks/:code/prices` 前端未调用、`APP_VERSION`、`cninfo_limiter`、`python-dotenv`（没有 `load_dotenv()`，导致 README 的 `.env` 本地配置不生效） | `worker/src/db/queries/prices.ts:34-46`、`worker/src/routes/stock-detail.ts:29-36`、`worker/src/env.d.ts:2`、`python-collector/common/rate_limiter.py:39`、`python-collector/requirements.txt:4` | 维护成本、误导；本地采集必须手动 export 环境变量 | 删除或真正使用；在采集脚本入口调用 `load_dotenv()` |
| L-09 | `sys.path.insert` 插入的是仓库根目录，而不是 `python-collector` 目录 | `python-collector/collect_prices.py:27`、`collect_dividends.py:32` | 路径插入冗余/不正确；以模块方式导入时可能找不到 `common` | 改为 `os.path.dirname(os.path.abspath(__file__))` |
| L-10 | 使用 `datetime.utcnow()`（Python 3.12+ 已弃用） | `python-collector/collect_prices.py:50`、`:146`、`:189`；`collect_dividends.py:48`、`:131`、`:193`、`:251` | 未来 Python 版本告警/行为变化 | 改用 `datetime.now(timezone.utc)` |
| L-11 | schema 没有 `CHECK` 约束、没有迁移版本表；`dividends.id` 注释说 UUID，实际是 `code-report_period` | `worker/src/db/schema.sql:61-76` | 脏数据无法在 DB 层拦截；文档误导 | 加 `CHECK (market IN ('A','HK'))`、`CHECK (status IN (...))`；引入 migrations 目录 |
| L-12 | seed 数据自身不一致：`pay_date` 早于 `ex_div_date`，`last_dividend_date` 比分红表最新记录晚一年；seed 用普通 `INSERT`，重复执行会主键冲突 | `worker/seed.sql:2`、`:139` | 本地演示数据自相矛盾；重复 seed 失败 | 修正样例数据；改为 `INSERT OR REPLACE` 或先清表 |
| L-13 | GitHub Actions 使用 `@v4`/`@v5` 标签而非 SHA；没有 `permissions: contents: read` | `.github/workflows/collect-prices.yml:40-42`、`collect-dividends.yml:22-24` | 供应链风险；默认 token 权限偏大 | pin SHA；加最小 `permissions` |
| L-14 | `compatibility_flags = ["nodejs_compat"]` 但 Worker 没有使用 Node API | `worker/wrangler.toml:4` | 不必要的兼容层 | 移除 |
| L-15 | `min_yield=abc` 得到 `NaN`，`1e999` 得到 `Infinity`；`page` 无上限 | `worker/src/routes/stocks.ts:10`、`:24-27` | 空结果或 D1 绑定错误；大 OFFSET 扫描 | 校验 `Number.isFinite` 和范围；page 上限 |
| L-16 | 非 A 市场一律显示“港股通” | `web/src/pages/stock/StockDetailPage.tsx:48` | 未来新增市场时标签错误 | 用明确映射，未知显示“其他” |
| L-17 | API 响应没有 `Cache-Control`/安全头 | `worker/src/index.ts:8-35` | 边缘/浏览器缓存行为不明确；安全加固不足 | 对公开只读接口加 `Cache-Control: public, max-age=...`；加 `secureHeaders` |
| L-18 | `README` 说“两个 workflow 会在推送后按 cron 自动运行”，cron 只在默认分支按计划运行，不是 push 触发 | `README.md:139-142` | 文档误导 | 改为“合并到默认分支后按 cron 运行” |
| L-19 | `searchStocks()` 的 `code LIKE ?` 使用前缀匹配，`name LIKE ?` 使用包含匹配；中文/大小写行为未定义 | `worker/src/db/queries/stocks.ts:102-110` | 搜索结果可能与预期不一致 | 明确搜索语义；必要时 `LOWER()`/转义 |
| L-20 | Web 没有 ErrorBoundary，任一组件渲染异常会导致整页白屏 | `web/src/main.tsx:10-15`、`web/src/App.tsx:6-15` | 局部错误放大为整站不可用 | 增加 ErrorBoundary，至少包住路由内容 |
| L-21 | 分红主键是 `code-report_period`，同一报告期若有多次分红（特别分红/多次中期）会被 `INSERT OR REPLACE` 合并；待验证 AKShare 是否返回多行（见 V-08） | `python-collector/collect_dividends.py:152`、`worker/src/db/schema.sql:62` | 潜在分红记录丢失 | 用 `code-report_period-ex_date` 或 UUID 作为主键 |
| L-22 | `web/tsconfig.tsbuildinfo` 被 tracked，`.gitignore` 没有忽略 `*.tsbuildinfo` | `web/tsconfig.tsbuildinfo`、`.gitignore` | 构建产物污染提交历史 | 删除并加入 `.gitignore` |
| L-23 | `biome.json` schema 版本 2.5.0 与 CLI 2.5.4 不一致 | `biome.json:2` | 每次 lint 输出 info，配置校验不严格 | 执行 `biome migrate` 或更新 schema URL |
| L-24 | 纯送转（无现金分红）记录被 `if per_share_div <= 0: continue` 丢弃，分红历史不完整 | `python-collector/collect_dividends.py:143-144` | 用户看不到送股/转增方案 | 保留 `bonus_share_ratio`/`transfer_ratio` 大于 0 的行，`per_share_div` 置 0 |

---

## 5. 待验证清单

以下结论依赖生产数据/上游接口行为，代码路径已定位，但需要在真实环境确认。每项给出验证方法。

| 编号 | 待验证事项 | 关联 | 验证方法 |
|---|---|---|---|
| V-01 | `stock_fhps_em(date="20261231")` 在当前日期是否为空 | B-02 | 在 GitHub Actions runner 执行 `python -c "import akshare as ak; df=ak.stock_fhps_em(date='20261231'); print(df.shape, df.head())"`；若为空，B-02 成立 |
| V-02 | 新浪 `stock_zh_a_daily` 在盘中是否返回当日行、`close` 是否为盘中价 | B-04 | 交易日 10:00 执行 `ak.stock_zh_a_daily(symbol="sh600519", start_date=今天, end_date=今天)`，检查是否含当日行及 `close` 是否等于实时价 |
| V-03 | `方案进度` 是否会出现 NaN | M-14 | `df=ak.stock_fhps_em(date="20251231"); print(df["方案进度"].isna().sum())` |
| V-04 | `stock_info_a_code_name()` 是否已包含 920xxx 北交所代码 | M-13 | `df=ak.stock_info_a_code_name(); print(df[df["code"].astype(str).str.startswith("920")].head())` |
| V-05 | `github.event.inputs.backfill_batch != ''` 在 `schedule` 事件下是否为 `false` | B-05、workflow | 在 workflow 中临时加 `run: echo "mode=${{ github.event.inputs.backfill_batch != '' }}"`，手动触发一次 schedule（或看下一次定时运行日志）；更稳妥的写法是 `github.event_name == 'workflow_dispatch' && github.event.inputs.backfill_batch != ''` |
| V-06 | `stock_fhps_em` 的日期列返回 `datetime.date` 还是带时间的 `Timestamp` | M-07、H-01 | `df=ak.stock_fhps_em(date="20251231"); print(type(df["除权除息日"].iloc[0]), df["除权除息日"].head())`；若带 `00:00:00`，`str()` 会污染 `trade_date`/`ex_div_date` 的字典序比较 |
| V-07 | `stock_zh_a_daily` 对 `bj` 前缀的北交所股票是否可用 | M-13 | `ak.stock_zh_a_daily(symbol="bj430017", start_date="20240101", end_date="20240131")`，检查是否返回数据 |
| V-08 | 同一报告期是否存在同一代码多行（特别分红/多次中期） | L-21 | `df=ak.stock_fhps_em(date="20251231"); print(df[df["代码"].astype(str)=="600519"].shape)`，看行数是否大于 1 |
| V-09 | `送转股份-送转比例`/`送转股份-转股比例` 在无送转时返回 `NaN` 还是 `0` | B-03 | `df=ak.stock_fhps_em(date="20251231"); print(df[["送转股份-送转比例","送转股份-转股比例"]].isna().mean())` |

> 说明：D1 的“100 个绑定参数”限制和 `requests` 的 `allow_nan=False` 已分别通过官方文档/源码确认，不需要再验证。

---

## 6. 建议修复顺序

### P0：先让数据链路可用（阻断级）

1. **B-01**：改 `upsert_rows()` 批大小/改用 D1 batch，确保任何表都不超过 100 个绑定参数。
2. **B-03**：清洗所有 `NaN`/`Inf`，避免 `requests` 序列化崩溃。
3. **B-02**：修正报告期生成与默认值，确保日常运行能抓到最近已披露的报告期。
4. **B-04 + B-05**：明确“盘中/收盘”语义，增加游标/分片，保证全市场收盘价能在超时内完成。
5. **B-06**：统一 `TABLE_PREFIX` 的迁移/seed/运行时规则，或删除该特性。

**验收标准**：在真实 D1 上跑一次 `BACKFILL_PERIODS=20` 和一次价格全量/增量，`stocks`、`dividends`、`dividend_yields` 都有数据；`TABLE_PREFIX=yl` 时迁移+查询+采集全部通过。

### P1：修正核心指标与部署（高危）

1. **H-01/H-03**：静态股息率年度口径、连续分红年数。
2. **H-02**：日频/月频收益率序列与分位口径（可与 H-01 一起重构）。
3. **H-04/H-05**：前端 10 倍金额、`min_yield` 绑定排序列。
4. **H-06/H-07**：industry、is_st、name 的更新链路。
5. **H-08**：部署脚本跨平台、配置不被覆盖、占位符顺序。
6. **H-09**：分红重算批量化/增量化和超时。
7. **H-10**：CI + 部署 workflow + 最小测试集。

**验收标准**：连续 10 年分红公司的 `consecutive_dividend_years=10`；`min_yield` 在任意 `sort` 下都按股息率过滤；前端分红方案金额与“10 股”口径一致；`pnpm lint/typecheck/build` 和 Python 测试在 CI 中全绿；`pnpm deploy` 在 Linux 上可执行。

### P2：健壮性与体验（中危）

- M-01 稳定排序、M-02 API 404、M-03 输入限制/缓存键、M-04 前端错误态/竞态、M-05 中报标签、M-06 分位文案、M-07 派息日/支付率、M-08 PE 标签、M-09 5xx 重试、M-10 失败退出、M-11 KV 降级、M-12 CORS、M-13 920 段、M-14 NaN 状态、M-15 concurrency、M-16 测试、M-17 README、M-18 限流/安全头。

### P3：清洁度（低危）

- L-01 到 L-24：lint 清理、时区/日期格式、a11y、死代码、依赖、schema 约束、seed、Actions pin、文档措辞。

---

## 7. 审查覆盖与限制

- **已阅读**：`README.md`、`README.en.md`、根 `package.json`、`pnpm-workspace.yaml`、`biome.json`、`tsconfig.base.json`、`worker/` 全部源码/配置、`worker/seed.sql`（结构 + 数量抽查）、`web/` 全部源码/配置、`python-collector/` 全部源码/测试/配置、`.github/workflows/` 两个 workflow、`.gitignore`。
- **未执行**：真实 Cloudflare D1/KV 调用、AKShare 上游调用、GitHub Actions 运行；因此涉及上游返回结构/盘中行为的结论已标为“待验证”。
- **已执行**：`pnpm typecheck`、`pnpm lint`、`pnpm build`；用 Python 复现了 `report_periods()`、连续分红年数循环、静态股息率前视示例。
- **未修改**：本次只新增本报告，没有修改任何业务代码、配置或依赖。

---

## 8. 附：关键证据索引

| 结论 | 代码位置 |
|---|---|
| D1 批量 150 行 | `python-collector/common/d1_client.py:22`、`:88-115` |
| 报告期从当前年开始 | `python-collector/collect_dividends.py:93-102`、`:335-340` |
| 送转 NaN | `python-collector/collect_dividends.py:160-161` |
| 价格跳过逻辑 | `python-collector/collect_prices.py:106-112` |
| 25 分钟超时 | `.github/workflows/collect-prices.yml:38`、`:8-15` |
| 前缀迁移 | `worker/package.json:10`、`:12`、`worker/src/types.ts:12-16` |
| 静态股息率 | `python-collector/collect_dividends.py:260-266`、`:291-296` |
| 连续分红年数 | `python-collector/collect_dividends.py:305-313` |
| 前端 10 倍金额 | `web/src/components/DividendTable.tsx:6-11` |
| min_yield 绑排序 | `worker/src/db/queries/stocks.ts:18-25` |
| industry 未写入 | `python-collector/collect_dividends.py:174-214` |
| is_st/name 不更新 | `python-collector/collect_dividends.py:82-88` |
| 部署脚本 | `package.json:16`、`worker/wrangler.toml:5-21` |
| 无 CI/部署 | `.github/workflows/`、`README.md:24` |
| PRD 缺失 | `README.md:7`、`.gitignore:30` |

---

## 9. 外部依据

| 依据 | 链接 |
|---|---|
| Cloudflare D1 Limits（单查询最多 100 个绑定参数、LIKE pattern 50 字节、单查询 30 秒） | <https://developers.cloudflare.com/d1/platform/limits/> |
| AKShare 股票数据文档（`stock_fhps_em`、`stock_zh_a_spot_em`、`stock_zh_a_daily`、`stock_dividend_cninfo` 的列定义） | <https://akshare.akfamily.xyz/data/stock/stock.html> |
| `requests` 的 JSON 序列化行为（`allow_nan=False`） | <https://github.com/psf/requests/blob/main/src/requests/models.py> |
| GitHub Actions 表达式/上下文（`github.event.inputs` 在非 `workflow_dispatch` 事件下的取值） | <https://docs.github.com/en/actions/learn-github-actions/contexts> |

---

*报告完。修复建议中的代码片段仅用于说明方向，本次未改动业务代码。*
