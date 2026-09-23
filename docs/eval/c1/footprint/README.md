# 两边引擎的常驻内存与镜像大小（I1 · 补 `docs/对比-opencode版.md` §6 的「未测」）

三份采样都在**评测机 hca-bench-01** 上取，每一份都是**一条 `docker stats --no-stream`
同时问两个容器**（不是先量一个再量另一个），生成命令 `tools/eval/engine_footprint.sh`。

| 文件 | 采样时的条件 | HCA daemon | opencode |
|---|---|---|---|
| `footprint-during-eval.json` | B 线评测跑到一半（HCA 挂 **10 个** MCP），load 1.67 | **1.1 GiB** | 886.7 MiB |
| `footprint-idle-after-eval.json` | A 线评测刚结束、空转（HCA 只挂 **6 个** MCP），load 0.04 | 553.4 MiB | 846.4 MiB |
| `footprint-idle-10mcp.json` | 恢复成 10 个 MCP、**刚重启 1 分钟**、空转，load 0.95 | 535 MiB | 859 MiB |

**这三行不能直接横着比，条件不一样，必须连着条件读**：

- HCA 那一列的差异主要来自**挂了几个 MCP** 与**跑没跑东西**：10 个 MCP 的 daemon 刚起来是
  535 MiB，压上评测负载会涨到 1.1 GiB。
- opencode 那一列的三个数看起来稳定在 850~890 MiB，但那个容器**已经连续跑了 9 小时**、
  中间还跑完了一轮 1 小时浸泡；它自己的起点是 **538.9 MiB**（浸泡开始时那一刻，
  见 `../soak-opencode/summary.json`）。
- 想看「长期常驻」，要看各自的浸泡：HCA **4 小时 0.95 GiB**（`docs/stability-report.md`，M5）、
  社区版 **1 小时 565 MB → 709 MB**（`../soak-opencode/summary.json`）。

## 镜像大小（`docker images` 的 SIZE，即**解压后**占盘）

| | HCA daemon | opencode |
|---|---|---|
| 镜像 | `hca-daemon:i2-fork` **1.11 GB** | `ghcr.io/agentpit-io/hunter-community-opencode:1.2.0` **619 MB** |

两边不完全可比，差在**里面装了什么**：HCA 的 daemon 镜像打包了 AtomCode 二进制
（自编 fork）+ 9 个 MCP 的两套 Python venv（含 akshare 整套依赖）+ 6 个技能 + 工作区模板；
社区版的 opencode 镜像是 Node 运行时 + opencode + 它那套 MCP，
数据类 MCP（akshare / kronos / truesource）它本来就没有。
这两个数放在一起看的是「一台机器要吃多少盘」，不是「谁的代码写得省」。

## 整套栈要下载多少（registry 上的**压缩**层大小，`docker manifest inspect` 实测）

| 镜像 | 压缩后 |
|---|---|
| hunter-community-web:1.2.0 | 331 344 498 B（316 MiB）|
| hunter-community-api:1.2.0 | 214 680 985 B（205 MiB）|
| hunter-community-opencode:1.2.0 | 152 979 569 B（146 MiB）|
| hunter-community-llm-shim:1.2.0 | 18 054 459 B（17 MiB）|
| **合计** | **717 059 511 B ≈ 0.67 GiB** |

冷机实测下载速度：在评测机上真拉了一次 `hunter-community-web:1.2.0`（那台本来没有这个镜像），
**18.6 秒 / 331 MB = 17.0 MiB/s**，拉完随即删掉。按这个速度整套 0.67 GiB 约 **40 秒**
—— 这一项是由两个实测值推算的，标明出处，不是量出来的单一数字。
