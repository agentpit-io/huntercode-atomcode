# 待向 AtomGit / AtomCode 团队确认的问题

> 来源：HunterCode·AtomCode 发行版（HCA）M0 预研，基于 **v5.1.0** 官方二进制与
> 上游源码 e4215f7 / 254d14a84 的逐条实测。每条都给出复现命令。
> 本文件只记录**以源码实测为准、与上游文档不符**或**语义不明**的点，不含我们自己的设计问题。

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

---

## C. 一个可能的小缺陷

### C1 · `read_file` 的参数名报错信息很好，但 `/chat` 的工具 schema 里字段名不统一

模型一次用 `{"path": "..."}` 调 `read_file`，返回：

```
read_file: invalid arguments: missing field `file_path` at line 1 column 26. Expected {"file_path": "<path>"}.
```

报错很清楚（点赞）。只是想确认：`read_file` / `write_file` / `edit_file` 一律是 `file_path`，
`search_replace` 是 `path`，这个不一致是有意的吗？
