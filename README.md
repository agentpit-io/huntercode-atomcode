# HunterCode · AtomCode 发行版

> **HunterCode 投研终端的 AtomCode 国内发行版** —— 把 HunterCode 的投研技能、行情 MCP 与
> Web 终端，搭在国产开源对话内核 [AtomCode](https://atomgit.com/atomgit_atomcode/atomcode) 上，
> 面向私募机构私有化部署与个人桌面：**仓位数据不出硬盘**。

[![状态](https://img.shields.io/badge/状态-开发中%20·%20M0-orange)](docs/开发文档/总进度表.md)
[![底座](https://img.shields.io/badge/AtomCode-v5.1.0-blue)](pins.lock)
[![许可证](https://img.shields.io/badge/License-Apache--2.0-green)](LICENSE)

> ⚠️ **开发中，尚不可用于生产。** 当前进度见 [`docs/开发文档/总进度表.md`](docs/开发文档/总进度表.md)。
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

## 快速开始（M1 · 当前只起 daemon）

```bash
git clone git@gitcode.com:agentpit-io/huntercode-atomcode.git
cd huntercode-atomcode

cp deploy/env.example deploy/.env      # 填 HCA_LLM_API_KEY（或把 key 文件放进 deploy/secrets/）
bash deploy/up.sh                      # 构建 + 启动 + 等健康 + 自检；可重复执行
bash deploy/up.sh --status             # 看 /health、/skills、/mcp/status
```

跑完会看到：

```
  GET /health   : {"status":"ok","version":"5.1.0",…,"binary_hash":"40d86fa3…"}
  GET /skills   : 6 个： skills:deep_analysis skills:investor_panel … skills:uzi
  GET /mcp/status: 9/9 connected，工具 28 个，trusted=True，blocked=[]
```

**现在能用什么**：daemon + 9 个 MCP + 6 个投研技能 + 研究工作区 +
**投研人设与三条 hook**（M2）——
`.atomcode.md` 逐段覆盖 AtomCode 的编码规则、`guard.sh` 把写类工具与 bash 的写/抓网
硬拦住（只放行 `reports/` 与 `theses/`）、`context.sh` 每轮注入真实日期与交易时段。
六个编码向开关关掉后每轮省 3 936 prompt token（实测 28 904 → 24 968）。

**现在还没有**：网页界面（挪到 M3）、`apps/api` 与它背后的 6 个 hunter 系 MCP 的数据
（发行版 compose 仍只起 daemon + llm-shim，那 6 个能挂上但调不通；M2 在评测环境里
验证了接上 api 之后确实能通）、一键安装脚本（M3）。
详见 [`docs/开发文档/M2-成果与测试报告.md`](docs/开发文档/M2-成果与测试报告.md)
与 [`docs/coding-bias-analysis.md`](docs/coding-bias-analysis.md)。

按总控端口表，daemon 与 llm-shim **都不发布到宿主**，只在 compose 内网可达；
daemon 的访问 token 在共享卷 `hca_daemon-token`。

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
| Hook（`.hooks.json`，8 事件，shell） | **PreToolUse `guard.sh`**：写类工具只放行 `reports/` `theses/`、bash 只放行只读/纯计算、工作区外路径全拒，顺带给 hunter 系 MCP 注入 `_hermes_user_id`；**UserPromptSubmit `context.sh`**：注入真实日期与交易时段；**PostToolUse `audit.sh`**：落审计 |

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

**Status: under development (milestone M0).** Not production-ready.

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
