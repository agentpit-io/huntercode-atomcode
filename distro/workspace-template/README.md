# HunterCode 投研工作区

这是 **HunterCode · AtomCode 发行版（HCA）** 的研究工作区。
AtomCode daemon 把它当作当前项目目录，你在对话里让它做的每件事都落在这里。

## 目录

| 目录 / 文件 | 说明 |
|---|---|
| `.atomcode.md` | 项目指令（投研人设）。AtomCode 会把它**追加**进系统提示 |
| `.mcp.json` | MCP 数据工具挂载。容器启动时由 `deploy/daemon/hca-init.py` 渲染，密钥来自环境变量 |
| `.hooks.json` · `.hooks/` | hook 注册与脚本。M1 只有审计，M2 补 guard / lang |
| `.atomcode/skills/` | 6 个投研技能（由仓库 `tools/build_skills.py` 从 `skills/` 生成，**不要手改**） |
| `.atomcode/audit.jsonl` | 审计日志，每次工具调用一行（运行时生成） |
| `holdings/` | 持仓与交易记录 |
| `theses/` | 投资论点：每只票一份，写清买入理由与**证伪条件** |
| `factors/` | 因子定义与回测结果 |
| `reports/` | 生成的研究报告 |
| `scripts/` | 一次性量化脚本；依赖见 `requirements.txt` |

## 数据工具

`.mcp.json` 挂了 9 个 MCP，分两类：

**不依赖 HunterCode 后端**（单机也能用）

| MCP | 工具 | 需要 key |
|---|---|---|
| `akshare` | `akshare_search` `akshare_signature` `akshare_call` | 不需要 |
| `kronos` | `kronos_health` `kronos_predict` | 需要 `KRONOS_API_KEY` |
| `truesource` | `truesource_procurement` `truesource_macro` `truesource_daily_brief` `truesource_alert_signals` `truesource_report` `truesource_scout` | 需要 `HUNTER_API_KEY` |

**薄代理，反调 HunterCode 后端 `${HERMES_API_URL}/api/internal/*`**

| MCP | 工具 |
|---|---|
| `uzi` | `stock_deep_analysis` |
| `watchlist` | `stock_quickview` `stock_news` `watchlist_digest` `watchlist_rank` `watchlist_add` `watchlist_add_batch` |
| `portfolio` | `portfolio_rebalance` `portfolio_stress` `update_risk_profile` |
| `screener` | `market_screen` |
| `hunter_cap` | `skill_repo_open` `skill_repo_read` `skill_stage` `skill_staged` |
| `hunter_user` | `list_my_sources` `invoke` |

在 AtomCode 里，MCP 工具名带前缀：`mcp__<服务名>__<工具名>`，
例如 `mcp__akshare__akshare_call`、`mcp__uzi__stock_deep_analysis`。

## 两个必须知道的限制（M0 实测）

1. **`POST /chat` 不挂载 MCP 工具，只有 `GET /live` + `POST /live/message` 挂载。**
   在 `/chat` 上调 MCP 工具会返回 `unknown or unmounted tool`。要行情就得走 `/live`。
2. **一个 daemon 进程的 `/live` 只服务一个工作目录**（`/live/message` 没有 `working_dir` 字段）。
   多工作区要一个工作区起一个 daemon。

## 跑量化脚本

```bash
pip install -r requirements.txt
python scripts/你的脚本.py
```

容器里已经预装了 `requirements.txt` 里的包（见 `/opt/hca/venv`；六个 hunter 系 MCP 另有一个 `/opt/hca/venv-hunter`，那边钉的是 mcp 1.x），
直接 `bash` 调用 `/opt/hca/venv/bin/python` 即可，不用再装。
