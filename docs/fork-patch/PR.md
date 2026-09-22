# 上游 PR 草稿

> 目标仓库：`atomgit_atomcode/atomcode`（GitCode）· 基点 `main`
> 分支：`agentpit-io/atomcode:feat/domain-persona`
> **只有 M2 的 A/B 决策为「fork」时才提交。** 提交后把链接回填到
> `docs/fork-patches.md` 与 `docs/开发文档/M2-AB对比报告.md`。

---

## 标题

feat(coding): 让 `system_prompt` 生效，并支持用外部文件整体替换编码人设

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

### 这个 PR 做了什么

两件事，未配置时行为**逐字节不变**：

1. **`persona::resolve_persona(override, built_in)`** —— 配了外部人设就整体替换内置人设，
   没配就返回内置的。内置人设做成惰性闭包，配了外部人设时**根本不去构造它**。
2. **让 `system_prompt` 真正生效**，并补一个文件形式 `system_prompt_file`
   （整个文件的内容替换，相对路径按 config 目录解析，inline 优先）。
   接到 TUI（`CodingRuntimeConfig`）与 daemon（`live_api::chat_runtime_config`）两条路径上，
   `/model` 切换（`apply_provider_config`）也重新解析 —— 否则换个模型会悄悄把内置编码人设换回来。

```toml
[models.my-domain-model]
provider = "openai-compatible"
model    = "..."
system_prompt_file = "personas/my-domain.md"   # 不写这一行，行为与现在完全一致
```

### 三个刻意的设计选择

1. **整体替换，不是追加。** 追加这件事项目指令文件已经能做了，再加一个开关没有意义。
2. **空值 / 文件读不到 → 回退内置人设，但打一条 `tracing::warn!`。**
   用空系统提示启动一个 agent 比回退糟得多；但静默回退会让一个拼错的路径看起来像
   "这个功能根本没生效"。`atomcode-config` 里没有 logger，按它既有的
   `load_with_diagnostics` 惯例把诊断回传给调用方，由 `atomcode-coding` /
   `atomcode-daemon` 用各自的 `tracing` 打出来。
3. **inline `system_prompt` 优先于 `system_prompt_file`。** inline 是已有字段，
   保持它优先级最高，不改变任何既有配置的行为。

### 改动规模

18 个文件、+311 / −24 行。其中 11 个文件只是给结构体字面量补一个 `None` 字段。
有逻辑的只有 4 处：`persona.rs`、`assemble.rs`、`config/provider.rs`、
`coding/config.rs` + `daemon/live_api.rs`。

新增 7 条单测，其中一条专门锁"未配置时逐字节不变"：

```rust
#[test]
fn no_override_keeps_the_built_in_persona_byte_for_byte() {
    let built_in = coding_persona("deepseek-v4-flash", false, false);
    for empty in [None, Some(""), Some("   \n ")] {
        assert_eq!(resolve_persona(empty, || coding_persona("deepseek-v4-flash", false, false)),
                   built_in, "an unset / blank override must not change existing behavior");
    }
}
```

### 验证

按 `.github/workflows/check.yml` 的四道门跑（Linux / stable）：

| 门 | 结果 |
|---|---|
| `cargo fmt --all -- --check`（阻塞） | 见实测表 |
| `cargo check --workspace --all-targets`（阻塞） | 见实测表 |
| `cargo clippy --workspace --all-targets`（report-only） | 见实测表；**补丁新增的 311 行上 0 条警告** |
| `cargo test --workspace`（report-only） | 见实测表 |

### 一个给文档的提醒

整体替换会把内置人设末尾的 `## ENVIRONMENT: Today's date: …` 一起替掉。
用替换人设的部署需要自己注入日期（我们是用 `UserPromptSubmit` hook 注入的，
顺带修掉了一个小问题：内置那一行用的是进程所在时区的日期，我们的容器跑 UTC，
显示的日期比用户所在时区晚一天）。如果这个 PR 被接受，文档里值得提一句。

### 我们没有做的事

- 没有动内置人设的任何一个字。
- 没有加"追加模式"之类的第二个开关 —— 保持一个语义：配了就整体替换。
- 没有碰 kernel / capabilities 的依赖方向（按 `AGENTS.md` 的架构约束）。
