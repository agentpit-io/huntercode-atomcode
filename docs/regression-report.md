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

### 5.2 M4 新增 7 项（`tools/e2e/m4-regression.mjs`）

第一遍跑出来 **3/7**，其中 3 个是**回归脚本自己的错**、1 个是**产品真缺陷**：

| 用例 | 第一遍 | 原因 |
|---|---|---|
| R1 持仓研判 | ⚠️ 假过 | 点完「新建对话」没等前端重挂输入框就 `fill()` → 文字被清掉 → 发送按钮禁用 → 点击无效；而断言只认「回答里有"持仓"两个字」，那两个字在**用户自己那句里就有**。表现：4.4 秒、配额差值 0、截图是空白首页 |
| R2 策略中心与回测 | ❌ 真缺陷 | `/api/backtest/config` 与 `/backtest/run/status` **403** |
| R3 对话式投研 | ❌ 同 R1 的发送问题 | 消息压根没发出去 |
| R5 忙时排队提示 | ❌ 同上 | 第一条没发出去，自然没有第二条的排队提示 |
| R6 审批档选择器标注 | ❌ 落错地方 | 前端的 agent 选择器**本来就是隐藏的**（`InputBox.tsx:503`） |

三处修完（`sendStrict()` 强校验 + `HUNTER_ADMIN_EMAILS` + 标注挂到 model picker）、
再修两处时序（等「发送」按钮 enabled、等模型名异步加载完）之后，第三遍 **7/7**：

| 用例 | 结果 | 实测 |
|---|---|---|
| R0 登录 | ✅ | 管理员登录成功（7.7 s） |
| R1 持仓研判 | ✅ | `/portfolio` 渲染出持仓结构；对话里 **2 个工具卡片、配额差值 80 573** |
| R2 策略中心与回测 | ✅（有保留） | 页面可用（403 已修）、四个指标卡在、**空状态如实显示 `--%`**（股票池 0 只 · 还没有回测数据）；点「立即运行」后界面无明显变化。**⚠️ 出数需要：股票池非空 + `KRONOS_API_KEY`（U-7 未定）+ 至少两天的预测重叠 —— 本轮没有端到端验到出数** |
| R3 对话式投研 | ✅ | **6 个工具卡片、英文散句 0 段、买卖指令 0 处**、配额差值 110 125 |
| R4 审计留痕（真实链路） | ✅ | 最近 60 条审计：**53 条带耗时、60 条带用户标识**；工具含 `mcp__watchlist__stock_news`、`mcp__akshare__akshare_call`、`fetch_output` 等 |
| R5 忙时排队提示（决策 10） | ✅ | 第二条消息立刻收到「另一个对话正在生成 —— 本发行版同一时刻只跑一个回合（daemon 的 /live 是全局单例）。你这条已排队，前一轮结束后会自动开始，不用重发。」截图 `06-m4-queued-notice.png` |
| R6 审批档/模型选择器标注（决策 9） | ✅ | model 选择器显示 **「hunter-chat（全局生效）」**；agent 选择器 count=0（前端本来就隐藏） |

**R2 这一项还抓到过一次我自己写的"假过"**：判据原本是「`方向命中率` 后面 40 个字符内出现百分数」，
命中的却是卡片**自己的说明文字**「涨跌看对的比例 · >55% 才算有效」里的 `55%` ——
用例报 ✅、截图里却是 `--%`。改成只认紧跟标题的那个值之后才是真的。
（本项目第二次栽在"断言认了页面上本来就有的字"，第一次是 M3 那三处。）



## 6. 持仓研判

* **页面**：`/portfolio` 渲染出持仓结构（命中「持仓 / 市值 / 盈亏 / 成本」四个字段标签）。
* **对话**：新建会话问「看一下我的持仓，按市值排序列出来，并指出集中度风险。
  数据要来自工具，拿不到就说拿不到」→ **1 个工具卡片、网关配额差值 80 152**（真实回合）。
* 断言只认系统自己产出的东西（工具卡片 / 真实 token 消耗）——
  不认回答里有没有"持仓"两个字，那两个字用户自己那句里就有。
* MCP 侧单独验过：`portfolio_stress` 返回
  `{"type":"portfolio_stress","empty":true,"hint":"需要先在持仓页录入…"}` ——
  **空持仓如实报空，不编数字**。

## 7. 会话恢复

M3 第 8 步：发完一轮之后刷新页面，历史消息与工具卡片都从 daemon 的会话详情
（`GET /projects/{hash}/sessions/{id}`）恢复出来，耗时 2 805 ms。✅

断线场景（第 10 步）里**数据层也是过的** —— 断网 6 秒再恢复，后端这一轮完整
（历史里有 `stock_quickview`）；失败的是界面层，原因见 §5.1。

## 8. 一键部署 · 从零安装

见 `docs/开发文档/M4-成果与测试报告.md` §4.1。要点：

* 三遍从零安装，**前两遍各炸出一个真缺陷**（`.gitignore` 吞掉适配层、
  `HCA_LLM_API_KEY_FILE` 容器读不到），第三遍 **407 秒 / rc=0 / 自检全过**。
* 自检的每个数字都是脚本实时查的：hook 注册 8 条、MCP 9/9（28 工具）、技能 6 个、
  网页 HTTP 200、`网页→daemon model=oneapi/hunter-chat`、对外端口只有 3300。
* key 校验是**真打接口**：`/quota` 返回「今日已用 18 044 434 / 上限 1 000 000 000 /
  剩余 981 955 566」。
* 耗时的诚实说明：镜像层部分命中本机缓存，**不是干净机器上的时间**；
  同一批里没命中缓存的 `npm run build` 实测 240.6 秒。干净机器上的总时间没实测，不编。

## 9. 端口与暴露（安全底线）

装完在测试机上实测（`ss -lntp`）：

```
LISTEN 0.0.0.0:3200    ← web，唯一对外
LISTEN 127.0.0.1:8200  ← api，只绑回环
（daemon / postgres / redis / llm-shim 在 compose 内网，宿主上没有监听）
```

`docker compose -p hca ps` 的端口列也印证：
`api 127.0.0.1:8200->8000`、`web 0.0.0.0:3200->3000`，
`daemon 13456/tcp`、`postgres 5432/tcp`、`redis 6379/tcp`、`llm-shim 3999/tcp` 全是 expose 不是 publish。

`api /api/auth/status` 实测 `single_user=False registration_mode=invite` —— 单用户免登录确实是关的。

**顺带清掉一个自己留下的东西**：宿主上还跑着一个 M0 时期的探针 daemon
（`/home/support/hca/bin/atomcode daemon --port 13456`，从 9-21 16:28 起一直在，
只绑 127.0.0.1）。它不属于本发行版的部署，但会让 `ss -lntp` 看起来像"daemon 暴露了端口"，
已收掉。

## 10. 三种模型通道

| 通道 | 结论 | 依据 |
|---|---|---|
| **OneAPI · Gemini 3.8**（默认） | ✅ | 全程在用。从零安装的 key 校验打的是真 `/quota`；6 个技能、所有浏览器用例、`web→daemon model=…` 都是它 |
| **自带官方 Key**（OpenAI 兼容） | ✅ **协议路径真跑过** | 见 §10.1 |
| **本地 Ollama** | ❌ **未测** | 测试机磁盘只剩 12～14 G（91%）且与另一条自驱链路共用，`ollama/ollama` 镜像加一个能做工具调用的模型要 2 G 以上，**填满磁盘会连带弄坏别人的链路**。已中止镜像拉取。`install.sh` 的 ollama 分支做过脚本审阅（`/models` 校验 + 模型名核对 + Linux/macOS 的地址默认值差异），但 **Ollama 本体与"小模型能不能做工具调用"都没有实测** |

### 10.1 official 通道实测

在临时栈（`hcafresh`，端口 3300）上把 `.env` 切成 official 通道：
`HCA_LLM_PROVIDER_NAME=official`、`HCA_LLM_BASE_URL` 改成**直连网关**（不经 llm-shim），
然后 `docker compose up -d daemon`，daemon 起来后：

```
web→daemon: {"model":"official/hunter-chat","provider":"official","workdir":"/workspace"}
```

再走完整的用户路径发一条真实消息（登录 → 建会话 → 发消息 → 读历史）：

| 项 | 实测 |
|---|---|
| 结果 | 成功，`stop_reason=stopped`，墙钟 **38.6 s** |
| 工具 | `watchlist_stock_quickview`（真实调用） |
| 正文 | 114 字：「由于当前尚未配置 Hunter key，`stock_quickview` 工具无法获取贵州茅台（600519）的真实行情数据，请点击页面左下角「解锁全部工具」免费申请 Hunter key 并填入…」 |

两件事一起验到了：① **official 通道（直连、不经 shim）能正常跑完一个带工具调用的回合**；
② 临时栈没配 `HUNTER_API_KEY`，行情工具**如实说没有 key 并给出申请入口，不编数据**。

**保留意见（重要）**：这把 key 本身仍然是 HunterCode 网关的 key，
只是走了"官方 key 直连 OpenAI 兼容端点"这条代码路径。
**没有用第三方厂商（OpenAI / DeepSeek / 通义）的 key 测过** —— 手上没有那种 key，
也不该为测试去买。所以这一条的准确说法是：**协议路径验过，某个具体厂商的兼容性没验过。**

## 11. 升级与回滚

在临时栈（`hcafresh`，与主部署完全隔离：独立目录 / 项目名 / 镜像 tag / 端口）上各验一次。

**第一次尝试就抓到 `install.sh` 的一个真缺陷**：`--upgrade --ref feat/m4` 跑完说
「✓ 已经是 feat/m4（666bc4c），无需升级」—— 因为 `git rev-parse feat/m4` 解的是
**本地同名分支**（clone 出来就停在那个提交上），不是刚 fetch 回来的 `origin/feat/m4`。
**升级"成功"但什么都没换，比报错更坏。** 修成：分支名优先解 `refs/remotes/origin/<ref>`，
并用解析后的提交做 checkout；tag 与裸 commit 不受影响。

修完之后重验，两项都过：

| 动作 | 结果 | 耗时 | 自检 |
|---|---|---|---|
| `install.sh --upgrade --ref feat/m4` | ✅ rc=0，`666bc4c → c9d841b` | **451 s**（含 web 镜像重建） | hook 8 条 / MCP 9/9（28 工具）/ 技能 6 个 / 网页 HTTP 200（0.012 s）/ 对外端口只有 3300 |
| `install.sh --rollback` | ✅ rc=0，回到 `666bc4c` | **61 s**（镜像层命中缓存） | 同上全过 |

升级前脚本自动存了回滚点（`.hca-rollback.json` + `.hca-rollback.env`，记的是
`666bc4c…` 与当时的 `.env`），回滚时原样放回。**数据卷全程没动**
（workspace / atomcode-home / pgdata / redisdata 都在，会话与持仓还在）。

⚠️ 两条要写进发布说明的事：
1. **回滚不回退数据库结构**（本项目的迁移都是加表加列，旧代码能跑）。
2. 上面那个分支解析的修复**只对下一次升级生效** —— 已经装在用户机器上的旧 `install.sh`
   仍有这个问题，所以从更早的版本升上来时，请用**新下载的** `install.sh` 跑 `--upgrade`。

## 12. 收尾状态（写报告这一刻的实测）

* **主部署跑的是 v0.1.0**（main `0011434`）：`bash deploy/up.sh` 自检全过 ——
  `/health` version 5.1.0、binary_hash `40d86fa3…`、`/skills` 6 个、
  `/mcp/status` 9/9 connected（28 工具，trusted=true，blocked=[]）、
  api `single_user=False registration_mode=invite`、
  web 首页 HTTP 200（0.006 s）、`web→daemon model=hunter/hunter-chat`。
* **公网可达**：`http://34.133.8.3:3200` → HTTP 200（0.0075 s）。
* **临时栈已拆干净**：`hcafresh` 的容器、网络、四个数据卷、安装目录、三个 `m4test` 镜像全部删除，
  `docker ps | grep hcafresh` 与 `docker volume ls | grep hcafresh` 都是 0。主部署没受影响。
* 测试机磁盘：**18 G 可用（85%）**，与另一条自驱链路共用 —— 这是 Ollama 通道没能测的直接原因。
