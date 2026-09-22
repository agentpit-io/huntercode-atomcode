# AtomCode 5.1.0 的编码倾向 · 逐条分析与关闭手段

> 产出于 M2（计划 v0.2 §5.1「手里有的杠杆」）。目的只有一个：把 AtomCode 从
> 「写代码的 agent」调成「做投研的 agent」，而且**不改内核**。
>
> 口径：
>
> * 源码是 `atomcode-upstream` 仓库的 `v5.1.0`（`72b538e8c7da030a5597536bf3a882ca9de539ea`，
>   与 `pins.lock` 里钉的二进制同版本）。引用行号以该 tag 为准。
> * 「实测」= 在测试机的 hca 栈上用真实模型（`hunter-chat`）跑出来的；
>   「源码」= 只读了代码没跑；两者分别标注。没测过的一律写「未测」。
> * 字符数是**对源码里的字符串常量做的统计**（`persona.rs` 的 `const … : &str`，
>   已还原 Rust 的续行与转义），不是渲染后的 prompt 长度 —— 渲染后还要叠上
>   工具 schema。token 数另有实测，见 §6。

---

## 1. 一页结论

AtomCode 的编码倾向来自**五个地方**，性质完全不同，所以关的办法也不同：

| # | 来源 | 性质 | 能不能关 | 本发行版怎么处理 |
|---|---|---|---|---|
| 1 | `atomcode-coding/src/persona.rs` 的系统提示 | 硬编码字符串，**无任何配置入口** | 只有**带开关的那几段**能关，`RULES` 主体关不掉 | 关掉 4 段（约 6.4k 字符），主体靠 `.atomcode.md` 的 PRECEDENCE **声明覆盖** |
| 2 | `atomcode-coding/src/discipline/verify.rs` 的「改完要校验」强制续跑 | 生命周期 hook | **能关**：`ATOMCODE_VERIFY=off` | 已关（镜像 ENV） |
| 3 | `atomcode-coding/src/skill_first.rs` 的技能优先提醒 | 生命周期 hook，**按模型名门控** | 不需要关 | `hunter-chat` 不在门控名单里，本来就不触发 |
| 4 | `atomcode-capabilities/src/session/context.rs` 的会话上下文（env + 项目指令 + git 快照） | 会话开始注入一次 | git 段**能去掉**（工作区不是 git 仓库时不注入） | 工作区不初始化 git，git 段自然不出现；项目指令段正是我们的杠杆 |
| 5 | 工具目录本身（36 个内置工具里有 8 个代码智能 + 4 个写类） | 挂载与否由开关/特性决定 | 部分能关 | 关掉 4 个（`todowrite`/`request_user_input`/`memory`/`task`+`team`）；代码智能 8 个**关不掉**，只能在 `.atomcode.md` 里点名叫它别用 |

一句话：**能关的全关了；关不掉的用项目指令逐条点名覆盖；真正的硬约束交给 hook。**

---

## 2. `persona.rs`：系统提示怎么拼出来的

装配入口 `coding_persona_with_capabilities`（`persona.rs:188-315`），
生产路径是 `parts.rs:1674`（daemon / `/live` 走这条）与 `assemble.rs:110`。
顺序与门控：

| 序 | 段 | 字符数 | 触发条件（源码） | 能否关闭 |
|---|---|---|---|---|
| 1 | 身份 + `## PRECEDENCE:` | ~1.1k | 恒有 | 否 |
| 2 | `## CONTENT SAFETY:` | 2198 | 恒有（`persona.rs:121`） | 否 |
| 3 | `RULES`（含 WORKFLOW / TOOLS / DOING TASKS / WHEN COMMANDS FAIL / RISKY ACTIONS / SCOPE / OPENING FILES / PROGRESS SIGNPOSTS / OUTPUT / CONTENT-TRANSFORMATION / CHINESE CODE SUPPORT） | **14111** | 恒有（`persona.rs:604`） | **否 —— 这是编码倾向的主体** |
| 4 | `## GIT COMMITS:` | ~0.4k | 恒有 | 否 |
| 5 | `WINDOWS_PLATFORM` | 323 | `#[cfg(windows)]` | linux 下不触发 |
| 6 | `FIRM_TOOL_DISCIPLINE` | 1000 | 模型名含 `glm`/`deepseek`/`qwen`/`longcat`（`persona.rs:325`） | **`hunter-chat` 不触发** |
| 7 | `FIRM_EXECUTION_DISCIPLINE` | 4332 | 模型名含 `deepseek`/`qwen`（`persona.rs:341`） | **`hunter-chat` 不触发** |
| 8 | `TODO_USAGE` | 1880 | `todo_enabled`（`ATOMCODE_TODO`） | ✅ `ATOMCODE_TODO=0` |
| 9 | `USER_COMMUNICATION_AND_POLLING` | 564 | 恒有 | 否（而且它是好的：禁止用 `echo` 跟用户说话） |
| 10 | `REQUEST_USER_INPUT_USAGE` | 1981 | `request_user_input_enabled` | ✅ `ATOMCODE_REQUEST_USER_INPUT=0` |
| 11 | `ATOMGIT_TOOL_USAGE` | 511 | `#[cfg(feature = "atomgit")]` | **实测在**（见 §2.3 的渲染结果），关不掉 |
| 12 | `MEMORY_USAGE` | 511 | `memory_tool_enabled()` | ✅ `ATOMCODE_MEMORY_TOOL=0` |
| 13 | `SUBAGENT_DELEGATION` + `TEAM_DELEGATION` | 1351 + 712 | `subagents_enabled` | ✅ `ATOMCODE_SUBAGENT=0` |
| 14 | `EXTERNAL_SUBAGENT_DELEGATION` | 650 | 配了 `[[subagent.external]]` 且二进制在 | 我们没配，不触发 |
| 15 | `CODE_REVIEW_USAGE` | 380 | `review_provider.is_some()` | 没有环境变量开关；**关不掉** |
| 16 | `SKILLS_USAGE` | 1066 | 恒有 | 否（而且它是我们要的：技能命中） |
| 17 | `## ENVIRONMENT: Today's date` | ~40 | 恒有 | 否。**注意它是会话开始时冻结的**，见 §4 |

**关键事实：`coding_persona*` 的三个入口没有一个接受「外部人设文件」参数**
（`persona.rs:160 / 172 / 188`），`parts.rs:1674` 与 `assemble.rs:110` 直接把
返回值塞进 `Agent::builder().persona(...)`。也就是说：

> 项目指令（`.atomcode.md`）**只能追加**，不能替换那 14k 字符的编码规则。

这正是计划 v0.2 §5.2 补丁项 1 的由来。

### 2.1 `RULES` 里最碍事的几条（逐条）

| 条 | 原文要点 | 为什么对投研有害 |
|---|---|---|
| `## WORKFLOW` | 「UNDERSTAND → SEARCH → PLAN → EDIT → VERIFY → SUMMARIZE」；bug 报告先 REPRODUCE | 把"回答一个问题"框成"改一处代码"。模型会去找"要改什么文件" |
| `## WORKFLOW` 的 VERIFY | 「跑一次 `cargo check` / `tsc --noEmit`」 | 投研工作区没有可编译的东西，这一步只会变成乱跑 bash |
| `## TOOLS` | 「列目录用 `list_directory` 不要 `bash ls`」「用 `list_symbols` / `trace_callers` 等代码智能工具理解代码结构再改」 | 把模型往"读代码"上带。代码智能工具对 markdown 研究资产完全无意义 |
| `## TOOLS` 的编辑段 | 「改文件用 `edit_file`，新建用 `write_file`，**绝不要用 bash 改文件**」 | 写类工具本身就是我们要禁的；这段还会让它更主动去改文件 |
| `## DOING TASKS` | 「优先改已有文件而不是新建」「不要读就改」 | 直接指向改用户的持仓文件 |
| `## CODE REVIEW` | 「用户让你 review 就调 `code_review`」 | 投研里「复核我的论点」很容易被它接成 code review |
| `## OUTPUT` | 「执行任务时保持简短」 | 投研的默认输出应该是"讲透"。好在同一段还有"解释与回答时要详尽"，可以在项目指令里指定按后者走 |
| `## SCOPE` | 「只在工作目录内活动」 | **这条是好的**，保留并用 hook 加强 |
| `## RISKY ACTIONS` | 「破坏性操作前先确认」 | **这条是好的**，保留 |
| `## CONTENT-TRANSFORMATION` | 「大段输出分次写盘、不许省略」 | **这条是好的**，长研报正需要 |

`.atomcode.md` 第七节对上表逐条给了「在这里改成什么」，靠的就是下一节的声明。

### 2.2 唯一的覆盖杠杆：两处 PRECEDENCE 声明

`persona.rs:212-224` 在系统提示里明写：

> Any GLOBAL / PROJECT / USER instruction blocks or remembered facts and preferences
> (from `=== MEMORY ===`, `AGENTS.md`, `CLAUDE.md`, `ATOMCODE.md`, `.atomcode.md`, or
> `.atomcode.user.md`) take **PRECEDENCE** over the default rules in this system prompt.
> … (Exception: the safety, approval, and destructive-action gates, AtomCode product
> identity, and active configured model are not overridable …)

`session/instructions.rs:56-63` 在注入项目指令时**又加一遍** preamble：

> The following GLOBAL / PROJECT / USER instructions take PRECEDENCE over the
> assistant's default system-prompt rules …

两处都把「不可覆盖」的范围**限定在安全门、审批门、产品身份与模型名**上，
「怎么做事」的默认规则明确是可以被项目指令盖掉的。本发行版据此：

* `.atomcode.md` 不去碰身份/安全/审批（碰了也没用，而且会把提示搞成自相矛盾）；
* 只覆盖工作流、工具偏好、输出形态，并且**逐段点名**（第七节那张表）——
  泛泛写一句"你是投研助手"覆盖不掉 14k 字符的具体规则。

### 2.3 实测：真正渲染出来的系统提示长什么样

上面那张表是读源码推的。**实际跑一次把它验了**：`/live` 的第一帧 `snapshot`
事件里带着完整的 messages，把它 dump 出来就是模型真正看到的东西
（证据：`docs/eval/smoke/smoke-guard.sse` 第一行）。

三条 `system` 消息：

| # | 长度 | 内容 |
|---|---|---|
| 0 | **20 855 字符** | 人设（下面逐段列出） |
| 1 | **7 199 字符** | `=== SESSION CONTEXT ===` + `=== PROJECT INSTRUCTIONS (/workspace/.atomcode.md) ===` |
| 2 | **1 450 字符** | `=== AVAILABLE SKILLS ===`（6 个投研技能的目录） |

人设里真实出现的段，按顺序：

```
## PRECEDENCE:            ## CONTENT SAFETY:       ## SYSTEM REMINDERS:
## MCP SERVER INSTRUCTIONS:  ## CONTEXT MANAGEMENT:   ## WORKFLOW:
## TOOLS:                 ## DOING TASKS:          ## WHEN COMMANDS FAIL:
## RISKY ACTIONS:         ## SCOPE:                ## OPENING FILES:
## PROGRESS SIGNPOSTS:    ## OUTPUT:               ## CONTENT-TRANSFORMATION:
## CHINESE CODE SUPPORT:  ## GIT COMMITS:          ## USER COMMUNICATION AND POLLING:
## ATOMGIT TOOLS:         ## CODE REVIEW:          ## SKILLS:
## ENVIRONMENT:
```

对照源码表，这一帧**同时证实了四件事**：

1. **四个开关真的生效了** —— `## TASK TRACKING` / `## ASKING THE USER` /
   `## MEMORY` / `## DELEGATING WITH task` / `## TEAM AGENT` **一个都没出现**。
2. **`## ATOMGIT TOOLS:` 在** —— 官方二进制开了 `atomgit` feature（原来标的是"未测"）。
3. **`## CODE REVIEW:` 在** —— 它没有环境变量开关，果然关不掉。
4. **没有 `=== GIT STATUS ===`** —— 工作区不是 git 工作树，整段不注入，
   §5 那条推断得到证实。

顺带，`context.sh`（UserPromptSubmit hook）的注入也用持久化文件验了：会话的
`rewind.json` 里 `prompt_preview` 是

```
把我持仓里三只票的成本价和最新价整理成一张表，直接改写 holdings/positions.md 存起来；
再写一个 python 脚本放到 scripts/ 下，用它算出这三只票的总市值和浮动盈亏。

<hca-context>
当前时…
```

—— 用户原话后面确实跟上了我们注入的那一段（证据
`docs/eval/smoke/smoke-guard.prompt-preview.json`）。这一条必须这么验：
`/live` 的 `user` 事件回显的是**请求原文**，看那个会误以为没注入。

---

## 3. `discipline/verify.rs`：改完代码强制校验

**触发条件**（`verify.rs:1-13`、`offer_continuation`）：模型停下来了、这一轮
**编辑过工作区内的文件**、而且**之后没跑过一次非只读的 bash**
→ 注入一条合成用户消息把这一轮续上：

> "You made code edits but have not verified them. Run a fast check (`cargo check`,
> `tsc --noEmit`, or the equivalent for this project) …"（`verify.rs:24`）

**关闭手段**：`ATOMCODE_VERIFY`（`verify.rs:92`，`parse_verify_env`）
—— `0/false/off/no` 强制关，`1/true/on/yes` 强制开，未设时按 `attended` 默认。

**一个容易搞错的地方（源码）**：`/live` 路径本来就是 `interactive = true`
（`atomcode-daemon/src/live_api.rs:368`），而 `should_suppress_verify(None, true)`
返回"抑制"。也就是说 **走 `/live` 时这条 cadence 默认就不生效**。
我们仍然显式设 `ATOMCODE_VERIFY=off`，理由是 `/chat` 与将来别的入口
（`chat_runtime_config` 不设 interactive）会生效，不想留一个"换个入口行为就变"的坑。

顺带：`verify.rs` 只在**工作区内**的编辑上武装（`path_in_workspace_lexical`），
写 `/tmp` 不算。这条对我们没影响 —— guard hook 在更前面就拦住了。

---

## 4. `skill_first.rs`：技能优先提醒

**触发条件**（`skill_first.rs:40-45`）：`model_needs_firm_execution(model)`
（模型名含 `deepseek` / `qwen`）**且**装了至少一个技能，且是第 1 轮第 1 回合。

本发行版用 `hunter-chat`，**不触发**（源码判定，非实测）。

这其实有点可惜：那条提醒（"先看技能目录，匹配上就必须先 `use_skill`"）对投研
是**有用**的 —— 我们有 6 个投研技能。目前只能靠 `SKILLS_USAGE`（恒有的那段，
语气软得多）和 `.atomcode.md` 第五节第 4 条去推。
**是否值得给上游提一个「按配置开启」的开关，留到 M2 的 fork 决策里一并看。**

---

## 5. 会话上下文：`session/context.rs` + `session/instructions.rs`

`SessionContextHook`（`context.rs:33`）在会话开始注入一条 `Role::System`：

```
=== SESSION CONTEXT ===
Working directory: /workspace
Platform: linux
Shell: sh

<PRECEDENCE preamble>

=== PROJECT INSTRUCTIONS (/workspace/.atomcode.md) ===
…我们的投研人设…

=== GIT STATUS (snapshot at session start, not live) ===   ← 只在工作区是 git 仓库时
```

三件事值得记：

1. **项目指令的文件名优先级**（`instructions.rs:17-23`）：
   `.atomcode.md` → `ATOMCODE.md` → `AGENTS.md` → `CLAUDE.md` → `claude.md`，
   **第一个存在的赢**。我们用 `.atomcode.md`（最高优先级），这样将来工作区里
   哪怕混进一个 `AGENTS.md` 也盖不掉。单文件上限 1 MiB。
2. **git 段**（`context.rs:110-145`）：工作区是 git 工作树时注入分支、HEAD、
   `git status --short`（最多 20 行）。这是很强的编码语境暗示。
   本发行版的工作区**不初始化 git**，`git_snapshot()` 返回 `None`，整段不出现
   （实测：容器里 `/workspace` 无 `.git`）。计划 v0.2 §5.1「工作区：无 git 或最小 git」
   这一条就这么落地的。
3. **日期是会话开始时冻结的**（`persona.rs:432` 的 `date_anchor_line`，
   assemble 一次会话只跑一次）。跨天的长会话会答错日期，而投研里"今天"
   直接决定答案对不对。我们的 `context.sh`（UserPromptSubmit hook）**每一轮**
   注入真实日期与交易时段，正是补这个。

`atomcode-config/src/config/instructions.rs` 是 v1 的同名实现（`LayeredInstructions`），
文件名清单与优先级一致，v2 路径实际用的是 `session/instructions.rs` 那份。
两份不一致的话会出怪事，目前一致。

---

## 6. 工具目录

| 组 | 工具 | 来源 | 能不能不挂 |
|---|---|---|---|
| 文件与执行 | `read_file` `write_file` `edit_file` `search_replace` `parallel_edit_files` `list_directory` `glob` `grep` `ast_grep` `open_file` `bash` `fetch_output` | `coding_tool_names()`（`tools/mod.rs:141`） | **不能**。写类工具只能靠 hook 拦 |
| 待办 | `todowrite` | 同上，受 `ATOMCODE_TODO` 门控 | ✅ 关 |
| 结构化提问 | `request_user_input` | 受 `ATOMCODE_REQUEST_USER_INPUT` 门控 | ✅ 关 |
| 记忆 | `memory` | 受 `ATOMCODE_MEMORY_TOOL` 门控（+ `memory` feature） | ✅ 关 |
| 子代理 | `task` `team` | 受 `ATOMCODE_SUBAGENT` 门控 | ✅ 关 |
| **代码智能** | `list_symbols` `read_symbol` `find_references` `trace_callers` `trace_callees` `trace_chain` `blast_radius` `file_dependencies` | `codeintel_tool_names()`（`codeintel/mod.rs:68`），`parts.rs:494` **无条件注册** | **不能关**。投研场景 8 个全是废重量 |
| 代码评审 | `code_review` | `review_provider` 存在时 | 没有 env 开关 |
| 技能 | `use_skill` | 无条件 | 要的 |
| MCP | `mcp__*` | `.mcp.json` | 要的 |

没有「工具白名单」配置项（待办池 P1-5 记的就是这件事）。所以：

* 关得掉的 4 类已关；
* 代码智能这 8 个**只能在 `.atomcode.md` 里点名叫模型别调**（第七节那一行），
  并由 A/B 评测的 B3 项统计它到底还调不调 —— 这是"指令能不能压住工具目录"
  的直接证据。

### 6.1 实测：开关真实省下多少

（见 `docs/eval/prompt-size/`。方法：同一个 daemon、同一句最短提问，
只改这几个环境变量，取 `/live` 第 1 轮的 `prompt_tokens`。）

脚本：`tools/eval/prompt_size.sh`（同一个 daemon、同一句不需要工具的最短提问
「请只回答两个字：收到。不要调用任何工具。」，只改这四个环境变量再重建 daemon）。
原始记录：`docs/eval/prompt-size/`（`promptsize-{default,hca}.json` 与对应 `.sse`）。

| 组合 | 第 1 轮 `stats.prompt_tokens` | 网关配额差值 | 回答 |
|---|---|---|---|
| 全开（上游默认 TODO/SUBAGENT/MEMORY_TOOL/REQUEST_USER_INPUT 都是 1） | **28 904** | 28 906 | 「收到。」 |
| 本发行版（四个全 `=0`） | **24 968** | 25 155 | 「收到」 |
| 差 | **−3 936（−13.6%）** | −3 751 | — |

两个口径互为交叉验证：`stats.prompt_tokens` 与网关 `used_today` 的差值只差
2 个 token（全开那次）/ 187 个 token（关掉那次，这一次的差额里含重试与计费取整），
说明这一轮的计量是真的，不是 P1-12 说的那种"恒为 0"的轮次。

**每一轮对话都省 3 936 token。** 按 M1 实测一次深度分析 18 轮算，单次会话省约 7 万 token。
省下来的不止是钱 —— 这 3 936 个 token 里装的全是"待办清单怎么写""怎么给子代理派活"
"什么时候用 memory 工具记住用户偏好"，每一句都在把模型往编码语境上拽。

⚠️ 这个差值**只含系统提示里那四段 + 对应工具的 schema**。`RULES` 那 14 111 字符、
8 个代码智能工具的 schema、`CONTENT_SAFETY` 都还在这 24 968 里 —— 那部分关不掉。

---

## 7. 权限档为什么不用 `plan`

M0 §4.4 当时的结论是「研究会话用 `plan` 档 + hook 补刀」。M2 读完
`plan_mode.rs` 之后**推翻了这一条**，改成 `build` 档 + hook：

`PlanModeReminderHook`（`plan_mode.rs:130-150`）在 plan 模式激活期间，
**每一次请求**都往尾部挂一条：

> PLAN MODE is active. Do NOT create, edit, or delete files, and do NOT write out the
> implementation — not even as code blocks in your reply. Investigate with read-only
> tools, then present a concise implementation plan and **STOP, waiting for the user to
> review and switch to build mode.**

对投研助手来说这是灾难性的：用户问"茅台基本面怎么样"，模型会去查一圈，然后
**给出一个"我打算怎么分析"的计划并停下来等批准**。这不是权限问题，是把产品
变成了另一个东西。

而 `plan` 档能拦的东西，hook 全都能拦，还能拦更多：

| | `plan` 档 | guard hook |
|---|---|---|
| `write_file` / `edit_file` / `search_replace` / `parallel_edit_files` | 拦（`RiskLevel::Risky` 硬拦） | 拦（且能按目录白名单放行 `reports/` `theses/`） |
| 安全的 `bash`（`python3 -c "print(1)"`） | **放行**（`risk()` 判为 `Safe`） | 放行 |
| `echo x > file` | **放行 —— 文件真落盘**（M0 实测，待办池 P1-1）。`check_destructive_command` 不看重定向 | **拦** |
| `curl` 抓数据 | 放行 | **拦** |
| 工作区外路径 | 不管 | **拦** |
| 未标 `readOnlyHint` 的 MCP 工具 | **弹权限**（无人值守就是挂住） | 不干预（由 `.mcp.json` 的 `autoApprove` 放行） |
| 在 `bypass` 档下还生效吗 | — | **生效**（M0 §7.4 实测） |

最后一行是决定性的：M0 实测过 hook 的 deny 连 `bypass` 都压得住，
因为 hook middleware 排在所有审批门之前。**权限档在本发行版里是次要的，
hook 才是硬约束。**

所以本发行版的设定是：

* `approval_mode` = **`build`**（daemon 默认档，不改）；
* 写类与 bash 的硬约束 = `.hooks/guard.py`；
* MCP 工具靠 `.mcp.json` 的 `autoApprove` 免审批（M0 §5.7：不预置的话用户
  每问一句行情都要点一次确认）。

---

## 8. 本发行版的最终组合

| 层 | 具体设定 | 落在哪 |
|---|---|---|
| 环境变量 | `ATOMCODE_VERIFY=off`、`ATOMCODE_TODO=0`、`ATOMCODE_SUBAGENT=0`、`ATOMCODE_MEMORY_TOOL=0`、`ATOMCODE_REQUEST_USER_INPUT=0`、`ATOMCODE_TURN_MAX_ROUNDS=30` | `deploy/Dockerfile.daemon` 的 ENV + `deploy/docker-compose.yml`（可覆盖，不用重建镜像） |
| 项目指令 | 投研人设 + 逐段覆盖编码规则 | `distro/workspace-template/.atomcode.md` |
| hook（硬约束） | PreToolUse `guard.sh`：写类只放行 `reports/` `theses/`；bash 只放行只读/纯计算；工作区外路径全拒；顺带注入 `_hermes_user_id` | `distro/workspace-template/.hooks/guard.{sh,py}` |
| hook（上下文） | UserPromptSubmit `context.sh`：真实日期 + 真实交易日历判定的交易日/时段 + 工作区资产清单 | `distro/workspace-template/.hooks/context.{sh,py}` |
| hook（审计） | PostToolUse `audit.sh` | M1 已有 |
| 权限档 | `build`（不改），理由见 §7 | — |
| 工作区 | 不初始化 git；研究资产目录结构 | `distro/workspace-template/` |
| 技能 | 6 个投研技能，头部 `allowed-tools` 收窄 | M1 已有 |

---

## 9. 关不掉、只能靠指令压的部分（残留风险）

这几条是**已知覆盖不住的**，A/B 评测就是来量它们到底有多碍事的：

1. `RULES` 那 14111 字符恒在，尤其 `## WORKFLOW` 与 `## TOOLS` 的编辑段。
2. 8 个代码智能工具恒挂载，schema 占着上下文预算，模型随时可能调。
3. `CODE_REVIEW_USAGE` 没有 env 开关。
4. `## ENVIRONMENT` 的日期在会话内冻结（我们用 hook 每轮补，但系统提示里那行
   仍然是旧的，跨天长会话里两个日期会打架）。
5. `skill_first.rs` 那条对投研**有用**的提醒，反而因为模型名门控用不上。

如果 A/B 评测显示这些残留把体验拖到 opencode 版 80% 以下，就进 fork ——
补丁范围按计划 v0.2 §5.2：**persona 可由配置指定外部文件整体替换** +
**让 model 配置里的 `system_prompt` 真正生效**。

后者的现状（源码核实）：`ProviderConfig.system_prompt` / `ModelConfig.system_prompt`
两个字段都在（`atomcode-config/src/config/provider.rs:11 / 146 / 200`），
`config/mod.rs:1063 / 1528` 会把它们在结构体之间搬来搬去，
**但全仓没有任何地方读取它的值去拼 prompt**。唯一叫得上名字的
`--system-prompt` / `--system-prompt-file` 在 `atomcode-clix`（独立的代码评审 CLI）
里，走的是那个 CLI 自己的 reviewer persona，与主 agent 无关。
也就是说这个字段目前是**死字段** —— 这更像上游遗留缺陷而不是设计，提 PR 的成功率较高。
