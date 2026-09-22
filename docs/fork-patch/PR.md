# 上游 PR 草稿

> 目标仓库：`atomgit_atomcode/atomcode`（GitCode）· 基点 `main`
> 分支：`agentpit-io/atomcode:feat/domain-persona`（fork 基点 `v5.1.0`）
> 提交后把链接回填到 `docs/fork-patches.md` 与 `docs/开发文档/I2-性能优化报告.md`。

---

## 标题

feat(config): 三个部署侧开关 —— 整体替换编码人设、按名单决定挂载哪些工具

## 正文

### 背景：我们在拿 AtomCode 做一个垂直领域发行版

我们在用 AtomCode 5.1.0 做一个**证券投研**方向的发行版：工作区里放的是持仓、
投资论点、因子定义和研报，不是代码；用户问的是"这只票基本面怎么样"，不是"帮我改个 bug"。
底座能力（daemon HTTP/SSE、MCP、hook、skill）都很好用，**唯一迈不过去的坎是系统提示**。

### 问题：内置编码人设没有任何配置能替换

`crates/atomcode-coding/src/persona.rs` 在进程内把系统提示拼出来。我们逐条试过现有的手段：

| 手段 | 能做到什么 | 做不到什么 |
|---|---|---|
| 项目指令文件（`AGENTS.md` / `.atomcode.md`） | 追加在内置人设**之后**，并且有 `## PRECEDENCE:` 背书 | 删不掉 `## WORKFLOW:` / `## TOOLS:` / `## DOING TASKS:` / `## CODE REVIEW:` / `## GIT COMMITS:` 等段落，它们照样在提示里 |
| 环境变量 | 关掉 4 个可选块（TODO / SUBAGENT / MEMORY_TOOL / REQUEST_USER_INPUT） | 实测只省 3 936 token，核心的编码工作流规则一条都关不掉 |
| 权限档 | 管工具能不能调 | 不改人设；而且 `plan` 档每轮注入"给个方案然后停下来等批准"，对非编码场景是负作用 |

结果是模型每一轮都在两套互相矛盾的规则之间选边：系统提示让它 UNDERSTAND → SEARCH →
PLAN → **EDIT** → **VERIFY**，项目指令让它去调数据工具写研报。**追加解决不了这个问题，
需要的是让那些规则「消失」。**

### 另外：`system_prompt` 这个字段已经有了，但从来没被读到过

`ProviderConfig::system_prompt`（`crates/atomcode-config/src/config/provider.rs`）
和 `ModelProfileConfig::system_prompt` 都在，序列化/反序列化都正常，但全树搜索下来
**没有任何代码把它送进 persona 的拼装路径**。用户配了它，不会报错，也不会生效。
我们倾向认为这是遗留缺陷而不是有意为之。

### 第二个问题：工具也没法按部署裁

同一件事的另一半。`[permissions]` 能决定一次调用要不要弹窗，但工具**照样出现在
发给模型的 `tools` 数组里** —— schema 的 token 照付，模型照样会去试，被拒之后再
换一个工具试。一个证券投研部署用不到 `list_symbols` / `trace_callers` /
`blast_radius` / `atomgit_pr` 这些，可是没有任何配置能让它们不出现。

内核里其实已经有正确的分层：`ToolRegistry::mount` 的注释写着
「Unmounted tools never produce a ToolDef and are not resolvable during a turn →
zero effect on the agent」。缺的只是**让部署方决定挂载名单**的那个口子。

### 这个 PR 做了什么

三个部署侧开关，未配置时行为**逐字节不变**：

1. **`persona::resolve_persona(override, built_in)`** —— 配了外部人设就整体替换内置人设，
   没配就返回内置的。内置人设做成惰性闭包，配了外部人设时**根本不去构造它**。
2. **让 `system_prompt` 真正生效**，并补一个文件形式 `system_prompt_file`
   （整个文件的内容替换，相对路径按 config 目录解析，inline 优先）。
   接到 TUI（`CodingRuntimeConfig`）与 daemon（`live_api::chat_runtime_config`）两条路径上，
   `/model` 切换（`apply_provider_config`）也重新解析 —— 否则换个模型会悄悄把内置编码人设换回来。
3. **`[tools] allow` / `deny`** —— 挂载名单的白/黑名单（`atomcode_coding::toolfilter`）。
   模式写法三种：精确名 `read_file`、前缀 `mcp__screener__*`、族
   `group:coding` / `group:codeintel` / `group:atomgit` / `group:skills` /
   `group:subagent` / `group:mcp`。`deny` 在 `allow` 之后判，deny 赢。

```toml
[models.my-domain-model]
provider = "openai-compatible"
model    = "..."
system_prompt_file = "personas/my-domain.md"   # 不写这一行，行为与现在完全一致

[tools]
deny = ["group:codeintel", "group:atomgit"]    # 不写这一段，行为与现在完全一致
```

**过滤器落在四个挂载点上**，少一个就是一句空话：基础工具名单（`prepare`）、
MCP 初次就绪发布、单台 MCP 连上时的增量发布、以及运行时注入的额外工具
（`register_extra_tool`，`/loop` 的 `schedule_wakeup` 走那条）。注册侧一个字不动 ——
工具照样注册，只是不挂载，这正是上面那段内核注释描述的语义。

### 四个刻意的设计选择

0. **`deny` 是「不挂载」，不是「不许调」。** 后者 `[permissions]` 已经做了。
   区别在于 token 与模型行为：没挂载的工具压根不进 `tools` 数组，模型看不见也想不起来。
1. **整体替换，不是追加。** 追加这件事项目指令文件已经能做了，再加一个开关没有意义。
2. **空值 / 文件读不到 → 回退内置人设，但打一条 `tracing::warn!`。**
   用空系统提示启动一个 agent 比回退糟得多；但静默回退会让一个拼错的路径看起来像
   "这个功能根本没生效"。`atomcode-config` 里没有 logger，按它既有的
   `load_with_diagnostics` 惯例把诊断回传给调用方，由 `atomcode-coding` /
   `atomcode-daemon` 用各自的 `tracing` 打出来。
3. **inline `system_prompt` 优先于 `system_prompt_file`。** inline 是已有字段，
   保持它优先级最高，不改变任何既有配置的行为。

### 改动规模

19 个文件。其中 11 个只是给结构体字面量补一个 `None` 字段（给 pub 结构体加字段
就是这么回事），1 个是新文件 `crates/atomcode-coding/src/toolfilter.rs`。
有逻辑的是：`persona.rs`、`assemble.rs`、`parts.rs`、`config/provider.rs`、
`config/mod.rs`、`coding/config.rs`、`daemon/live_api.rs`。

新增 12 条单测，其中两条专门锁"未配置时行为不变"：

```rust
#[test]
fn no_override_keeps_the_built_in_persona_byte_for_byte() {
    let built_in = coding_persona("deepseek-v4-flash", false, false);
    for empty in [None, Some(""), Some("   \n ")] {
        assert_eq!(resolve_persona(empty, || coding_persona("deepseek-v4-flash", false, false)),
                   built_in, "an unset / blank override must not change existing behavior");
    }
}

#[test]
fn unconfigured_keeps_every_tool() {
    let f = ToolFilter::default();
    assert!(f.is_noop());
    for name in ["read_file", "bash", "mcp__x__y", "list_symbols"] {
        assert!(f.keeps(name), "{name} must still mount when nothing is configured");
    }
}
```

### 验证

按 `.github/workflows/check.yml` 的四道门跑（Linux / rust stable 1.98.1 / Debian 12）：

| 门 | 结果 |
|---|---|
| `cargo fmt --all -- --check`（阻塞） | **通过**（rc=0） |
| `cargo check --workspace --all-targets`（阻塞） | **通过**（rc=0） |
| `cargo clippy --workspace --all-targets`（report-only） | rc=0；补丁碰的文件上 0 条警告 |
| `cargo test --workspace`（report-only） | **1 693 passed / 1 failed / 1 ignored** |

那一条失败是 `plugin::marketplace::tests::git_runs_rejects_present_but_failing_stub`
（「一个 `--version` 能成功的二进制必须被当成 git」——它往 tempdir 里写一个 shell
桩并执行它）。三条证据说明它与这个补丁无关：

1. 它在 `crates/atomcode-capabilities/` 里，而这个补丁**一个字都没动那个 crate**。
2. 单独跑这一条（`cargo test --workspace git_runs_rejects`）在补丁树上**通过**。
3. 编出来的 `atomcode-capabilities` 测试二进制在补丁树与干净 `v5.1.0` 树上
   **是同一个 cargo fingerprint**（`atomcode_capabilities-81779d80689760d2`）——
   同一份产物，不可能一个失败一个通过。

看起来是并行跑 1 693 条测试时的偶发（写+执行临时文件那类测试对负载敏感）。

### 另外：拿两个二进制跑同一个 stub 比过一次

「未配置时行为不变」这句话不该只靠读代码断言。用一个只记录请求的 stub provider，
同一道题、同一个工作区，分别用**官方 5.1.0 二进制**和**打了补丁的二进制**各跑一遍
headless：

| 档 | 系统提示 | 工具数 |
|---|---|---|
| 官方 5.1.0 | 27 397 字符 | 33 |
| 补丁版，不配任何参数 | 27 397 字符 | 33 |
| 补丁版，只配 `system_prompt_file` | 12 067 字符 | 33 |
| 补丁版，再配 `[tools] deny` | 12 067 字符 | **20** |

前两行的系统提示**逐字节相同**、工具清单相同。第四行摘掉的 13 个是
`atomgit_*` 四个 + 代码智能八个 + `task`。

### 一个给文档的提醒

整体替换会把内置人设末尾的 `## ENVIRONMENT: Today's date: …` 一起替掉。
用替换人设的部署需要自己注入日期（我们是用 `UserPromptSubmit` hook 注入的，
顺带修掉了一个小问题：内置那一行用的是进程所在时区的日期，我们的容器跑 UTC，
显示的日期比用户所在时区晚一天）。如果这个 PR 被接受，文档里值得提一句。

### 我们没有做的事

- 没有动内置人设的任何一个字。
- 没有加"追加模式"之类的第二个开关 —— 保持一个语义：配了就整体替换。
- 没有碰 kernel / capabilities 的依赖方向（按 `AGENTS.md` 的架构约束）。
  工具过滤放在 `atomcode-coding`（挂载名单的所有者），kernel 的
  `ToolRegistry` / `MountedTools` 一行没改。
- 没有把 `group:*` 的族定义硬编成一张表：`coding` / `codeintel` / `skills`
  直接问各自的 `*_tool_names()`，所以上游往某个族里加工具时这里自动跟上。
  只有 `atomgit` 例外 —— 那个模块在一个可选 Cargo feature 后面，按名字前缀
  `atomgit_` 匹配，免得同一份配置在不同构建下含义不一样。
