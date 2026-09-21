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
