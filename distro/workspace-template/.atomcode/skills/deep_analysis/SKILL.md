---
name: deep_analysis
description: 22 维度融合个股深度分析。生成包含基本面、技术面、资金面及投委会结论的综合报告。
user-invocable: true
allowed-tools: mcp__uzi__stock_deep_analysis mcp__watchlist__stock_news
version: 3.9.4
author: FloatFu-true
license: MIT
metadata:
  tags: [finance, deep-research, valuation, a-share]
hunter:
  display_name: UZI 深度分析
  icon: Article
  category: 投研报告
  prompt_tpl: 帮我写一份 {股票} 的深度投研报告
  needs_tools:
    - uzi_stock_deep_analysis
    - watchlist_stock_news
---

# UZI Deep Analysis (Adapted)

## 工作流 (已适配本系统)

1. **Step 1: 数据采集**
   - 调用 `mcp__uzi__stock_deep_analysis` 获取个股 22 维度深度数据（含财务、治理、估值分位）。
   - 调用 `mcp__watchlist__stock_news` 获取近期重大事项。
2. **Step 2: 逻辑加工**
   - 应用 UZI 180 条量化规则进行初筛。
   - 分析财务造假风险与行业竞争力。
3. **Step 3: 报告组装**
   - 参照 `references/report-template.md` 格式，输出包含「投资论点、风险点、估值区间」的最终报告。

<!-- HCA:BEGIN 由 tools/build_skills.py 生成，勿手改 -->

## 参数

本技能的入参是**股票**（代码或名称都可以）。用户这次给的是：

$ARGUMENTS

参数为空时，先问清楚要看哪只标的再开工，**不要随便挑一只演示**。

## 工具名对照

上面正文提到的工具，在 AtomCode 里的真名是：

| 正文里的写法 | 实际要调的工具 |
|---|---|
| `uzi_stock_deep_analysis` | `mcp__uzi__stock_deep_analysis` |
| `watchlist_stock_news` | `mcp__watchlist__stock_news` |

**按右边那一列调。** 左边是 HunterCode opencode 版的命名，这套发行版里不存在。

<!-- HCA:END -->
