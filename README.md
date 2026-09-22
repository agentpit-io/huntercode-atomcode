# HunterCode · AtomCode 发行版

> **HunterCode 投研终端的 AtomCode 国内发行版** —— 把 HunterCode 的投研技能、行情 MCP 与
> Web 终端，搭在国产开源对话内核 [AtomCode](https://atomgit.com/atomgit_atomcode/atomcode) 上，
> 面向私募机构私有化部署与个人桌面：**仓位数据不出硬盘**。

[![版本](https://img.shields.io/badge/版本-v0.1.1%20预发布-orange)](https://github.com/agentpit-io/huntercode-atomcode/releases)
[![底座](https://img.shields.io/badge/AtomCode-v5.1.0-blue)](pins.lock)
[![许可证](https://img.shields.io/badge/License-Apache--2.0-green)](LICENSE)

> **v0.1.1 是预发布版**：功能已经能完整走通（回归清单见
> [`docs/regression-report.md`](docs/regression-report.md)，连续 4 小时浸泡见
> [`docs/stability-report.md`](docs/stability-report.md)），但已知缺陷都公开摆在
> [`docs/开发文档/待办池.md`](docs/开发文档/待办池.md) 里，**上生产前请先读一遍那份清单**。
> 这一版修了什么：[`docs/发布说明-v0.1.1.md`](docs/发布说明-v0.1.1.md)。
> 和 opencode 版怎么选：[`docs/对比-opencode版.md`](docs/对比-opencode版.md)。
> 主仓在 [GitCode](https://gitcode.com/agentpit-io/huntercode-atomcode)，
> [GitHub](https://github.com/agentpit-io/huntercode-atomcode) 为镜像。

---

## 这是什么

[HunterCode 社区版](https://github.com/agentpit-io/hunter-community)（猎鹿人）是一个开源的 AI 投研终端：
自选股、龙虎榜、深度分析、组合建议、Kronos 预测……底座用的是 opencode。

本仓库是**同一套投研能力换一个对话内核**的发行版：底座换成 AtomCode（国产、开源、MIT、
可完全离线跑），面向国内私有化场景。上层的 Web 终端、SKILL、MCP、数据库沿用 HunterCode 社区版。

**为什么值得做**：

- **数据不出硬盘** —— 私募最在意的一条。对话内核、MCP、数据库全在自己机器上，只有大模型调用出网（也可换本地 Ollama）。
- **国产底座** —— AtomCode 由 AtomGit 团队维护，代码与发布都在国内，不依赖境外服务可用性。
- **一套技能两个底座** —— SKILL 与 MCP 都是标准格式，社区版（opencode）与本发行版（AtomCode）共用。

## 快速开始

一条命令装完（Linux + Docker，装完直接能用）：

```bash
curl -fsSLO https://raw.githubusercontent.com/agentpit-io/huntercode-atomcode/main/deploy/install.sh
bash install.sh
```

交互里只有四个问题：装到哪 → **模型通道三选一**（默认 OneAPI · Gemini 3.8）→
网关地址 → API key。key 会**真的去打一次接口**校验（不是本地格式检查），
装完自检不过脚本直接失败退出 —— 不会给你一个"起来了但用不了"的栈。

无人值守：

```bash
bash install.sh --non-interactive --yes \
  --dir /opt/hca --channel oneapi --api-key-file /root/hca-key \
  --web-port 3200 --admin-email it@yourfund.com
```

装完会打印访问地址与管理员口令文件位置。升级 / 回滚：

```bash
bash deploy/install.sh --upgrade        # 数据卷不动，升级前自动存回滚点
bash deploy/install.sh --rollback       # 回到升级前
```

已经 clone 了仓库的话，`bash deploy/install.sh` 会直接用当前这份代码；
只想起一套开发环境就 `cp deploy/env.example deploy/.env` 之后 `bash deploy/up.sh`。

**装完应该看到**（数字都是真查出来的）：

```
  hook 注册      : 8 条
  MCP            : 9/9 connected，工具 28 个
  技能           : 6 个
  网页           : HTTP 200
  网页→daemon    : model=oneapi/hunter-chat
  对外端口       : 0.0.0.0:3200
```

| 想做什么 | 看哪份文档 |
|---|---|
| 部署、备份、升级、排障 | [`docs/部署与运维.md`](docs/部署与运维.md)（面向私募 IT） |
| 加技能 / 加数据源 / 加约束 / 跟上游升级 | [`docs/开发者指南.md`](docs/开发者指南.md) |
| 15 分钟演示 | [`docs/demo-script.md`](docs/demo-script.md) |
| 五个 hook 怎么设计的、为什么 | [`docs/hooks-design.md`](docs/hooks-design.md) |
| 这版都测了什么、结果如何 | [`docs/regression-report.md`](docs/regression-report.md) |

## 现在能用什么

* **对话式投研** —— 网页终端（`http://<你的机器>:3200`），问行情、问财务、写论点、做复核；
  每次取数都是一次工具调用，卡片上能看到工具名、参数与返回。
* **9 个数据源 MCP / 28 个工具** —— akshare（全市场数据）、自选、组合、筛选、
  龙虎榜与深度分析、Kronos（K 线预测）、一手信号等。
* **6 个投研技能** —— 深度分析、投资人辩论、龙虎榜、风险画像、杀猪盘检测、UZI 全维扫描。
* **五个 hook**（发行版自己的约束层，不改内核）——
  写类工具只放行 `reports/` 与 `theses/`；`bash`/`bash_start` 只放行只读与纯计算；
  取数一律走 MCP（写爬虫、直接 `import akshare` 都拦）；每轮注入真实日期与交易时段；
  语言硬约束；每次工具调用落审计（工具名 / 参数摘要 / 耗时 / 结果大小 / 会话 / 用户）。
* **一键安装 / 升级 / 回滚**，模型通道三选一，国内镜像与 npm 源可配。

**还没有的**（都写在 [`待办池`](docs/开发文档/待办池.md) 里，不藏）：多工作区 / 多用户隔离、
语言守卫的出口强校验、MCP 返回超 16 KB 被内核截断的根治、Ollama 通道的真机验证。

## 架构

```
┌───────────────────────────────────────────────────────────────────────┐
│  浏览器  http://<你的机器>:3200                                        │
└────────────────────────────┬──────────────────────────────────────────┘
                             │
┌────────────────────────────▼──────────────────────────────────────────┐
│  apps/web · Next.js 终端（来自 HunterCode 社区版，尽量零改动）          │
│    app/api/opencode/[...path]/route.ts                                │
│      └─ AGENT_BACKEND=opencode | atomcode  ← 适配层落在这里             │
│         把 AtomCode 的 SSE 事件归一成前端现有事件                       │
└──────────┬───────────────────────────────────┬────────────────────────┘
           │                                   │
┌──────────▼───────────────┐   ┌───────────────▼────────────────────────┐
│ apps/api · FastAPI        │   │ atomcode daemon（官方二进制 v5.1.0）    │
│  用户/自选/组合/回测/额度  │   │   GET  /live        SSE 事件流          │
│  postgres + redis         │   │   POST /live/message                    │
└──────────▲───────────────┘   │   POST /chat        （无 MCP，见 M0 报告）│
           │                   │   .atomcode.md  .hooks.json  .mcp.json   │
           │ 内部接口           │   .atomcode/skills/                      │
           │                   └───────────────┬────────────────────────┘
           │                                   │ stdio MCP
           │       ┌───────────────────────────┴────────────────────────┐
           └───────┤ tools/opencode-mcp/  uzi · watchlist · portfolio     │
                   │                      screener · hunter_cap · hunter_user│
                   │ tools/akshare-mcp  kronos-mcp  truesource-mcp        │
                   └──────────────────────────────────────────────────────┘
```

四个外部面，**不改 AtomCode 内核**：

| 面 | 我们放什么 |
|---|---|
| daemon HTTP/SSE API | Web 转发层对接 `/live`（MCP 只在这条通道上） |
| MCP（stdio） | 9 个 MCP，行情、自选、组合、筛选、AKShare、Kronos |
| Skill | 6 个投研 SKILL，目录式 `SKILL.md` |
| Hook（`.hooks.json`，8 事件，shell） | **五组实现、8 条注册**：`guard`（PreToolUse 硬约束 + 身份注入 + 给审计留起始记录）、`context`（注入真实日期与交易时段）、`lang`（语言硬约束）、`audit`（PostToolUse / PostToolUseFailure 落审计）、`budget`（Stop 记账 + 超限拦截，**默认关**）。设计与上游契约的坑见 [`docs/hooks-design.md`](docs/hooks-design.md) |

## 与上下游的关系

| 项目 | 关系 |
|---|---|
| [AtomCode](https://atomgit.com/atomgit_atomcode/atomcode)（MIT） | **对话内核底座**。以官方二进制形式依赖，版本与 sha256 锁在 [`pins.lock`](pins.lock)，源码不包含在本仓库 |
| [HunterCode 社区版](https://github.com/agentpit-io/hunter-community)（Apache-2.0） | **Web / API / SKILL / MCP 的来源**。2026-09-22 从 `1875679` 一次性导入，之后独立演进，**不做持续同步**（见 [`docs/来源说明.md`](docs/来源说明.md)） |
| hunter-community.agentpit.io | 社区版在线演示站，跑的是 opencode 版，与本发行版并行 |

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/开发文档/总进度表.md`](docs/开发文档/总进度表.md) | M0–M5 里程碑与状态 |
| [`docs/开发文档/M0-预研结论.md`](docs/开发文档/M0-预研结论.md) | 底座实测结论：权限档、MCP、hook、技能、多会话 |
| [`docs/开发文档/M1-成果与测试报告.md`](docs/开发文档/M1-成果与测试报告.md) | 底座集成：daemon 镜像、9 个 MCP、技能转换、工作区、compose，以及 6 个技能的真实模型验收 |
| [`docs/开发文档/M2-成果与测试报告.md`](docs/开发文档/M2-成果与测试报告.md) | 领域改造：投研人设、guard / context hook、编码向开关，以及 A/B 评测与 fork 决策 |
| [`docs/coding-bias-analysis.md`](docs/coding-bias-analysis.md) | AtomCode 5.1.0 的编码倾向逐条分析：来源、触发条件、能不能关、怎么关 |
| [`docs/开发文档/M3-成果与测试报告.md`](docs/开发文档/M3-成果与测试报告.md) | Web 接入：`AGENT_BACKEND=atomcode` 转发层、compose 纳入 web/api/pg/redis、真浏览器验收 |
| [`docs/开发文档/M4-成果与测试报告.md`](docs/开发文档/M4-成果与测试报告.md) | hook 全量、一键部署、回归与发布 |
| [`docs/部署与运维.md`](docs/部署与运维.md) | 面向私募 IT：硬件要求、备份、升级回滚、安全、国内网络、离网部署、排障 |
| [`docs/开发者指南.md`](docs/开发者指南.md) | 架构、四个外部面、怎么加技能/MCP/hook、怎么跟上游升级 |
| [`docs/hooks-design.md`](docs/hooks-design.md) | 五个 hook 的设计、opencode 插件映射表、上游契约的 12 个坑 |
| [`docs/regression-report.md`](docs/regression-report.md) | 回归清单：每项通过/失败 + 实测数字 + 证据路径 |
| [`docs/demo-script.md`](docs/demo-script.md) | 15 分钟演示脚本 |
| [`docs/开发文档/待办池.md`](docs/开发文档/待办池.md) | 发现但当轮不做的问题（P0/P1/P2） |
| [`docs/daemon-api.md`](docs/daemon-api.md) | AtomCode daemon API 实测手册（65 个端点、22 种 SSE 事件、真实样例） |
| [`docs/来源说明.md`](docs/来源说明.md) | 导入清单、MCP 清单与缺口 |
| [`docs/questions-for-atomgit.md`](docs/questions-for-atomgit.md) | 上游文档与实现不符之处 |
| [`docs/design/`](docs/design/) | 开发计划 v0.2 与技术方案 v0.1 |
| [`docs/evidence/`](docs/evidence/) | 实测原始输出（SSE 抓包、hook 输入、MCP 返回） |

## 许可证

本仓库采用 **Apache License 2.0**（见 [`LICENSE`](LICENSE)）。
AtomCode 为 MIT，HunterCode 社区版为 Apache-2.0，两者版权声明保留在 [`NOTICE`](NOTICE)。

HunterCode、Hunter、AgentPit、猎鹿人 是 AgentPit 团队的商标。对外分发 / 托管服务 /
商业发布请去掉这些名称与 Logo，可注明「based on HunterCode」。

---

## English

**HunterCode · AtomCode Distribution** — the HunterCode investment-research terminal,
rebuilt on [AtomCode](https://atomgit.com/atomgit_atomcode/atomcode), an open-source (MIT)
coding-agent kernel developed and released inside mainland China.

**Status: v0.1.1 pre-release.** The stack works end to end (see
[`docs/regression-report.md`](docs/regression-report.md) and a 4-hour soak test in
[`docs/stability-report.md`](docs/stability-report.md)); known defects are listed
openly in [`docs/开发文档/待办池.md`](docs/开发文档/待办池.md) — read that before
putting it in front of real money.

**Why**: the target users are China-based hedge funds and individual investors who need the
whole stack — agent kernel, market-data MCP servers, database — to run on their own hardware,
so position data never leaves the disk. Only the LLM call goes out, and that can be pointed at
a local Ollama instead.

**How it is put together**: the web terminal (Next.js), API (FastAPI), 6 investment-research
skills, and 9 stdio MCP servers come from
[HunterCode Community Edition](https://github.com/agentpit-io/hunter-community) (Apache-2.0),
imported once at commit `1875679` and evolving independently from there — there is no ongoing
sync. AtomCode is consumed as an **official prebuilt binary** (v5.1.0, sha256 pinned in
[`pins.lock`](pins.lock)); its source is not vendored here. We deliberately do not patch the
AtomCode kernel — the distribution rides on its four public surfaces: the daemon HTTP/SSE API,
MCP, hooks, and skills.

Primary repository is on [GitCode](https://gitcode.com/agentpit-io/huntercode-atomcode);
this GitHub repository is a mirror. Licensed under Apache-2.0; see [`NOTICE`](NOTICE) for
third-party copyright (AtomCode is MIT).
