# 自建 GitHub Actions Runner（P3685.local）

两个采集 workflow 从 GitHub 托管 runner 迁到团队自己的 Mac（`P3685.local`）上
执行。原因：上游行情站点会拦截 GitHub 数据中心的出口 IP，而这台机器可以正常
访问东方财富/新浪的接口。

建表 workflow（`apply-schema.yml`）不涉及上游站点，继续留在 GitHub 托管
runner 上，改动 D1 schema 时不需要占用这台机器。

## 安装概况

| 项目 | 值 |
|---|---|
| 仓库 | `WorkerHub/YieldLens`（仓库级 runner，非组织级） |
| 名称 | `P3685-yieldlens` |
| 标签 | `self-hosted`、`macOS`、`ARM64`（内置）+ `yieldlens`（自定义） |
| 版本 | 2.338.0（`actions-runner-osx-arm64-2.338.0.tar.gz`） |
| 安装目录 | `~/actions-runners/yieldlens` |
| 服务 | 用户级 LaunchAgent `actions.runner.WorkerHub-YieldLens.P3685-yieldlens` |
| 注册时间 | 2026-10-20 |

安装包来自 GitHub 官方 release，下载后按 release 说明里的 SHA-256 校验
（`df4cebda25c86a886ed204e49fee63f5c2e7cec5f447b5c98440a826bbdf9df2`）。
注册令牌只作为 `config.sh --token` 的参数使用一次，没有写入仓库、日志或文档。

## 服务管理

```bash
cd ~/actions-runners/yieldlens
./svc.sh status     # LaunchAgent 是否在运行
./svc.sh start
./svc.sh stop
./svc.sh uninstall  # 停止并移除 LaunchAgent（不删除 runner 注册）
```

- LaunchAgent：`~/Library/LaunchAgents/actions.runner.WorkerHub-YieldLens.P3685-yieldlens.plist`
- runner 自身日志：`~/actions-runners/yieldlens/_diag/Runner_*.log`
- 服务输出：`~/Library/Logs/actions.runner.WorkerHub-YieldLens.P3685-yieldlens/`

确认在线（应看到 `status: online`、`busy: false`、标签包含 `yieldlens`）：

```bash
gh api repos/WorkerHub/YieldLens/actions/runners \
  --jq '.runners[] | {name,status,busy,labels:[.labels[].name]}'
```

## workflow 改动

`collect-prices.yml` 与 `collect-dividends.yml`：

```yaml
jobs:
  reject-untrusted:
    if: github.event_name != 'schedule' && github.event_name != 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - name: Refuse untrusted event
        run: |
          echo "::error::Refusing to run the self-hosted collector for event '${{ github.event_name }}'; only schedule and workflow_dispatch are allowed."
          exit 1

  collect:
    if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'
    runs-on: [self-hosted, yieldlens]
```

- `collect` 的 job 级 `if` 让不可信事件在**分配 runner 之前**就被拒绝，自建机器
  完全不会收到该 job；
- `reject-untrusted` 在 GitHub 托管 runner 上跑，把这类运行标红并写明原因，避免
  只显示一个「skipped」让人误以为没事；
- `apply-schema.yml` 保持 `runs-on: ubuntu-latest`。

### Python 环境（自建 runner 专用）

两个采集 job 不再使用 `actions/setup-python`：该 action 在 macOS 上把工具缓存
硬编码成 `/Users/runner/hostedtoolcache`（v5–v7 都一样），自建 runner 以普通用户
`mapan` 运行时无法创建 `/Users/runner`，安装步骤会直接失败：

```
##[error]mkdir: /Users/runner: Permission denied
```

改为使用机器上已有的 `uv`（`~/.local/bin/uv`，已在 runner 的 `.path` 里）：

```bash
uv venv --python 3.11 .venv
echo "$PWD/.venv/bin" >> "$GITHUB_PATH"
uv pip install --python .venv/bin/python -r python-collector/requirements.txt
```

首次运行会把 CPython 3.11 下载到 `~/.local/share/uv/python`，之后复用；wheel
缓存位于 `~/.cache/uv`。机器上若没有 `uv`，用
`curl -LsSf https://astral.sh/uv/install.sh | sh` 安装（job 里也会先检查并给出
同样的提示）。

## 安全：公开仓库上的自建 runner

仓库是公开的，自建 runner 绝不能执行外部贡献者可以改动的代码。当前配置：

- 两个采集 workflow 只声明了 `schedule` 与 `workflow_dispatch`，没有
  `pull_request` 触发器；
- job 级 `if` 只放行这两个事件；
- `permissions: contents: read`，job 只拿到只读 `GITHUB_TOKEN`；
- 组织级 secret `CLOUDFLARE_API_TOKEN` 只在这两个 job 里注入。

workflow 文件本身在 `pull_request` 事件下来自 PR 分支，所以任何写在 workflow
里的守卫都不是安全边界。真正的边界是「不让这类事件进入自建 runner」，因此还
需要在仓库设置里确认：

1. **Settings → Actions → General → Fork pull request workflows**：选择
   *Require approval for all outside collaborators*（外部 PR 一律先人工批准）；
2. 不要给这两个 workflow 添加 `pull_request` / `pull_request_target` 触发器；
3. 定期在 **Settings → Actions → Runners** 检查是否有多余的 runner 被注册。

## 验证

### runner 注册

2026-10-20 18:31 CST，`gh api repos/WorkerHub/YieldLens/actions/runners` 返回：

```
{"busy":false,"id":21,"name":"P3685-yieldlens","os":"macOS","status":"online",
 "version":"2.338.0",
 "labels":["self-hosted","macOS","ARM64","yieldlens"]}
```

### 两次真实运行

在分支上触发（合并前即可验证，`--ref` 用当前工作分支）：

```bash
gh workflow run collect-dividends.yml --ref <branch> -f mode=daily
gh workflow run collect-prices.yml    --ref <branch> -f mode=snapshot

gh run list --workflow=collect-dividends.yml --limit 3
gh run list --workflow=collect-prices.yml --limit 3
gh run view <run-id> --log
```

首次尝试记录（分支 `tds/conv-01a158dc-…`，commit `d400142`）：

| 运行 | run id | 结果 | 耗时 | 说明 |
|---|---|---|---|---|
| 分红 #1 | 39409808041 | ❌ | 81s | `actions/setup-python` 失败：`mkdir: /Users/runner: Permission denied` |
| 分红 #2 | 39410122170 | ❌ | 89s | 同上；runner `.env` 里加 `RUNNER_TOOL_CACHE` 无效，action 会覆盖 |
| 分红 #3 | 39410447975 | ❌ | 81s | 同上；加 `AGENT_TOOLSDIRECTORY` 也无效（见「Python 环境」） |

三次运行都确认 job 落在 `P3685-yieldlens` 上（`reject-untrusted` 正常 skipped），
失败点只在 Python 安装步骤。改用 `uv` 后的验证记录补充到下面。

判读标准：

- 运行详情里 **Runner** 显示 `P3685-yieldlens`，而不是 `GitHub Actions N`；
- 分红运行日志出现 `Loaded N A-share codes`、报告期处理与 `YL_dividends` 写入统计；
- 股价运行日志出现 `Price collection mode: snapshot` 与
  `Snapshot ... rows=... updated=...`；若快照接口失败，应看到
  `Snapshot mode failed (...); falling back to sharded history mode`，随后是逐股
  回退的 checkpoint 日志；
- 写入结果用只读 SQL 复核（Cloudflare 控制台 → D1 → 共享库）：

```sql
SELECT COUNT(*) FROM YL_stocks WHERE last_price IS NOT NULL;
SELECT COUNT(*) FROM YL_stock_prices WHERE trade_date = date('now', '+8 hours');
SELECT COUNT(*) FROM YL_dividends;
```

> 运行记录（run id / 耗时 / 写入统计）在首次真实验证后补充到本节。

### 上游连通性（本机实测，2026-10-20 18:40 CST）

| 接口 | 用途 | 结果 |
|---|---|---|
| `ak.stock_fhps_em(date=20260630)` | 分红/送转（东方财富 datacenter） | ✅ 1044 行 |
| `ak.stock_zh_a_daily("sh600000")` | 逐股回退（新浪） | ✅ 含 2026-10-20 收盘 9.76 |
| `82.push2.eastmoney.com/api/qt/clist/get` | 全市场快照（东方财富） | ⚠️ 前几次请求返回真实行情（沪市主板 `total=1642`），连续探测约 6 次后开始返回 `Empty reply from server` |

`push2his.eastmoney.com`（历史 K 线）与 `quote.eastmoney.com` 始终 200，
换一个出口 IP（本机 SOCKS 代理）也仍能拿到 200，说明不是网络整体不通，而是
**本机直连出口**被实时行情接口按 IP 限流。采集任务每 30 分钟只跑一次快照
（akshare 分页之间还有 0.5–1.5s 随机间隔），不会像探测脚本那样连续请求；若仍
被限流，`collect_prices.py` 会记录
`Snapshot mode failed (...); falling back to sharded history mode` 并走逐股
回退（新浪接口，已实测可用）。首次真实运行时以日志为准。

## 机器睡眠 / 关机时的行为

- runner 是用户级 LaunchAgent，**随登录启动**：重启后要有人登录这台 Mac，
  服务才会恢复；只开机不登录时 runner 处于离线。
- 机器睡眠/关机期间任务不会失败，而是在 GitHub 侧排队等待 runner 上线；
  排队中的 job 最多保留 **24 小时**，超时会被自动取消（GitHub Actions limits，
  *Self-hosted → Job queue time*）。
- 所以长时间合盖会丢当天的采集窗口。建议：
  - 台式机/常开 Mac：系统设置 → 锁定屏幕 → 「显示器关闭时…防止自动进入睡眠」；
  - 笔记本：`sudo pmset repeat wakeorpoweron MTWRFSU 08:55:00` 之类定时唤醒
    （交易日 09:30、15:10 与 16:00 前后需要在线），或改为接电源常开；
  - 需要补跑时直接 `gh workflow run ...` 手动触发即可。

## 仍需人工完成

1. **首次登录后的后台项目批准**：若 macOS 提示「已添加后台项目」，打开
   系统设置 → 通用 → 登录项，允许 `actions.runner.WorkerHub-YieldLens.P3685-yieldlens`。
2. **两次真实验证运行**（见上节），确认写入 `YL_stocks.last_price` 与
   `YL_stock_prices`。
3. **合并到 `main` 后**确认定时任务生效：Actions 页面里两个采集 workflow 的
   下一次 `schedule` 运行应显示 runner 为 `P3685-yieldlens`。
4. **仓库设置**：按「安全」一节的第 1 条确认外部 PR 需要人工批准。

## 升级 / 卸载

升级 runner（保留 `.runner`、`.credentials*`、`.env`）：

```bash
cd ~/actions-runners/yieldlens
./svc.sh stop
# 下载并校验新版本 actions-runner-osx-arm64-<version>.tar.gz 后解压覆盖本目录
./svc.sh start
```

彻底移除：

```bash
cd ~/actions-runners/yieldlens
./svc.sh uninstall
# 再到 GitHub: Settings → Actions → Runners → P3685-yieldlens → Remove
# 最后删除 ~/actions-runners/yieldlens
```
