# v0.1.0 回归报告

> 测试环境：测试服务器 `34.133.8.3`（Ubuntu 24.04 · 2 核 8G · 磁盘 120G）。
> 主部署 compose 项目 `hca`，网页 `http://34.133.8.3:3200`。
> 底座 AtomCode **5.1.0 官方二进制**，
> `sha256 40d86fa3e31980dbcdc8a6e5707c822f3953284ff76b31523789ca19988c8763`（**未 fork**）。
> 默认模型通道 OneAPI · `hunter-chat`（Gemini 3.8），经 llm-shim。
> 时间一律上海时间。
>
> **规矩**：每一项要么给出实测数字与证据路径，要么写「未测」和原因。
> 断言一律认**被测系统自己产出**的东西 —— 不认用户输入里本来就有的字样
> （M3 栽过三次，规矩写在 `tools/e2e/README.md`）。
>
> ⚠️ **耗时都偏大**：测试期间这台机器与另一条自驱链路（HunterLauncher）共用，
> load 长时间在 12～24 之间。这里的秒数用来证明"能跑通"，**不能当性能指标**。

## 汇总

| # | 回归项 | 结果 | 一句话 |
|---|---|---|---|
| 1 | 对话式投研 | ✅ | 真浏览器里问投研问题，走 MCP 取数、出富卡片、全中文、0 处买卖指令 |
| 2 | 全部技能（6 个） | ✅ **6/6** | 照前端提示语问，6 个技能全部被正确触发，工具序列里全是真实 MCP 工具 |
| 3 | 全部 MCP（9 个 / 28 工具） | ✅ **8 真实数据 + 1 期望内报错** | kronos 没配 key → 明确说去哪申请，不假装成功 |
| 4 | Kronos Artifact | ✅ | HTML 报告在 ArtifactPanel 的 iframe 里渲染出来 |
| 5 | 策略中心与回测 | 见 §5 | `/backtest` 页真跑一次 |
| 6 | 持仓研判 | 见 §6 | `/portfolio` 页 + 对话里问持仓 |
| 7 | 五组 hook / 8 条注册 | ✅ | `hooks list` 8 条、`hooks test` 9/9、自定义用例 25/25、单测 163 条 |
| 8 | 三种模型通道 | 🟡 | OneAPI ✅；official ✅ 协议路径；**Ollama ❌ 未测（磁盘不足）** |
| 9 | 会话恢复 | ✅ | 刷新后历史消息与工具卡片都从 daemon 的会话详情恢复 |
| 10 | 一键部署（含升级/回滚） | ✅ | 从零装 **407 s / rc=0**；升级与回滚各验一次 |

---

## 1. 对话式投研

**方法**：真浏览器（Playwright chromium）登录后新建对话，问一个需要取数的投研问题；
断言三件事，全部只认系统自己产出的东西：① 页面上出现工具卡片（`watchlist_*` / `akshare_*` / `mcp__*`）；
② 回答正文里没有成句英文（连续 ≥5 个英文词，白名单放过 PE/ROE/TTM 这类专业名词）；
③ 没有买卖指令（`建议买入|卖出|加仓|减仓|清仓`、`目标价`、`可以买`、`该卖`）。

结果见 §5 的 Playwright 汇总表 `R3-对话式投研`。

另有一次**不经浏览器**的同类验证（`tools/e2e/web_turn.py`，走 web→BFF→daemon 的 HTTP 路径）：
「用 `stock_quickview` 看一下 600519 现在的行情」→ 工具序列
`watchlist_stock_quickview`，正文给出结论。

## 2. 全部技能（6 个）

`tools/e2e/web_turn.py --cases skills`，问法取自各技能自己的 `hunter.prompt_tpl`
（测的是"用户照着前端提示语问，模型会不会走到这个技能"，不是"我明示让它用技能"）。

| 用例 | 触发的技能 | 工具调用 | 墙钟 | 正文 | stop_reason | 配额差值 |
|---|---|---|---|---|---|---|
| s1 深度分析（600519） | ✅ `deep_analysis` | 21 | 174.0 s | 2 289 字 | stopped | 495 452 |
| s2 投资人辩论（000001） | ✅ `investor_panel` | 12 | 90.1 s | 4 239 字 | stopped | 192 287 |
| s3 龙虎榜（002594） | ✅ `lhb_analyzer` | 9 | 64.7 s | 2 277 字 | stopped | 170 001 |
| s4 风险画像 | ✅ `risk_profile` | 2 | 38.7 s | 435 字 | stopped | 80 640 |
| s5 杀猪盘检测（300750） | ✅ `trap_detector` | 9 | 71.1 s | 1 585 字 | stopped | 226 279 |
| s6 UZI 全维扫描（600036） | ✅ `uzi` | 9 | 91.5 s | 2 615 字 | stopped | 206 322 |

**6/6 命中正确技能**；工具序列里是 `watchlist_stock_quickview`、`uzi_stock_deep_analysis`、
`kronos_kronos_predict`、`akshare_*`、`portfolio_update_risk_profile` 等真实工具，
**没有一例退化成 bash 抓数**。6 题合计约 137 万 token。
明细（每题的完整工具序列、正文开头、会话 id）：`docs/evidence/M4/skills/`。

## 3. 全部 MCP（9 个 server / 28 个工具）

`tools/e2e/mcp_smoke.py`：直连 stdio JSON-RPC 逐个真调，**零 token**。
为什么必须单独做：`/mcp/status` 的 `connected` 只说明 server 起来了、工具列出来了，
**不说明调用能拿到数据**（M1 踩过 9/9 connected 但 6 个薄代理全部 name resolution 失败）。

| server | 代表工具 | 结果 | 耗时 | 返回摘要 |
|---|---|---|---|---|
| akshare | `akshare_search` | ✅ | 7.9 s | `{"found":5,"functions":[{"func":"bond_zh_hs_cov_spot"…` |
| kronos | `kronos_health` | ⚠️ 期望内报错 | 10.3 s | `{"error":"missing_api_key","message":"没有配置 KRONOS_API_K…` |
| truesource | `truesource_macro` | ✅ | 7.9 s | `{"date":"2026-09-22","nbs_macro":[],"customs_trade":[…` |
| uzi | `stock_deep_analysis` | ✅ | 41.8 s | `{"type":"uzi_deep_analysis","code":"600519","name":…` |
| watchlist | `stock_quickview` | ✅ | 9.1 s | `{"type":"stock_quickview","code":"600519","market":"A"…` |
| portfolio | `portfolio_stress` | ✅ | 4.8 s | `{"type":"portfolio_stress","empty":true,"hint":"需要先在持仓页录入…` |
| screener | `market_screen` | ✅ | 11.0 s | `{"market":"a","universe_total":5238,…` |
| hunter_cap | `skill_staged` | ✅ | 5.5 s | `{"repo":"","total":0,"names":[]}` |
| hunter_user | `list_my_sources` | ✅ | 4.7 s | `{"sources":[],"hint":"该用户尚未接入任何外部数据源…` |

`/mcp/status` 同时实测 **9/9 connected，工具 28 个，trusted=true，blocked=[]**。
kronos 的报错是**正确行为**：没配 `KRONOS_API_KEY` 时明确告诉你去哪申请，不编数据（U-7 仍挂着）。
明细 `docs/evidence/M4/mcp-smoke.json`。

## 4. 五组 hook / 8 条注册

| 层 | 命令 | 结果 |
|---|---|---|
| 注册真的加载了 | `atomcode hooks list`（容器内，工作区目录下） | **Total 8**：PreToolUse 1 / UserPromptSubmit 3 / PostToolUse 1 / PostToolUseFailure 1 / Stop 1 / StopFailure 1 |
| 真实执行器逐点位跑 | `bash tools/hooks/hooks-test.sh` | **9/9 exit 0**，输出留档 `docs/evidence/M4/hooks-test/` |
| 自定义 payload | `bash tools/hooks/run-hook-cases.sh`（容器内）/ `python3 tools/hooks/run_hook_cases.py`（本机） | **25/25 过**，两处各跑一遍，明细 `docs/evidence/M4/hook-cases.json` |
| 单测 | `pytest tools/tests/` | **163 条全过**（其中 M4 新增 33 条） |
| guard 补漏复验 | `python3 tools/hooks/guard_old_vs_new.py` | **7 条真实绕过旧版全漏、新版全拦；3 条反误伤对照两版都放行** |

每个 hook 的单点耗时（真实 `atomcode hooks test` 报的 Duration，延迟导入优化之后）：
guard 330 ms / audit 201 ms / audit-fail 176 ms / budget(Stop) 185 ms / lang 101 ms / context 189 ms。
优化前分别是 573 / 567 / 594 / 1098 / 94 / — ms，单次工具调用的 hook 开销从约 1.14 s 降到约 0.53 s。

**审计在真实链路里确实在落**：见 §5 的 `R4-审计留痕`（回读容器里的 `audit.jsonl`，
统计带耗时、带用户标识的条数与涉及的工具）。

## 5. 真浏览器（Playwright chromium · 对着真部署跑）

两套脚本，共 17 个用例。截图在 `docs/screenshots/M4/`，脚本自己写的结论在各自的 `result.json`。

### 5.1 M3 那 10 步（`tools/e2e/m3-web.mjs`，**一个字没改**）

脚本是 M3 的验收证据，本轮原样复跑，验证 M4 的改动（五组 hook、决策 9/10、
`/live` 孤儿恢复、镜像构建参数）没有破坏已有功能。

| 用例 | 结果 | 说明 |
|---|---|---|
| 01 登录页 | ✅ | 单用户免登录已关，看到的是真的登录表单 |
| 02 登录 | ✅ | 管理员登录成功 |
| 03 新建对话 | ✅ | 侧栏「新建对话」→ 会话建出来，输入框可用 |
| 04 问一个会调 MCP 的问题 | ✅ | StockQuickviewCard 富卡片渲染出来（命中「52 周区间 / 加自选 / 深度分析」三个只有富卡片才画得出的标志）；配额差值 52 313 |
| 05 通用工具卡片 + ArtifactPanel | ✅ | 卡片可展开；「在右侧查看」把 `akshare_akshare_search` 的 INPUT/OUTPUT 原文送进 ArtifactPanel；配额差值 113 450 |
| 06 触发一个技能 | ✅ | 页面上出现 `risk_profile`、`use_skill`；配额差值 79 999 |
| 07 Kronos 预测图进 ArtifactPanel | ✅ | HTML 报告在 iframe 里渲染出来 |
| 08 刷新后会话恢复 | ✅ | 历史消息与工具卡片都从 daemon 的会话详情恢复 |
| 09 中止生成 | ✅ | 点「停止生成」后 **81 ms** 内回到可输入状态；配额差值 0 |
| 10 SSE 断线重连（浏览器层） | ❌ **失败一次** | 见下 |

**第 10 步失败的真相**（不藏）：断网 6 秒再恢复之后，① 数据层是**过**的 ——
后端这一轮的历史里有 `stock_quickview`；② 界面层刷新后点回该会话，60 秒内没看到工具卡片。
截图 `docs/screenshots/M4/m3-16-sse-after-reload.png` 说明了为什么：
**这一轮模型手里根本没有 MCP 工具** —— 它自己写道「本会话未声明挂载
`mcp__watchlist__stock_quickview` 等 MCP 行情工具」，然后用 `read_file .atomcode/audit.jsonl`、
`grep`、`bash python3 -c 'import json …jsonrpc…'` 想手搓 MCP 协议
（其中一条 `import urllib.request` 被 guard 当场拦下，截图里是红色 ✗）。
这是 **P0-10 那一族**（MCP 工具消失而 `/mcp/status` 仍 9/9 connected）在真实浏览器流程里的复现。

做过一次**受控复现但没复现出来**：「发一轮 → 中止 → 新会话 → 问一个必须走 MCP 的问题」，
新会话照常调到 `watchlist_stock_quickview`（配额差 51 486）。所以触发条件还没定位，
怀疑与"浏览器在回合中途断网"这一步有关。**没有可观测手段**：`/live` 的 snapshot 帧里
只有 messages 与会话元数据，没有当前 runtime 的工具清单 —— 转发层没法在发消息前判断
"模型手里到底有没有 MCP 工具"。已在 `docs/questions-for-atomgit.md` B12 请上游补这个字段。

