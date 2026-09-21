---
name: trap_detector
description: 杀猪盘检测器。扫描朋友推荐、内幕消息、基本面脱节等 8 个信号给出风险评级。
user-invocable: true
allowed-tools: mcp__uzi__stock_deep_analysis mcp__watchlist__stock_news
version: 3.9.4
author: FloatFu-true
license: MIT
metadata:
  tags: [finance, a-share, trap-detection, risk]
hunter:
  display_name: UZI 杀猪盘检测
  icon: ReportProblem
  category: 尽调风控
  prompt_tpl: 帮我测一下 {股票} 是不是杀猪盘
  needs_tools:
    - uzi_stock_deep_analysis
    - watchlist_stock_news
---

# Trap Detector · 杀猪盘检测器

## 8 信号扫描清单
1. 低质量账号推荐
2. 模板化话术
3. VIP群引流
4. 基本面与热度脱节
5. K线异常拉升
6. 老师人设推广
7. 跨平台联动
8. 虚假消息

## 风险评级
- 🟢 安全 | 🟡 注意 | 🟠 警惕 | 🔴 高度可疑

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
