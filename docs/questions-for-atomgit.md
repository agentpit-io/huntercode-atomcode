# 待向 AtomGit / AtomCode 团队确认的问题（M5 定稿）

> **状态：定稿**（2026-09-22，M5 / 计划 v0.2 TP-11）。
>
> 来源：HunterCode·AtomCode 发行版（HCA）M0 → M5 的逐条实测，基于 **v5.1.0**
> 官方二进制（sha256 `40d86fa3…`）与上游源码 `e4215f7` / `254d14a84` / tag `v5.1.0`(`72b538e8c`)。
> **每条都带最小复现或源码行号**，没有一条是"看文档觉得"。
>
> 本文件只记录**以源码实测为准、与上游文档不符**或**语义不明**的点，
> 不含我们自己的设计问题（那些在 `docs/开发文档/待办池.md`）。

## 0. 索引（M5 新增）

共 **34 条**。「我们的处置」一栏说明本发行版是怎么活下来的 —— 提问不等于等答案。

| # | 主题 | 发现于 | 我们的处置 | 进汇总 issue |
|---|---|---|---|---|
| A1 | `daemon` 没有 `--host`，README 写了 | M0 | compose 内网访问，不暴露 | ✅ |
| A2 | `ATOMCODE_DAEMON_ENABLE_DANGEROUS_TOOLS` 不门控任何东西 | M0 | 不依赖它，用 hook 拦 | ✅ |
| A3 | 独立 daemon 强制 token 鉴权，注释说不强制 | M0 | 按"强制"设计部署 | ✅ |
| A4 | README 工具表列 12 个，实际 36 个 | M0 | 文档按实测写 | ✅ |
| A5 | `hooks test <NAME>` 的 NAME 不是配置键名 | M1 | 用 command 子串点名 | ✅ |
| A6 | `docs/hooks.md` / `webhook-guide.md` 与实现不符 | M0 | 一律以源码为准 | ✅ |
| A7 | 技能 `allowed-tools` 解析了但没有消费者 | M1 | 照写不指望它生效 | ✅ |
| A8 | `.hooks.json` 不许注释且解析失败**静默** | M1（原误编为 A5） | 模板不写注释 + 部署自检 | ✅ |
| A9 | hook 的 `command` 不做环境变量展开 | M1（原误编为 A6） | 写绝对路径 | ✅ |
| B1 | **`/chat` 不挂载 MCP 工具，`/live` 挂载** | M0 | 转发层只走 `/live` | ✅ **单独提** |
| B2 | `/live` 没有 `working_dir`，一 daemon 一工作目录 | M0 | 单工作区形态（已拍板决策 5） | ✅ |
| B3 | `/mcp/status` 在刚 `/cd` 时返回自相矛盾的一帧 | M0 | 前端容错 | ✅ |
| B4 | `plan` 档的精确边界 | M0 | 改用 `build` + guard hook | ✅ |
| B5 | `accept_edits` 对工作区外的写也不弹权限 | M0 | 不用这个档 | ✅ |
| B6 | 版本锁定该看什么 | M0 | pins.lock 锁 sha256 + size | ✅ |
| B7 | 人设是否会支持整体替换 | M0 | `.atomcode.md` 覆盖（fork 补丁待命） | ✅ |
| B8 | 官网"定制我的领域"指什么机制 | M0 | — | ✅ |
| B9 | 切会话后 MCP 工具全消失而状态仍 connected | M1 | 不用 `switch_session` + reload 等待 | ✅ |
| B10 | 官方二进制的 glibc 基线没公开说明 | M2 | 只分发 Docker 镜像 | ✅ |
| B11 | **工具返回 16 KB 截断阈值写死、无配置入口** | M2 | M5 在 MCP 侧加大小闸（见下） | ✅ |
| B12 | **`GET /live?session_id=` 被孤儿 runtime 占死** | M4 | BFF 识别 404 后收孤儿重试 | ✅ |
| B13 | `POST /cd` 到同目录不开新会话 | M1（原误编为 B7） | 用 `POST /sessions` | ✅ |
| B14 | `tokens` 事件与 `state.stats` 多数轮次为 0 | M1（原误编为 B8） | 用网关配额差值计量 | ✅ |
| C1 | `/chat` 工具 schema 字段名不统一 | M0 | — | ✅ |
| C2 | 流式 `tool_calls` 缺 `index` 被合成脏调用 | M0 | 网关侧已修；shim 留兜底 | ✅ |
| D1 | 冷启动时 `switch_session` 恒 `Unbound` | M2 | 按良性处理 | ✅ |
| D2 | `plan` 档只读与"出方案就停"被绑死 | M2 | 改用 `build` + guard | ✅ |
| D3 | 8 个代码智能工具无条件挂载 | M2 | `.atomcode.md` 里点名别调 | ✅ |
| D4 | `skill_first.rs` 按模型名门控 | M2 | 用 `use_skill` 显式加载 | ✅ |
| D5 | `system_prompt` 是死字段 | M2 | fork 补丁已写好未启用 | ✅ |
| **E1** | **`latest.json` 在 tag 上写的是上一版** | **M5** | 按内容回溯同步 commit | ✅ |
| **E2** | **`GET /skills` 丢掉自定义 frontmatter 字段** | M0/M5 | 前端字段由 api 提供 | ✅ |
| **E3** | **免费公共算力能否面向金融领域用户开放** | 计划 §10-6 | — | ✅ |
| **E4** | **被 hook 拒绝的调用，`PostToolUseFailure` 不带 `tool_name`** | **M5 回归实测** | 工具名另从 `guard.jsonl` 取 | ✅ |

**提 issue 的授权与计划**（总控规则 已拍板决策 8 · 红线 7）：**只提两个** ——
B1 单独一条（对第三方前端影响最大），其余合并成一个汇总条目。


## A. 文档与实现不一致（建议修文档或修实现）

### A1 · `atomcode daemon` 没有 `--host`，但 README 写了

`crates/atomcode-daemon/README.md` 的"启动参数"表列了 `--host <ip>`，并配了
"绑定非 loopback 会打印安全警告"的说明；`lib.rs` 里确实有 `host != "127.0.0.1"` 的警告分支。
但 5.1.0 官方二进制的 CLI 没有暴露这个参数：

```
$ atomcode daemon --help
Options:
      --port <PORT>                  Port to listen on [default: 13456]
      --client <CLIENT>              …
      --idle-timeout <IDLE_TIMEOUT>  …
      --no-auth                      Disable daemon API token authentication
      --no-telemetry                 …
```

**问题**：是有意收回（只允许 loopback + 反代）还是漏接线？容器化部署里 daemon 与调用方
不在同一个网络命名空间时，官方建议的做法是什么？

### A2 · `ATOMCODE_DAEMON_ENABLE_DANGEROUS_TOOLS` 不门控任何东西

README 写"设为 `1` 启用 bash 和写文件的 daemon 工具"。源码里 `DANGEROUS_TOOLS_ENV`
（`lib.rs:641`）只被 `dangerous_tools_enabled()`（`lib.rs:1268`）读一次，而该函数**全仓库唯一的
调用点**是 `lib.rs:6700` 的启动横幅警告。实测不设这个变量，`/chat` 一样能跑 bash 和 `write_file`。

**问题**：这是刻意移除了门控（改由 approval_mode 承担）还是回归缺陷？如果是前者，README 该删。

### A3 · 独立 daemon 实测强制 token 鉴权，源码注释说不强制

`lib.rs:6517` 的注释写「仅 webui 模式（`enforce_token=true`）强制 token 鉴权；
独立 daemon/VSCode（`enforce_token=false`）中间件直接放行」。实测 `atomcode daemon --port 13456`
（无 `--no-auth`）时，无 token 访问 `/config` 返回 **401**，且照常写出了 `~/.atomcode/daemon-13456.json`。

**问题**：注释过时了，还是 `enforce_token` 的取值逻辑有变？（对我们是好事，只是想确认这是稳定行为。）

### A4 · README 的"可用工具"表只列了 12 个，实际 36 个

daemon 对话模式实际暴露给模型的工具（5.1.0 实测，让模型自己列）：

```
ast_grep atomgit_api atomgit_issue atomgit_pr atomgit_repo bash blast_radius
code_review edit_file fetch_output file_dependencies find_references glob grep
list_directory list_sessions list_skills list_symbols memory open_file read_file
read_symbol recall request_user_input schedule_wakeup search_replace task team
todowrite trace_callees trace_callers trace_chain use_skill web_fetch web_search
write_file
```

**问题**：有没有一份权威的工具清单（含各自的参数 schema）可以引用？我们做企业内发行版需要
维护工具白名单，靠"问模型"不是办法。

### A5 · `atomcode hooks test <NAME>` 的 NAME 不是配置里的键名

`.hooks.json` 的形状是 `{"hooks": {"<名字>": {...}}}`，但 `hooks test` 按**事件名或命令子串**匹配：

```
$ atomcode hooks test deny-write-file
❌ No hook matching 'deny-write-file' found.
Available hooks (test by event name or a command substring): …
```

而且它喂给 hook 的是固定样例 payload（`tool_name: "bash"`、`tool_input: {"command":"echo hello"}`），
**不按该 hook 的 `matcher` 生成**。结果是：一个 `matcher: "write_file"` 的 deny hook，在
`hooks test` 里收到的是 `bash`，回 allow，测试"通过"，但拦截逻辑一点没验到。

**问题**：能不能支持按配置键名匹配，并按 `matcher` 生成对应 `tool_name` 的样例？

### A6 · `docs/hooks.md` / `docs/webhook-guide.md` 与实现不符

实际生效的只有 8 个事件（`cc_hooks.rs:55`：PreToolUse / PostToolUse / PostToolUseFailure /
SessionStart / SessionEnd / UserPromptSubmit / Stop / StopFailure），配置文件是 `.hooks.json`，
**只支持 shell**，webhook 属旧引擎已不触发。

**问题**：这两份文档会更新吗？webhook 能力会在新引擎回归吗？（我们的审计/预算上报本来想走 webhook，
现在改成 shell 脚本里自己 curl。）

### A8 · `.hooks.json` 不许写注释，而 `.mcp.json` 许 —— 并且解析失败是**静默**的

> **编号更正（M5）**：本条 M1 写入时误用了 `A5`（与 A 组前面一条重号），定稿改为 `A8`。
> 旧文档里引用的「A5」若指的是这条内容，看这里。

`.mcp.json` 的解析器会剥 `//` 与 `/* */` 注释；`.hooks.json` 不会
（`atomcode-capabilities/src/cc_hooks.rs:174` 是裸的
`serde_json::from_str::<HooksFile>(&raw)`）。更麻烦的是失败路径：

```rust
let Ok(parsed) = serde_json::from_str::<HooksFile>(&raw) else {
    return Vec::new(); // malformed → skip the file rather than wedge startup.
};
```

解析不过就当**没有 hook**，不报错也不打日志。诊断命令给出的信号是自相矛盾的：

```
$ atomcode hooks list
Loaded Hooks:
  (No hooks loaded)
Hook Config Files:
  ✓ Project:  /workspace/.hooks.json     ← 文件找到了，却一条都没加载
```

HCA M1 在容器里踩到：`.hooks.json` 里带了 `//` 说明，结果审计 hook 一次都没触发，
而 `hooks list` 显示文件 ✓ 存在。排查了一圈才定位到是注释。

**建议**：① 两个配置文件的注释策略统一；② 解析失败至少 warn 一行（文件路径 + serde 错误），
或者让 `hooks list` 把"文件存在但解析失败"与"文件存在且 0 条 hook"区分开。

### A9 · hook 的 `command` 不做环境变量展开，`.mcp.json` 做

> **编号更正（M5）**：本条 M1 写入时误用了 `A6`（与 A 组前面一条重号），定稿改为 `A9`。
> 旧文档里引用的「A6」若指的是这条内容，看这里。

`mcp/config.rs:315-336` 对 MCP 的 `command` / `args` / `env` 都走
`expand_env_vars`（支持 `${VAR}` 与 `${VAR:-默认值}`，`config.rs:797+` 有单测）。
hook 那边没有这一层，`.hooks.json` 的 `command` 是原样当命令用。

**问题**：这是有意的（hook 命令要可审计、不许被环境变量改写）还是没接？
容器里工作区路径是可配的，hook 命令写死绝对路径会让镜像无法通用 ——
我们现在的做法是在启动脚本里自己渲染一遍。

### A7 · 技能的 `allowed-tools` 解析了但没有任何消费者

`skills/skill.rs:260` 解析 `allowed-tools` 存进 `Skill.allowed_tools`，
字段注释写着 "the L1 capability does not enforce it — that's an L2 approval-policy
concern"。但全仓库 grep `.allowed_tools` 只有定义处与构造处，**没有读取方**；
`GET /skills` 也只回 `{name, description}`，拿不到它。

**问题**：L2 那层是在路线图上还是已经删了？我们按 AgentSkills 规范写了这个字段
（`mcp__uzi__stock_deep_analysis` 这类真名），想确认将来生效时语义是"白名单"还是
"免确认清单"——两者对我们写什么值影响很大。

---

## B. 语义与能力确认

### B1 · `/chat` 为什么不挂载 MCP 工具，而 `/live` 挂载？（对我们影响最大）

同一时刻 `/mcp/status` 显示 `{"name":"akshare","status":"connected","tool_count":3}`，但：

| 通道 | 模型可见工具数 | 含 `mcp__akshare__*` |
|---|---|---|
| `POST /chat` | 36 | **否**（调用直接返回 `unknown or unmounted tool: mcp__akshare__akshare_call`） |
| `GET /live` + `POST /live/message` | 39 | 是 |
| CLI headless `atomcode -p` | 39 | 是 |

源码上看，MCP 是异步补挂的（`atomcode-coding/src/parts.rs:805` 一带，注释
"never await them on the session candidate path"），而 `wait_mcp_ready()` 只有三个调用点
（`atomcode-clix/src/code.rs:258`、`atomcode-daemon/src/native_live.rs:458`、
`atomcode-cli/src/main.rs:3018`），`/chat` 的 `process_chat_request` 不在其中——
该函数的 `_mcp_cache` 参数带下划线前缀，注释写「this per-project cache is warmed by the
/context, /compact and /live paths, not the chat turn」。

**问题**：
1. 这是有意的取舍（`/chat` 要低延迟、不等 MCP）还是待补的缺口？
2. `/chat` 上**永远**不会有 MCP 工具，还是"第一轮没有、之后会有"？我们实测同一会话连续两轮都没有。
3. 如果 `/chat` 不打算支持 MCP，README 里 `/chat` 的描述是否该注明？它现在读起来像是全功能对话端点。
4. **`/live` 是否是官方推荐给第三方前端的对话通道？** 它的 wire 事件（`snapshot` / `state` / `user` / …）
   与 `/chat` 的 `ChatEvent` 是两套，稳定性承诺一样吗？

### B2 · `/live` 没有 `working_dir`，一个 daemon 只能服务一个工作目录？

`POST /live/message` 的请求体（`live_api.rs:1447`）没有 `working_dir`，
`live_stream` / `live_message` 都取 `state.project.working_dir`（由 `POST /cd` 改）。
而 `POST /chat` 是按请求带 `working_dir` 的，实测两个工作目录并发对话互不串扰。

**问题**：`/live` 支持多工作目录并发的计划是什么？多用户/多项目的前端目前只能"每个工作目录一个 daemon 进程"吗？

### B3 · `/mcp/status` 在刚 `/cd` 到未信任项目时返回自相矛盾的一帧

```
# 当前目录切到 ws2（ws2 的 .mcp.json 未信任），ws1 已信任
$ curl -s .../mcp/status
{"servers":[{"name":"akshare","status":"connected","tool_count":3}],"trusted":false,"blocked":["akshare-ws2"]}
```

`servers` 里是**上一个目录（ws1）**的注册表（`lib.rs:5210` 的 `mcp_cache` 查不到当前目录就回退到
`state.mcp_registry`），`blocked` 是当前目录的。前端照这一帧渲染会显示"ws2 里连着一个 ws1 的 server"。

**问题**：是否应该在 `mcp_cache` 未命中当前目录时返回空 `servers` 而不是回退？

### B4 · `plan` 档的精确边界

实测（`/chat` + `approval_mode: "plan"`）：

| 动作 | 结果 |
|---|---|
| `read_file` | 放行 |
| 工作区内 `bash`（`python3 -c "print(1+1)"`） | **放行** |
| 工作区外 `bash`（写 `/home/support/hca/outside/...`） | 弹权限，拒绝后 `blocked: destructive bash denied by approval policy` |
| `write_file` / `edit_file`（工作区内外都一样） | `blocked: plan mode is active — \`write_file\` would modify the workspace and is blocked. Only read-only tools are allowed: …` |

**问题**：`plan` 只拦"写文件类工具"，不拦 bash 本身（bash 走的是另一条 `BashWorkspaceGate`）。
那么 `plan` 档下用 `bash` 写工作区内的文件是**设计允许**的吗？我们想用 `plan` 做"只读研究模式"，
这个口子会让它名不副实。有没有计划加一个"真只读"档，或者让 `plan` 也管住 bash 的写行为？

### B5 · `accept_edits` 对工作区外的写也不弹权限

实测：`accept_edits` 档下 `write_file` 到工作区外的 `/home/support/hca/outside/escape.txt`
**直接写成功，没有弹权限**；同一档下写 `.env`（敏感路径）会弹。

看源码是有意的：`write_approval.rs` 的顺序是「敏感 → 弹（不记住）」→「accept_edits → 直接 Allow」
→「in_workspace → Allow」→「其余 → 弹」，`accept_edits` 的判断**排在 in_workspace 之前**。

**问题**：`accept_edits` 的文案语义是"自动接受编辑"，用户大概率理解成"自动接受**这个项目里的**编辑"。
把工作区外的写也包进去是刻意的吗？

### B6 · 版本锁定应该看什么

仓库根 `latest.json` 给了 version + 6 个平台的 sha256/size，tag 是"快速发布-atomcode-release-时间戳"
形式。我们实测：**npm 包 `@atomgit.com/atomcode@5.1.0-linux-x64` 里的二进制与 `latest.json` 的
linux-x64 条目逐字节一致**（sha256 `40d86fa3…8c8763`，size 40214480），而且 `GET /health` 会回显
同一个 `binary_hash`。

**问题**：
1. 官方推荐的版本锁定依据是 `latest.json` 还是某个 tag？
2. npm 包与 release 二进制"永远同一文件"是可以依赖的承诺吗？
   （atomgit 的 release 直链在美国机房返回 418，我们只能走 npm。）
3. 技能、hook（`.hooks.json` 8 事件）、MCP 配置格式在 5.x 内有向后兼容承诺吗？

### B7 · 人设是否会支持整体替换

`atomcode-coding/src/persona.rs`（约 690 行）里的编码人设没有配置开关，项目指令
（`.atomcode.md` / `ATOMCODE.md` / `AGENTS.md` / `CLAUDE.md` 取其一）**只能追加**；
人设里有"项目指令优先"的声明。model 配置里的 `system_prompt` 字段没有被读取。

**问题**：
1. `system_prompt` 是遗留未接线，还是有意保留？
2. 有没有计划支持"由配置指定外部人设文件整体替换"（未配置时行为不变）？
   我们做的是**投研**垂直发行版，编码人设是最大的改造阻力。
   如果上游不打算做，我们会 fork 出最小补丁并提 PR（已获内部授权）。

### B8 · 官网"定制我的领域"指的是什么机制？

我们没有在源码里找到任何"领域定制 / 垂直发行版"的官方机制（既没有 persona 替换，
也没有领域包/预设的装载点）。

**问题**：官网提到的"定制我的领域"具体是指 skills + `.atomcode.md` 这一套，还是另有规划？
垂直发行版能否进入官方目录？

### B9 · `/live/switch_session` 之后 MCP 工具全部消失，但 `/mcp/status` 仍显示 connected

与 B1 同一类，但更隐蔽。受控实验（同一 daemon、同一个问题、只差一步）：

| 步骤 | 模型自述可调的 `mcp__*` 工具 | 同一时刻 `/mcp/status` |
|---|---|---|
| 不切会话，直接问 | **28 个**（逐个列出来了） | 9/9 connected |
| `POST /sessions` + `POST /live/switch_session` 后问 | **0 个**（「当前运行环境中并未注册或挂载任何 MCP 服务工具」） | 9/9 connected |
| 再 `POST /mcp/reload`，**立刻**问 | 0 个 | 有 `connecting` |
| 再 `POST /mcp/reload`，**等到没有 connecting** 再问 | **28 个** | 9/9 connected |

看起来 `resume_session_with_lease` 这条路没有 `wait_mcp_ready()`
（与 `/chat` 的 `process_chat_request` 同一个问题）。

后果不是"少几个工具"这么简单：模型看不到数据源就会拿内置 `bash` 自己写
`python3 -c "import requests; ..."` 去爬，我们实测一轮里连发 26 次 bash、
烧掉约 102 万 token 也没拿到想要的数据。而运维侧从 `/mcp/status` 上
**完全看不出异常**。

**建议**：① `switch_session` / `resume_session` 也走 `wait_mcp_ready()`；
② 或者至少让 `/mcp/status` 反映「注册表连着，但当前 live 会话没挂上」这个区别 ——
现在这两种状态在 API 上不可分辨。

### B13 · `POST /cd` 到**同一个目录**不会开新会话

> **编号更正（M5）**：本条 M1 写入时误用了 `B7`（与 A 组前面一条重号），定稿改为 `B13`。
> 旧文档里引用的「B7」若指的是这条内容，看这里。

`ChangeDirRequest` 的注释写着不带 `session_id` 时广播 `WorkingDirChanged`
=「cd + 开新会话」。实测 `POST /cd {"path": "<当前目录>"}` 之后，`/live` 上的
`session_id` 不变，上一轮的上下文照样在 prompt 里。

我们要的是"每个测试用例一个干净会话"，最后走的是
`POST /sessions` 建新会话 + `POST /live/switch_session` 切过去（这条可用）。

**问题**：`/cd` 到同目录属于 no-op 是有意的吗？有没有一个"就在当前目录开一个新会话"的
单步端点（webui 的"新建对话"按钮走的是哪条路）？

### B14 · `/live` 的 `tokens` 事件与 `state.stats` 多数轮次为 0

> **编号更正（M5）**：本条 M1 写入时误用了 `B8`（与 A 组前面一条重号），定稿改为 `B14`。
> 旧文档里引用的「B8」若指的是这条内容，看这里。

接 OpenAI 兼容网关（Gemini 上游）时实测：一条 12 轮的会话里 12 个 `tokens` 事件
全是 `{"prompt":0,"completion":0,"total":0}`，而同一个会话的**第一轮**拿到过
`{"prompt":25610,"completion":43,"total":25653}`。`state.stats` 里的
`prompt_tokens` / `completion_tokens` 同样多数为 0。

看起来是网关只在部分响应里回 `usage`，AtomCode 如实透传、不做累计。

**问题**：`state.stats` 里的 token 字段语义是"本轮"还是"整个会话累计"？
如果是后者，上游没回 `usage` 的轮次能不能不要把累计值冲成 0？
（调用方想展示"这次对话花了多少 token"时，现在只能自己去查网关配额差值。）

---

## C. 一个可能的小缺陷

### C1 · `read_file` 的参数名报错信息很好，但 `/chat` 的工具 schema 里字段名不统一

模型一次用 `{"path": "..."}` 调 `read_file`，返回：

```
read_file: invalid arguments: missing field `file_path` at line 1 column 26. Expected {"file_path": "<path>"}.
```

报错很清楚（点赞）。只是想确认：`read_file` / `write_file` / `edit_file` 一律是 `file_path`，
`search_replace` 是 `path`，这个不一致是有意的吗？

### C2 · 流式 `tool_calls` 缺 `index` 时，`unwrap_or(0)` 会把多个并行调用合成一个脏调用

`crates/atomcode-capabilities/src/provider/openai_compat.rs:1710`：

```rust
let idx = tc.index.unwrap_or(0);
```

OpenAI 流式协议里 `index` 是可选字段（`#[serde(default)] index: Option<usize>`，
你们自己的结构体也是这么声明的）。我们实测的一个 OpenAI 兼容网关（Gemini 上游）
**发并行 `tool_calls` 时每个分片都带唯一 `id`、但不带 `index`**。
这时三个调用全落进槽位 0：`name` 互相覆盖（最后一个赢）、`arguments` 首尾相接，
结果是一个参数被污染的调用，例如把 `bash` 的 `command` 混进了 `write_file`：

```json
{"name":"write_file",
 "arguments":{"file_path":"rv-write.txt","command":"python3 -c \"print(1+1)\"","content":"已验证"}}
```

下游表现是工具反复重试直到触发你们的 `tool_loop_detected`。

**建议**：`index` 缺失时回退到按 `id` 分组（`id` 在这类网关上是可靠的），
只有 `id` 也没有时才落到槽位 0。大致是：

```rust
let idx = match tc.index {
    Some(i) => i,
    None => match tc.id.as_deref() {
        Some(id) if !id.is_empty() => *id_slots.entry(id.to_string())
            .or_insert_with(|| { let n = next_slot; next_slot += 1; n }),
        _ => next_slot.saturating_sub(1),   // 无 id 的续传帧 → 接在最近一个调用上
    },
};
```

**问题**：这个回退你们愿意收吗？如果愿意，我们可以按 M2 的 fork 授权提一个 PR。
（我们当前是在自己的网关前置一层 shim 把 `index` 补回去绕过的，
但守协议的客户端普遍会踩这个坑，修在客户端更通用。）

---

## D. M2 新增（2026-09-22）

### D1 · `POST /live/switch_session` 在 daemon 刚起、还没绑过会话时恒返回 `Unbound`

```
POST /sessions            → 200 {"id":"…"}      # 会话建出来了
POST /live/switch_session → {"ok":false,"active_turn":false,
                             "error":"session switch rejected: Unbound"}
```

只在**容器/进程刚重建、一条消息都还没发过**时出现；发过一条消息之后再切就正常。

**问题**：
1. 「没有 live 会话可切」和「切换被业务逻辑拒绝」用的是同一个 `error` 串，调用方
   区分不了"这是良性的冷启动状态"还是"真出错了"。能不能给冷启动一个单独的错误码，
   或者干脆允许在未绑定时直接绑上去？
2. 有没有一个官方的"把 live 绑到某个会话"的冷启动入口？我们现在的绕法是
   把第一次失败当良性（反正冷启动时本来也没有上一轮上下文要清），但这是推断，
   不是文档保证的。

### D2 · `plan` 档的 `PlanModeReminderHook` 对非编码场景是硬伤

`atomcode-coding/src/plan_mode.rs` 的 `PLAN_MODE_REMINDER_BODY` 在 plan 模式下
**每一次请求**都注入：

> … present a concise implementation plan and **STOP, waiting for the user to review
> and switch to build mode**.

对编码场景这完全合理。但 `plan` 同时也是**唯一一个不需要人工点确认就能把四个写类
工具全部拦死**的档（M0 §4 实测），于是任何"只读模式"的非编码用法都被迫连这条
"给个方案然后停下来"一起吞下去 —— 投研助手照做就变成「我打算去查行情，请批准」。

**问题**：能不能把「只读强制」与「先出方案再等批准」拆成两件事？
比如 `plan` 保持现状，另加一个 `readonly` 档只做工具层的只读强制、不注入那条提醒。
（我们当前的绕法是用 `build` 档 + PreToolUse hook 自己拦，hook 连 `bypass` 都压得住，
能用；但那等于每个垂直发行版都要重写一遍只读策略。）

### D3 · 8 个代码智能工具无条件挂载，没有工具白名单

`register_codeintel_tools`（`parts.rs:494`）无条件注册 `list_symbols` / `read_symbol` /
`find_references` / `trace_callers` / `trace_callees` / `trace_chain` / `blast_radius` /
`file_dependencies`，另有 `ast_grep` / `code_review`。这些在非代码工作区（我们的是
markdown 研究资产）一个都用不上，但它们的 schema 一直占着上下文预算，而且模型
随时可能去调。

`todowrite` / `request_user_input` / `memory` / `task`+`team` 都有环境变量开关
（我们实测关掉这四类每轮省 3 936 prompt token，28 904 → 24 968），
代码智能这一组没有。

**问题**：有没有计划加一个工具白名单/黑名单配置项（`[tools] disable = [...]` 之类）？
对垂直发行版来说这比逐个加环境变量开关更通用。

**I2（2026-09-22）补一组实测数字**，把「占着上下文预算」量化成了具体的字节：
一次真实请求里**共 59 个工具、schema 合计 59 885 字节、输入 26 132 token**；
其中本场景一次都用不上的是 **17 个 / 17 407 字节（29%）**：

| 字节 | 工具 |
|---|---|
| 7 586 | `atomgit_repo` / `atomgit_pr` / `atomgit_issue` / `atomgit_api`（`assemble.rs:274`，同样无条件挂载） |
| 7 366 | `code_review` / `ast_grep` / `read_symbol` / `list_symbols` / `find_references` / `trace_callers` / `trace_callees` / `trace_chain` / `blast_radius` / `file_dependencies` |
| 2 455 | `recall` / `list_sessions` / `schedule_wakeup` |

也就是说 **AtomGit REST 那一族（`atomgit_*`）比代码智能那一族还大**，
而它在没有 AtomGit 仓库的工作区里同样一个都用不上 —— 所以白名单最好覆盖到它。

我们的绕法是在自己的 OpenAI 兼容代理里把这些工具从请求体里摘掉（内核那边仍然注册着，
所以不会出现「说有却调不动」）。这能用，但它要求发行版必须自带一跳代理 ——
**如果上游给一个配置项，这一跳就可以只做它本来该做的事**。

顺带一条同类的：`ATOMCODE_AI_SESSION_NAMING` 默认开，每轮对话结束后会**再发一次
模型请求**给会话起标题（实测 3 817 ms / 552 输入 token）。对「会话标题由宿主应用
自己管」的嵌入式用法，这一跳是纯开销。它**有**环境变量开关（我们已经关掉），
只是默认值对嵌入式场景不太友好，记在这里供参考。

### D4 · `skill_first.rs` 按模型名门控，垂直发行版反而用不上

`SkillFirstHook` 只对 `deepseek` / `qwen` 生效（`model_needs_firm_execution`）。
它做的事（开局强制查一遍技能目录、匹配上就先 `use_skill`）对**垂直领域**是刚需 ——
我们装了 6 个投研技能，希望模型看到"龙虎榜""杀猪盘"这类词就先加载对应技能。
但我们的模型名是 `hunter-chat`，门控判定不命中。

**问题**：能不能把它变成一个配置开关（默认维持现在的按模型名判定，显式开启时强制生效）？

### D5 · `ProviderConfig.system_prompt` / `ModelConfig.system_prompt` 是死字段（补 B7）

M2 把整条链路读了一遍，确认这个字段**全仓没有任何消费者**：

* `atomcode-config/src/config/provider.rs:11 / 146 / 200` 三处结构体都有这个字段；
* `config/mod.rs:1063 / 1528`、`provider.rs:245` 只是在结构体之间 `clone()` 来 `clone()` 去；
* 唯一按名字对得上的 `--system-prompt` / `--system-prompt-file` 在
  `atomcode-clix`（独立的代码评审 CLI），走的是那个 CLI 自己的 reviewer persona
  （`main.rs:370` 写进 `cfg.persona`），与主 agent 的 `coding_persona*` 无关；
* `parts.rs:1674` 与 `assemble.rs:110` 直接把 `coding_persona_with_capabilities(...)`
  的返回值塞进 `Agent::builder().persona(...)`，没有任何分支去看配置。

也就是说：**用户在 config.toml 里填了 `system_prompt`，不会报错，也不会生效。**
这比"没有这个功能"更糟 —— 它看起来像有。

**问题**：接上它（未配置时行为完全不变）能不能接受？我们准备按这个思路提 PR。

### B10 · 官方二进制的构建环境（glibc 基线）没有公开说明

**实测**：`v5.1.0` 的 linux-x64 官方二进制（npm `@atomgit.com/atomcode@5.1.0-linux-x64`）
最高只需要 `GLIBC_2.17`：

```
$ strings -a atomcode | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1
GLIBC_2.17
```

而仓库里的 `.github/workflows/build.yml` 用的是 `ubuntu-latest`（24.04，glibc 2.39），
照它编出来的产物需要 `GLIBC_2.39`，装进任何 Debian 12 / CentOS 系的运行镜像都会
`version 'GLIBC_2.39' not found` 起不来 —— 我们自己编 fork 二进制时实测踩到。

**问题**：

1. 官方发行的二进制是不是用另一套（manylinux2014 / 旧 sysroot / zig cc 之类）构建的？
   仓库里的 `build.yml` 与实际发行产物看起来不是同一条流水线。
2. 有没有打算公开可复现的构建说明？对做垂直领域发行版的人来说，
   「自编二进制的兼容面和官方一致」是能不能替换官方产物的前提。
3. 如果暂时没有，建议在 `build.yml` 或 README 里注明官方产物的 glibc 基线，
   免得下游照着 `build.yml` 编出一个兼容面窄得多的产物却不自知。

**我们的做法**：不追 2.17，改在 `rust:1-bookworm` 容器里编，对齐自己运行镜像的
glibc 2.36。见 `docs/fork-patches.md` §4。

### B11 · 工具返回的截断阈值写死在源码里，没有任何配置入口

`crates/atomcode-capabilities/src/tools/output_artifact.rs`：

```rust
pub const THRESHOLD_BYTES: usize = 16 * 1024;
const PREVIEW_HALF: usize = 4 * 1024;
```

超过 16 KB 的工具返回被替换成「头 4 KB + 尾 4 KB + 一行 `[atomcode: output
truncated — … fetch_output(artifact_id=…)]`」，中间整段对模型不可见。设计上
很合理（内容寻址、去重、`fetch_output` 可分页取回），问题只在**两个常量都是
`const`，全树没有环境变量也没有配置项能改**（`grep -rn "ATOMCODE_ARTIFACT\|
ATOMCODE_TOOL_OUTPUT\|ATOMCODE_TRUNCAT"` 零命中）。

对编码场景 16 KB 基本够用；对**数据密集型场景**（我们这个投研发行版：财务报表、
全市场筛选、研报列表，一次 MCP 返回动辄 30–40 KB）截断是常态而不是例外。

**实测**（AtomCode 5.1.0，官方二进制 sha256 `40d86fa3…`）：一次「持仓论点复核」
的对话里 5 次 `akshare_call` 有 4 次被截断（全长 16 651 / 30 691 / 35 020 /
40 030 字节）。模型没有调 `fetch_output`，而是在正文里写出了三个**本次任何工具返回里都
搜不到**的数字（两个分红率、一个长协基准价），并把它们标成了工具来源；
另有一句话把三份不同返回里的字段拼在了一起（值取自 A 返回的「扣非净利润」，
标题写成「归母净利润」，来源标成 B 返回，同比增速取自 C 返回）。

想问的是：

1. 有没有我们没找到的配置入口？
2. 如果没有，是否愿意接受一个「阈值可配置」的补丁（默认值不变，未配置时行为
   与现在逐字节一致）？我们可以按贵方偏好的形式提（环境变量 / `config.toml`
   字段 / 两者都要）。
3. 另外一个更轻的方向：截断提示里能不能带上「这是第几段 / 共几段」的结构信息，
   让模型更容易意识到自己缺了中间那段？目前的提示只说了总字节数。

（我们这边不改内核也能缓解：把 MCP 侧的返回压到 16 KB 以内。这一条是想确认
上游的意向，不是阻塞项。）

### B12 · `GET /live?session_id=` 被"孤儿 runtime"占住之后无法恢复（M4 实测）

**版本**：5.1.0（官方二进制，sha256 `40d86fa3…`）

**现象**：`GET /live?session_id=<已存在的会话>` 返回 **HTTP 404**，body 是
`{"error":"cannot replace an active live runtime"}`。此后**任何**带 `session_id`
的 `/live` 都是 404，而 `GET /health`、`GET /mcp/status`、
`GET /projects/{hash}/sessions/{id}` 全部正常（200）。

**来源**：`crates/atomcode-daemon/src/native_live.rs:394-401` ——
换绑时若当前 runtime 的 `phase` 属于 `InTurn | WaitingApproval | Reconfiguring`，
直接把 owner 放回去并返回该错误。

**最小复现**（一台跑着 daemon 的机器上）：

```bash
# ① 开一条不带 session_id 的 /live 然后直接断开（模拟一个没善后的消费者）
curl -sN -H "Authorization: Bearer $T" http://127.0.0.1:13456/live & sleep 3; kill %1
# ② 换绑任何一个已存在的会话
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $T" \
     "http://127.0.0.1:13456/live?session_id=$SID"      # → 404
```

**为什么对使用方是个坑**：

1. **诊断面看不出来**。`/health` ok、`/mcp/status` 9/9 connected、会话也查得到，
   只有发消息那一刻报错，运维很难把它和"上一个消费者没退干净"联系起来。
2. **没有明确的释放手段**。`POST /live/stop` 在没有进行中的回合时返回
   `{"accepted":false}`；文档里也没有"放弃当前 runtime"的端点。
3. 相关的第二个效果更贵：当 `can_reuse` 成立时（`working_dir` 相同且未指定
   `session_id`），新消费者会**复用**上一个 runtime。我们实测到复用到一个
   **没有挂载 MCP 工具**的 runtime 之后，模型退化成用 `bash`/`glob`/`read_file`
   在工作区里翻找，一个问题跑了 30 次工具调用、`stop_reason=max_rounds`、
   网关配额差值 **982 548 token**。

**想请教 / 建议**：

* 错误里能否带上当前 runtime 的 `session_id` 与 `phase`？调用方就能判断"要不要等"。
* 有没有（或能否加）一个显式释放端点，例如 `DELETE /live` / `POST /live/release`，
  在没有活跃订阅者时强制收掉当前 runtime？
* `GET /live` 的响应里能否给出**这个 runtime 当前可见的工具数**（或至少
  `mcp_tool_count`）？这样调用方能在发消息前发现"模型其实看不到 MCP 工具"，
  而不是烧掉几十万 token 之后才从对话内容里看出来。

**我们当前的绕法**（不改内核）：转发层识别这个 404 —— 若本进程没有进行中的回合，
就判定为孤儿，`POST /live/stop` 后退避重试最多 4 次；否则如实告诉用户"另一个会话
正在生成"。

---

## E. M5 新增（2026-09-22）

### E1 · `latest.json` 在 release tag 上写的是**上一版**的校验值

做上游差异工具（`tools/upstream_diff.sh`）时发现的，**直接影响任何按 tag 锁版本的人**。

仓库根的 `latest.json` 是"最新版二进制的 sha256 / size"清单，也是我们 `pins.lock`
的主校验来源。但它的同步 commit **落在打 tag 之后**，所以：

```bash
# 在 atomcode 仓库里
git show v5.1.0:latest.json | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])'
# → v5.0.9      ← tag 是 v5.1.0，文件里写的却是 v5.0.9

git show v5.0.9:latest.json | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])'
# → v5.0.8      ← 同样差一版
```

对应的同步 commit 分别是 `254d14a84`（v5.1.0）与 `287bff70f`（v5.0.9），
提交信息都是 `chore: sync latest.json from release vX.Y.Z`。

**后果**：按"checkout tag vX → 读 latest.json → 校验二进制"这条看起来最自然的路径锁版本，
**锁到的是上一版的 sha256**，校验必然不过；如果实现得宽松一点（校验不过就跳过），
那就悄悄装了一个没校验过的二进制。我们的 `upstream_diff.sh` 因此改成
**按文件内容回溯同步 commit**，而不是读 tag 上的文件。

**想请教 / 建议**：

* 能否在打 tag 之前把 `latest.json` 同步进去（或者把 tag 移到同步 commit 上）？
* 或者在 `latest.json` 里加一个"这份清单对应哪个 commit"的字段，让使用方能自检？
* 顺带确认：`latest.json` 是不是**推荐**的版本锁定入口（补 B6 的提问）？
  如果推荐 release 附件的 checksum 文件，请在 README 里指明 —— 我们在美国机房
  访问 atomgit 的 release 直链会拿到 418，只能走 npm registry（`@atomgit.com/atomcode`），
  实测 npm 包里的二进制与 `latest.json` 记录的是同一文件（sha256 与 size 逐字节一致）。

### E2 · `GET /skills` 只回 `{name, description}`，自定义 frontmatter 字段全丢

`crates/atomcode-capabilities/src/skills/skill.rs` 解析 SKILL.md 的 frontmatter 时，
只保留结构体里声明的字段；`GET /skills` 又只投影其中两个。

我们的技能 frontmatter 里有一整块 `hunter:` 扩展（`display_name` / `icon` /
`category` / `prompt_tpl` / `needs_tools`），是给前端画技能卡片用的。
走 daemon 取不到，只能由 api 那侧另外提供一份 —— 同一份 SKILL.md 要被读两次，
而且两处随时可能不一致。

**最小复现**：

```bash
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:13456/skills | head -c 300
# → [{"name":"deep_analysis","description":"..."}, ...]   没有任何自定义字段
```

**想请教 / 建议**：

* `GET /skills` 能否把 frontmatter 里**未识别的顶层键**原样透出（例如放在 `extra` 里）？
  这样第三方发行版就能把自己的前端元数据放在 SKILL.md 里，保持单一事实来源。
  （核对过：`struct Frontmatter` 目前只有 `name` / `description` / `allowed_tools` /
  `user_invocable` 四个字段，未识别的键在解析阶段就丢了，不是只在投影时丢的；
  `GET /skills` 的 `SkillInfo` 更只投影 `name` + `description` 两个 ——
  `crates/atomcode-daemon/src/lib.rs:6016-6022`。所以这个改动要动两处。）

### E3 · 免费公共算力能否面向金融领域用户开放（计划 v0.2 §10 第 6 问）

这条不是缺陷，是商务/策略上的确认，之前几轮一直没落到文档里，定稿补上。

AtomCode 提供的免费公共算力目前的可用范围、限额与合规边界是什么？
面向**金融投研类**的垂直发行版（像本项目这样把 AtomCode 当引擎、
挂自己的 MCP 与技能）能否使用？如果不能，是否有面向垂直发行版的合作路径？

### E4 · 被 `PreToolUse` 拒绝的调用，`PostToolUseFailure` 不带 `tool_name`，审计里记不下是哪个工具

M5 回归时从真实部署的审计日志里翻出来的（不是构造的）。我们的 `PreToolUse` hook
拒绝了一条内联爬虫命令之后，`PostToolUseFailure` 照常触发、`tool_response` 里
原样带着我们的拒绝说明，**但 `tool_name` 是缺的**：

```json
{"ts":"2026-09-22T11:06:27.707+00:00","session_id":"9777d40b-…",
 "event":"PostToolUseFailure","ok":false,"tool":null,"cwd":"/workspace",
 "response_len":85,
 "response_head":"blocked: 内联脚本在自己发 HTTP 请求。…请调工具，不要写爬虫 …"}
```

（`tool` 取自事件的 `tool_name`；同一份 hook 在**成功**的 `PostToolUse` 上
每次都能拿到 `tool_name`，所以不是我们解析错了。上下文：`cc_hooks.rs:897-903`
的 payload 只有 `session_id` / `hook_event_name` / `tool_name` / `tool_response` /
`cwd`，两个事件都**不带 call_id**，没有任何字段能把 pre 和 post 配起来。）

**后果**：对以「可审计」为卖点的发行版，"哪个工具被拦了"恰恰是最该留痕的一条。
我们能绕（`PreToolUse` 那一侧自己落一份 `guard.jsonl`，里面有工具名），
但审计与拦截日志因此是两个文件、要靠时间戳对，而**时间戳配不出唯一解**。

**想请教 / 建议**：

* `PostToolUseFailure` 能否在被 hook 拒绝这条路径上也带上 `tool_name`？
* 更根本的：两个事件能否都带一个 **call_id**（哪怕只是进程内自增）？
  有了它，"参数 / 耗时 / 结果"就能由 hook 侧自行配对，
  不必像现在这样由 `PreToolUse` 侧先落一份文件再去猜。

---

## F. 与计划 v0.2 §10 六个问题的对应关系（M5 定稿）

计划里列了六个"待向 AtomGit 团队确认"的问题。逐条对到本文件的哪几条，
以及**我们自己已经实测出的答案**（很多问题在做的过程中已经有了确定结论，
提给上游是为了确认意图，不是还在等答案）：

| 计划 §10 | 本文件 | 我们实测到的答案 |
|---|---|---|
| 1 · 人设能否整体替换；`system_prompt` 是否遗留未接线 | **B7 + D5** | **确认是死字段**：三个结构体都有 `system_prompt`，`Config::resolve_model` 一路解析下来，**全仓没有任何地方读它去拼 prompt**。M2 写了最小补丁让它生效（`docs/fork-patch/apply.py`），因 A/B 达标（89.6% ≥ 80%）**没有启用**。M5 用 `upstream_diff.sh` 验证该补丁对 v5.0.9 与 v5.1.0 都还能干净打上 |
| 2 · `plan` 档是否禁止 bash / MCP | **B4 + D2 + 待办池 P1-1** | **不禁止**。`plan` 只拦 write_file / edit_file / search_replace / parallel_edit_files，**工作区内 bash 照跑**（`echo x > file` 能落盘）。而且 `PlanModeReminderHook` 每轮注入「出完方案就停下等用户 review」，对非编码场景是硬伤。**结论：投研只读会话不能用 `plan` 档**，我们改成 `build` 档 + guard hook |
| 3 · 技能 / hook / MCP 配置在 5.x 内是否承诺向后兼容；版本锁定看什么 | **B6 + E1** | 5.0.8 → 5.0.9 → 5.1.0 三版实测，**skill 解析与 hook 事件格式一处没变**，MCP 配置只多了一个 `McpConfigSource::Driver` 枚举值，daemon 路由是净增（见 `docs/upstream-diff/`）。所以经验上是兼容的，但**上游没有书面承诺**，这正是要问的。版本锁定我们锁 `latest.json` 的 sha256 + size，踩到了 E1 那个坑 |
| 4 · `docs/hooks.md` / `webhook-guide.md` 是否会更新；webhook 会不会回归 | **A6** | 两份文档与实现都不符（hook 实际只有 **8 个事件**、配置文件是 `.hooks.json`、**只支持 shell**）。webhook 在新引擎里没有对应实现。我们一律以源码为准 |
| 5 · 官网"定制我的领域"指什么机制；垂直发行版能否进官方目录 | **B8** | 没找到对应的代码机制。我们的做法是四个外部面（daemon API / MCP / hook / skill）全用上，**不改内核** —— 这套做法能不能算"定制领域"、能否进官方目录，要上游答 |
| 6 · 免费公共算力能否面向金融领域用户开放 | **E3** | — |

## G. 提 issue 的执行记录（M5）

按总控规则已拍板决策 8 与红线 7，**只提两个** issue，链接见 `docs/开发文档/M5-成果与测试报告.md`：

1. **B1 单独一条** —— `/chat` 不挂载 MCP 工具而 `/live` 挂载。挑出来单独提的理由：
   它对"用 daemon HTTP API 接第三方前端"的人影响最大，而且从 API 文档上完全看不出来，
   踩到的人会先怀疑自己的 MCP 配置错了（我们 M0 就是这么走了弯路的）。
2. **汇总条目** —— 其余条目按 A/B/C/D/E 分组，每条一行摘要 + 本文件的锚点链接。

措辞要求（总控规则）：客观、中文、不催促、不带商业诉求。
