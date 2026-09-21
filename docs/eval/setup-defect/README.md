# 评测设置缺陷 · 基线工作区缺论点/持仓文件（2026-09-22 07:37 上海时间发现并修复）

## 缺陷

`deploy/eval/seed_eval_account.py` 的设计前提写得很清楚：

> 两边必须看到**同一份持仓、同一份论点**，否则比的是数据不是 agent。
> opencode 侧从 api 取（`portfolio_*` / `watchlist_*` 这几个 MCP 是薄代理），
> HCA 侧走同一个 api，另外还在工作区里铺一份等价的 `holdings/` 与 `theses/` 文件。

**这个前提是错的。** 论点是用 `PUT /api/watchlist/{code}/thesis` 播进 api 的，
但**两边的 MCP 里都没有任何一个工具会把论点读回来**——逐个核对过：

| MCP | 工具 | 含论点？ |
|---|---|---|
| `watchlist` | `stock_quickview` / `stock_news` / `watchlist_add` / `watchlist_add_batch` / `watchlist_digest` / `watchlist_rank` | 否（源码里 `grep thesis` 零命中） |
| `portfolio` | `portfolio_rebalance` / `portfolio_stress` / `update_risk_profile` | 否（两边同一份文件，`grep thesis` 零命中） |
| `uzi` | `stock_deep_analysis` / `invoke` | 否（只有提示词里出现「投资论点」四个字，不是取数能力） |
| `hunter_cap` / `hunter_user` / `screener` | — | 否 |

所以论点**只能从工作区文件读到**。而 HCA 侧的工作区铺了
`theses/*.md`（`tools/eval/seed_workspace.py`），基线工作区
`/opt/opencode-workspace` **一个都没铺** —— 第 2 类题（持仓论点复核）
对基线是结构性不可能完成的。

## 证据：修复前基线侧那一次的原文

`q2-thesis-review-opencode-r1.缺论点文件.json` 的 `text` 全文：

> 请提供您当初买入**中国神华（601088）**时记录的**买入理由**与**证伪条件**。
> 获取后我将结合当前最新行情与财务数据，逐条为您评估「成立 / 削弱 / 已证伪 /
> 数据不足」及触发状态。

它调了 `portfolio_portfolio_rebalance` 与 `watchlist_stock_quickview` 各一次，
都成功，但两个工具都不返回论点，于是只能回头问用户。
**这不是模型的问题，是评测设置的问题。**

## 修复

用同一个脚本、同一份 `seed`，把等价文件铺进基线工作区：

```
python3 tools/eval/seed_workspace.py --account ~/hca/secrets/eval-account.json \
    --container hca-baseline-opencode-1 --workspace /opt/opencode-workspace
```

铺完两边逐文件 sha256 核对，**四个文件全部逐字节相同**：

| 文件 | sha256 |
|---|---|
| `holdings/positions.md` | `56948936d728d87c1f4977fa2d2309c6d3b0fd4f09544afb3040d9deae86b0ce` |
| `theses/300750.md` | `4b48cc21b2ebb11b5b32d785607e770fcb033c7d2e8383ae54c514a57cab5aca` |
| `theses/600519.md` | `145b005d42936481885a25b2f6ebee1e1dcbd39c8a3d1ac3e52c969897a1246f` |
| `theses/601088.md` | `fdaf18c3118fcd4fadc8470904c6cac69d2717eb3c78a8237aab5b24abe28ebd` |

（HCA 侧另有一个 `theses/README.md`，属工作区模板自带的说明文件，
不含任何账本数据，未铺到基线侧。）

## 受影响的运行与处置

修复时刻 = 23:37:35 UTC（07:37:35 上海）。此前已跑完的 r1 共 6 次：

| 运行 | 这道题要不要论点/持仓文件 | 处置 |
|---|---|---|
| `q1-fundamental-atomcode-r1` / `-opencode-r1` | 不要（要行情 + 财务） | 保留计分。HCA 侧顺手读了 `theses/600519.md` 引了一句建仓价，不影响 q1 的任何一个评分点 |
| `q2-thesis-review-atomcode-r1` | — | 保留计分 |
| **`q2-thesis-review-opencode-r1`** | **要** | **作废重跑**，原始记录留存在本目录 |
| `q3-factor-screen-atomcode-r1` / `-opencode-r1` | 不要（全市场筛选） | 保留计分 |

q4 / q5 的 r1 都在修复之后才开跑，三次重复一致。

---

## 补记（同日 07:45 上海时间）：铺了文件之后，基线**仍然**答不出来 —— 但原因变了

修复之后跑的 `q2-thesis-review-opencode-r2`（工作区里已经有逐字节相同的
`theses/601088.md`）正文全文：

> 请提供您当初写下的**具体买入理由**以及设定的**证伪条件**文本（或关键要点）。
> 收到后我将结合当前最新行情（现价约 46.80 元）与基本面数据，逐条为您复核评级与触发情况。

它还是回头问用户，而且这次**一次文件读取都没调**（只调了
`portfolio_portfolio_rebalance` 与 `watchlist_stock_quickview`）。

查基线的主提示 `/opt/hunter-boot/HUNTER-AGENT.md`，第 18 行写着：

> **你不是在开发 opencode。** 工作区里那些 TypeScript 源码、`AGENTS.md`、
> `CONTRIBUTING.md` 都是引擎自身的代码，与用户的提问无关，**不要读、不要提**。

**这是一条对工作区的无差别禁读令。** 它在社区版里是必要的 ——
社区版的工作区 `/opt/opencode-workspace` **就是 opencode 自己的源码树**
（主提示开头那段注释把来龙去脉写得很清楚：在这条指令出现之前，模型的项目
上下文一直是「你在给 opencode 这个代码库做贡献」，表现是用英文、用开发者
口吻回答）。代价是：社区版**没有「投研工作区」这个概念**，也就没有地方
放持仓与论点这类用户自己的长期资产。

所以修复前后这道题的性质变了，报告里要按后一种说法写：

| | 修复前 | 修复后 |
|---|---|---|
| 基线拿不到论点的原因 | **评测没给** —— 工作区里压根没这个文件 | **发行版设计如此** —— 文件在，但主提示叫它别读工作区 |
| 这算谁的问题 | 评测设置的缺陷，数据作废 | 两个发行版的真实差异，计分 |

HCA 侧的对照：`.atomcode.md` 里点名 `holdings/` 与 `theses/` 是用户资产并要求
先读，工作区是专为投研铺的（`distro/workspace-template/`），不是引擎源码树。
`q2-thesis-review-atomcode-r1` 的第 1、2 次调用正是
`read_file holdings/positions.md` 与 `read_file theses/601088.md`。

**结论**：这一题 HCA 赢在**发行版形态**（专用工作区 + 指向它的人设），
不是赢在引擎。报告的结论一节必须把这一点和「引擎差异」分开说 ——
它和 §4.3 的「两边工具清单本来就不一样」是同一类的合成项。
