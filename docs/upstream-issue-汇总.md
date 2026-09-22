# 汇总 issue 正文（提给上游 `atomgit_atomcode/atomcode`）

> 本文件是**将要提交**到上游的那一条汇总 issue 的正文原稿，留档以便对照。
> 按总控规则已拍板决策 8 与红线 7，本项目对上游**只提两个** issue：
> B1 已在 M3 单独提（[#1583](https://gitcode.com/atomgit_atomcode/atomcode/issues/1583)），
> 其余 33 条合并成这一条。
>
> 正文里引用了本仓库 `main` 分支上的文档，所以**提交时机排在合并 main 之后**，
> 否则链接当场 404。实际提交的 issue 链接写在 `docs/开发文档/M5-成果与测试报告.md`。

---

**标题**：`基于 daemon HTTP API 做垂直发行版的一批实测问题汇总（v5.1.0，33 条，附最小复现）`

**正文**：

我们在 AtomCode **v5.1.0** 上做了一个金融投研方向的私有化发行版，只依赖四个外部面
（daemon HTTP/SSE API、MCP、hook、skill），**没有改内核**。从预研到发布的过程中
逐条实测记录了一批与文档不符、或语义不明的点，合计 33 条（另有 1 条已单独提为 #1583）。

先说明：**这些不是抱怨，也不阻塞我们**——每一条我们都已经找到了自己这一侧的活法，
写在下面「我们的处置」一栏里。提上来是因为我们踩的弯路大概率别人也会踩，
其中几条（B11、B12、E1）对「用 daemon API 接第三方前端」或「按 tag 锁版本」的人影响不小。
**不需要逐条回复**，挑贵方觉得值得处理的看就好。

完整清单（每条带最小复现命令或源码行号）：
<https://gitcode.com/agentpit-io/huntercode-atomcode/blob/main/docs/questions-for-atomgit.md>
（文中的编号 A1 / B11 / E1 与下表一一对应，可直接在页面里搜编号定位。）

版本与来源：官方二进制 `@atomgit.com/atomcode@5.1.0-linux-x64`，sha256 `40d86fa3…`；
源码对照 tag `v5.1.0`(`72b538e8c`) 与 `254d14a84`。

## 最想请教的三条

**B11 · 工具返回的 16 KB 截断阈值写死，没有配置入口**
`crates/atomcode-capabilities/src/tools/output_artifact.rs` 里
`THRESHOLD_BYTES = 16 * 1024` 与 `PREVIEW_HALF = 4 * 1024` 都是 `const`，
全树 grep 不到任何环境变量或配置项。对编码场景够用；对数据密集型场景
（财务报表、全市场筛选）截断是常态。我们实测过一次对话里 5 次工具调用 4 次被截断，
模型没有去调 `fetch_output`，而是在正文里写出了三个任何一份工具返回里都搜不到的数字
并标上了工具来源。想问：① 是不是有我们没找到的配置入口？② 若没有，是否接受一个
「阈值可配置、默认值与现在逐字节一致」的补丁？③ 更轻的方向——截断提示里能否带上
「第几段 / 共几段」的结构信息？
（我们这边已经在自己的 MCP 层把返回压到阈值以下，所以不阻塞。）

**B12 · `GET /live?session_id=` 被「孤儿 runtime」占住后无法恢复**
`native_live.rs:394-401`：换绑时若当前 runtime 的 `phase` 属于
`InTurn | WaitingApproval | Reconfiguring`，返回 HTTP 404
`{"error":"cannot replace an active live runtime"}`。此后任何带 `session_id` 的 `/live`
都是 404，而 `/health`、`/mcp/status`、`/projects/{hash}/sessions/{id}` 全部正常。
若消费端在 `InTurn` 期间断开且没有别的路径让 runtime 收敛，这个状态就出不来了。
想请教：有没有推荐的显式释放端点或恢复姿势？（我们目前的绕法是识别到这个 404 之后
调 `/live/stop` 再退避重试，但它依赖「网页是唯一合法消费者」这个前提。）

**E1 · `latest.json` 在 release tag 上写的是上一版的校验值**
```
git show v5.1.0:latest.json | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])'
# → v5.0.9
git show v5.0.9:latest.json | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])'
# → v5.0.8
```
同步 commit（`chore: sync latest.json from release vX.Y.Z`）落在打 tag 之后，
所以「checkout tag → 读 latest.json → 校验二进制」这条最自然的路径会锁到**上一版**的 sha256。
建议之一：把同步 commit 放到 tag 之前，或在 `latest.json` 里加一个「本清单对应哪个 commit」
的字段，让使用方能自检。顺带想确认 `latest.json` 是不是推荐的版本锁定入口（见 B6）。

## 其余各条（一行摘要 + 我们的处置）

### A · 文档与实现不一致

| # | 摘要 | 我们的处置 |
|---|---|---|
| A1 | `atomcode daemon` 没有 `--host` 参数，但 `crates/atomcode-daemon/README.md` 的启动参数表里有 | 改走 compose 内网访问，不暴露端口 |
| A2 | `ATOMCODE_DAEMON_ENABLE_DANGEROUS_TOOLS` 实际不门控任何工具；唯一调用点 `lib.rs:6700` 只打一行启动警告 | 不依赖它，权限用 hook 拦 |
| A3 | 独立 daemon 实测强制 token 鉴权（不带 token 的 `GET /config` 返回 401），而源码注释说不强制 | 按「强制」设计部署 |
| A4 | README 的「可用工具」表列 12 个，实测 36 个 | 文档按实测写 |
| A5 | `atomcode hooks test <NAME>` 的 NAME 不是 `.hooks.json` 里的键名 | 用 command 子串点名 |
| A6 | `docs/hooks.md` / `docs/webhook-guide.md` 与实现不符（实现是 8 个事件、`.hooks.json`、只支持 shell） | 一律以源码为准 |
| A7 | 技能 frontmatter 的 `allowed-tools` 解析了，但全树没有消费者 | 照写，不指望它生效 |
| A8 | `.hooks.json` 不许写注释（而 `.mcp.json` 许），**并且解析失败是静默的** —— 写错一个逗号，hook 全部不挂而没有任何提示 | 模板不写注释 + 部署时自检条数 |
| A9 | hook 的 `command` 不做环境变量展开，而 `.mcp.json` 做 | hook 里写绝对路径 |

A8 单独说一句：静默失败这一条我们觉得最值得改——启动时打一行 warn 就够。

### B · 语义与能力确认

| # | 摘要 | 我们的处置 |
|---|---|---|
| B2 | `/live/message` 没有 `working_dir`，一个 daemon 只能服务一个工作目录 | 定型为单工作区单 daemon |
| B3 | `/mcp/status` 在刚 `/cd` 到未信任项目时会返回自相矛盾的一帧 | 前端容错 |
| B4 | `plan` 档的精确边界（哪些写操作会被拦）文档里没写清 | 改用 `build` 档 + 自己的 guard hook |
| B5 | `accept_edits` 档对工作区**外**的写也不弹权限 | 不用这个档 |
| B6 | 版本锁定该以什么为准（`latest.json` / release 附件 / npm `dist.integrity`），README 没指明 | `pins.lock` 同时锁 sha256 与 size，双源校验 |
| B7 | 系统人设能否整体替换（而不是只做追加） | 用 `.atomcode.md` 覆盖 |
| B8 | 官网提到的「定制我的领域」具体指哪个机制？ | — |
| B9 | `/live/switch_session` 之后模型手里的 MCP 工具全部消失，而 `/mcp/status` 仍显示 connected | 不用 `switch_session`；改 `POST /mcp/reload` 并**等到没有 connecting** |
| B10 | 官方二进制的 glibc 基线没有公开说明（实测官方需 2.17，我们自编的需 2.34） | 只分发 Docker 镜像 |
| B13 | `POST /cd` 到**同一个**目录不会开新会话 | 用 `POST /sessions` |
| B14 | `/live` 的 `tokens` 事件与 `state.stats` 多数轮次为 0 | 计量一律用网关侧配额差值 |

B9 是我们整个过程里最难定位的一条：状态全绿，而模型看不见工具，退化成 `bash` 在工作区里乱翻
（有一次因此连发 26 次 `bash`，白烧了约 102 万 token）。
如果 `/live` 的 snapshot 帧里能带上「当前 runtime 实际挂载的工具清单」，
这类问题对第三方前端就从「完全没有观测手段」变成「一眼可见」。这也是我们唯一一条
带有明确接口建议的请求。

### C · 两个可能的小缺陷

| # | 摘要 | 我们的处置 |
|---|---|---|
| C1 | `/chat` 的工具 schema 里字段名不统一（`read_file` 的报错信息本身很清楚，是 schema 与之不一致） | — |
| C2 | 流式 `tool_calls` 缺 `index` 时，`openai_compat.rs` 的 `tc.index.unwrap_or(0)` 会把多个并行调用合成一个脏调用 | 网关侧已修；我们的 shim 里留了兜底 |

C2 补充一个可能有用的对照：`@ai-sdk/openai-compatible` 按 `id` 归并、不依赖 `index`，
所以同一个上游服务喂给两边，只有 AtomCode 这一侧会合并。

### D · 垂直发行版视角的四条

| # | 摘要 | 我们的处置 |
|---|---|---|
| D1 | `POST /live/switch_session` 在 daemon 刚起、还没绑过会话时恒返回 `Unbound` | 当良性处理 |
| D2 | `plan` 档的只读强制与「出方案后停下」被绑在一起，非编码场景想要前者不想要后者 | 改用 `build` + guard |
| D3 | 8 个代码智能工具无条件挂载，没有工具白名单 | 在 `.atomcode.md` 里点名别调（提示词约束，不是硬约束） |
| D4 | `skill_first.rs` 按模型名门控，接自建网关的垂直发行版反而用不上 | 用 `use_skill` 显式加载 |
| D5 | `ProviderConfig.system_prompt` / `ModelConfig.system_prompt` 是死字段（解析了但没有读取方） | `.atomcode.md` 覆盖 |

D3 与 D5 是垂直发行版最想要的两个入口：**工具白名单**与**人设整体替换**。

其中后者（D5 / B7）我们写过一份最小补丁并在本地验证过：`persona.rs` 加一个
`resolve_persona(override, built_in)`，再让 model 配置里**现有的** `system_prompt`
字段真正被读到，另补一个文件形式的 `system_prompt_file`。
共 18 个文件、+311 / −24 行（其中 11 个文件只是给结构体字面量补一个 `None`），
`cargo fmt --all --check` / `cargo check --workspace --all-targets` /
`clippy -D warnings` 三道门全绿，并有一条逐字节比对的单测钉住「未配置时与改前完全一致」。
但我们**最终没有启用它**——A/B 评测显示不 fork 也够用，就不想给贵方增加评审负担。
若这个方向贵方有兴趣，我们可以整理成 PR；方向不对的话也完全不必回复。

工具白名单（D3）我们没有动手，因为不确定贵方期望的形态（配置项 / 启动参数 / 按 profile）。

E4 顺带带出一个更一般的请求：`PreToolUse` 与 `PostToolUse*` 两个事件都不带 call_id，
没有任何字段能把一次调用的「参数 / 耗时 / 结果」配起来。我们现在是由 `PreToolUse`
那一侧先落一份文件、`PostToolUse` 再去配，可这在并发同名调用下配不出唯一解。
加一个进程内自增的 call_id 就够。

### E · 其余三条

| # | 摘要 | 我们的处置 |
|---|---|---|
| E2 | `GET /skills` 只回 `{name, description}`，SKILL.md frontmatter 里的自定义字段全部丢弃 | 前端要的扩展字段改由我们自己的后端提供 |
| E3 | 免费公共算力能否面向金融领域的用户开放？（这条是使用政策问题，不是技术问题） | — |
| E4 | 被 `PreToolUse` 拒绝的调用照常触发 `PostToolUseFailure`，但那一帧**不带 `tool_name`**（同一份 hook 在成功的 `PostToolUse` 上每次都拿得到），于是审计里记不下"哪个工具被拦了" | 工具名改从我们自己的 `PreToolUse` 日志里取 |

## 最后

感谢 AtomCode 这个项目。daemon 的 HTTP/SSE 接口、hook 与 MCP 三层拆得很清楚，
我们整个发行版**没有改一行内核**就做出来了，这在同类项目里不多见。
上面这些条目请按贵方的节奏处理，不必回复我们。
