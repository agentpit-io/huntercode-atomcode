# fork 补丁 · 两个开关，让垂直领域发行版能换掉编码人设

> 状态：**待定 —— 等 M2 的 A/B 数据**。
> 按总控「已拍板决策 4」与计划 v0.2 §5.2，只有 A/B 得分 < 基线 80% 时才真的 fork。
> 补丁本身已经写好并在测试机编译、测试通过（见下「验证记录」），
> 决策一出就能直接用；若决策是「不 fork」，这份文档作为**已备好但未启用**的方案留档。
>
> 决策结论与依据：见 `docs/开发文档/M2-AB对比报告.md`。

## 1. 为什么需要这两个开关

AtomCode 是编码 agent，系统提示由 `crates/atomcode-coding/src/persona.rs` 在进程内拼出来，
**没有任何配置能替换它**。`docs/coding-bias-analysis.md` 把这件事逐条查过：

- 项目指令文件（`AGENTS.md` / `.atomcode.md`）只能**追加**在内置人设之后，不能删改它。
  实测渲染出来的系统提示里，`## WORKFLOW:` / `## DOING TASKS:` / `## CODE REVIEW:` /
  `## GIT COMMITS:` 等段落照样在（§2.3）。
- 环境变量只能关掉**四个**可选块（TODO / SUBAGENT / MEMORY_TOOL / REQUEST_USER_INPUT），
  实测合计省 3 936 token —— 占系统提示的一小部分，核心的编码工作流规则一条都关不掉（§6.1）。
- 权限档只管工具能不能调，不改人设；而且 `plan` 档每轮额外注入「给个方案然后停下来等批准」，
  对投研场景是负作用（§7）。

结果是：一个投研发行版只能靠「项目指令覆盖编码指令」这种**软对抗**——
让模型自己在两套互相矛盾的规则里选边。这正是 M2 要评测的风险点。

上游其实已经有一个 `system_prompt` 配置字段（`ProviderConfig::system_prompt`），
但它**从来没有被读到过**：全树搜索，除了序列化/反序列化与几处结构体字面量，
没有任何代码把它送进 persona 的拼装路径。这更像遗留缺陷，不是有意为之。

## 2. 补丁范围（计划 v0.2 §5.2 的两项，不多做一行）

| # | 改什么 | 怎么保证「未配置时行为不变」 |
|---|---|---|
| 1 | `persona.rs` 新增 `resolve_persona(override, built_in)`：给了外部人设就**整体替换**内置人设，没给就原样返回内置的 | `resolve_persona(None, ..)` 走 `built_in()` 闭包，与改前同一个函数、同一份字符串；单测 `no_override_keeps_the_built_in_persona_byte_for_byte` 逐字节比对 |
| 2 | 让 model 配置里现有的 `system_prompt` 真正生效，并补一个文件形式 `system_prompt_file`（整文件内容替换） | 两个字段都是 `Option`，`#[serde(default, skip_serializing_if = "Option::is_none")]`，老配置文件读进来是 `None`、写回去不多一行 |

一共 **18 个文件、+311 / −24 行**，其中 11 个文件只是给结构体字面量补一个 `None` 字段。

真正有逻辑的只有四处：

| 文件 | 改动 |
|---|---|
| `atomcode-coding/src/persona.rs` | `resolve_persona()` + 2 条单测 |
| `atomcode-coding/src/assemble.rs` | 拼装处改成走 `resolve_persona`，内置人设变成惰性闭包（配了外部人设就根本不去构造它） |
| `atomcode-config/src/config/provider.rs` | `resolve_system_prompt_override()`（inline 优先、文件相对 config 目录解析、空/读不到回退并**带 warning**）+ `system_prompt_file` 字段 + 5 条单测 |
| `atomcode-coding/src/config.rs`、`atomcode-daemon/src/live_api.rs` | 把解析结果接到 TUI 与 daemon 两条运行路径上；`/model` 切换（`apply_provider_config`）也重新解析，否则换模型会悄悄把内置编码人设换回来 |

### 刻意做的三个选择

1. **整体替换，不是追加**。垂直领域要的是编码工作流规则**消失**，不是压在下面。
   追加这件事项目指令文件已经能做了，再加一个开关没有意义。
2. **空值 / 读不到 → 回退内置人设 + 打一条 warning**。用空系统提示启动一个 agent 比回退糟得多；
   但静默回退会让一个拼错的路径看起来像「这个功能根本没生效」，所以必须有 warning。
   `atomcode-config` 这个 crate 里没有 logger，按它既有的 `load_with_diagnostics` 惯例
   把诊断信息**回传给调用方**，由 `atomcode-coding` / `atomcode-daemon` 用各自的 `tracing` 打出来。
3. **inline `system_prompt` 优先于 `system_prompt_file`**。inline 是上游已有字段，
   保持它的优先级最高，不会因为我们新加的字段改变任何既有配置的行为。

## 3. 怎么用

```toml
# ~/.atomcode/config.toml
[models.hunter-chat]
provider      = "openai-compatible"
model         = "hunter-chat"
base_url      = "https://hunter.agentpit.io/api/saas/llm/v1"
# 整个文件的内容替换掉内置编码人设。相对路径按 config 目录解析。
system_prompt_file = "personas/hunter-research.md"
```

不写这一行，行为与官方二进制**完全一致**。

## 4. 构建：**必须在 bookworm 容器里编**（实测踩过）

测试机是 Ubuntu 24.04（glibc 2.39）。直接在宿主上 `cargo build --release` 编出来的产物，
装进 daemon 运行镜像（`python:3.12-slim-bookworm`，glibc **2.36**）时：

```
atomcode: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.39' not found (required by atomcode)
```

三个二进制的 glibc 需求（`strings -a … | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1`）：

| 二进制 | 最高 GLIBC 需求 | 说明 |
|---|---|---|
| 官方 npm 包里的 5.1.0 | **2.17** | manylinux2014 那一档，兼容面很宽 |
| 宿主（Ubuntu 24.04）自编 | **2.39** | 装不进运行镜像 |
| bookworm 容器里自编 | 见下表 | 对齐运行镜像的 2.36 |

所以 fork 的构建脚本是 `~/hca/bin/build-fork-bookworm.sh`：在 `rust:1-bookworm` 容器里编，
`CARGO_TARGET_DIR` 与 cargo registry 都挂宿主目录（增量与下载可复用），
`--memory 5g --cpus 2` + `flock ~/.hca-heavy.lock`，与测试机上另一条链路的重负载串行。
**不追 2.17**：那需要更老的构建基底，而我们只要求「能装进本发行版的运行镜像」。

上游 `[profile.release]` 原样不动（`opt-level="z"` + `lto` + `codegen-units=1` + `strip` + `panic="abort"`），
这样产物行为与官方构建一致，只差我们那点补丁。

## 5. 验证记录

按上游 `.github/workflows/check.yml` 的**四道门**逐条跑（`~/hca/bin/verify-fork.sh`）。
注意上游有**两道阻塞门**，不是一道：

| 门 | 命令 | 上游是否阻塞 | 本补丁结果 |
|---|---|---|---|
| gate-1 | `cargo check --workspace --all-targets` | **阻塞** | 见下表 |
| gate-2 | `cargo test --workspace` | report-only（`continue-on-error`） | 见下表 |
| lint-1 | `cargo fmt --all -- --check` | **阻塞** | 见下表 |
| lint-2 | `cargo clippy --workspace --all-targets` | report-only（598 条存量警告） | 见下表 |

> `--all-targets` 这一项要紧：它会编 `tests/` 下的集成测试目标，而 `cargo build` / 不带
> `--all-targets` 的 `cargo check` 都编不到。我们这个补丁往 `ProviderConfig` /
> `ModelProfileConfig` / `ResolvedModelConfig` 三个结构体里加了字段，**结构体字面量最容易
> 在集成测试里漏补**，正好是这道门能抓住的那类问题。

实测表见文末「附：实测表」。

## 6. 替换用的人设文件

`distro/personas/hunter-research.md`（11 314 字符，内置人设是 20 855 字符，**−46%**）。

整体替换不是「删掉编码规则就完事」—— 内置人设里有几段与编码无关、去掉会真的退化，
必须在替换文件里保留：

| 保留的段 | 为什么不能丢 |
|---|---|
| `## CONTENT SAFETY` | 安全边界。只把开头一句 "You are a coding assistant" 改成 securities-research assistant，其余原文保留 |
| `## SYSTEM REMINDERS` | 不然模型会对着 `<system-reminder>` 回一句"已记录" |
| `## MCP SERVER INSTRUCTIONS` | MCP 服务器下发的指令是**不可信输入**，这一段是防提示注入的 |
| `## CONTEXT MANAGEMENT` | 不然模型会劝用户"新开一个会话" |
| `## SCOPE` / `## RISKY ACTIONS` / `## WHEN COMMANDS FAIL` | 越界与破坏性操作的约束 |
| `## PROGRESS SIGNPOSTS` / `## CONTENT-TRANSFORMATION` / `## USER COMMUNICATION AND POLLING` | 长报告分次写盘、不许用"（其余省略）"、不许用 `echo` 跟用户说话 |
| `## SKILLS` | 六个投研技能全靠它被加载 |
| 开头的产品身份段 | 上游明说产品身份不可覆盖，替换文件照样保留 |

**丢掉的**：`## WORKFLOW`（UNDERSTAND→…→EDIT→VERIFY）、`## TOOLS`（代码智能工具那一套）、
`## DOING TASKS`、`## OPENING FILES`、`## CHINESE CODE SUPPORT`、`## GIT COMMITS`、
`## ATOMGIT TOOLS`、`## CODE REVIEW`。

**一处需要留意的副作用**：内置人设末尾的 `## ENVIRONMENT: Today's date: …` 也一并没了。
本发行版不受影响 —— `context.sh`（UserPromptSubmit hook）每轮注入的日期**更准**：
内置那一行用的是容器 UTC 日期（实测显示 `2026-09-21`，而上海时间已是 09-22），
hook 注入的是上海时间并带 AKShare 真实交易日历判定。上游若采纳这个补丁，
文档里应提醒「整体替换会连 ENVIRONMENT 段一起替掉」。

配置接法：`deploy/daemon/hca-init.py` 认 `HCA_LLM_SYSTEM_PROMPT_FILE`，
给了才往 `config.toml` 写 `system_prompt_file` 这一行 —— **官方 5.1.0 会把这个未知键
读进来又原样忽略（不报错也不生效）**，所以不给环境变量时不写，免得配置文件里躺着
一行看起来生效、实际没生效的东西。

镜像接法：`deploy/Dockerfile.daemon-fork`（`FROM hca-daemon:dev`，只换二进制，
自带一道 sha256）。**不给主 Dockerfile 加「用本地二进制」的开关** —— 那会在主路径上
开一个绕过官方二进制 sha256 校验的口子，而那道校验是刻意做硬的。

## 7. 分支基点与上游 PR

**上游在 GitCode**：`docs/来源说明.md` 里那份上游克隆的 remote 就是
`git@gitcode.com:atomgit_atomcode/atomcode.git`，`main` 的最新提交与 GitCode API
查到的一致。所以 fork 和 PR 都在 GitCode 上做，不需要绕去 atomgit.com
（那边的 HTTPS 在美国机房返 418，见总控）。

**分支基点取 `main`（`e4215f733`），不是 `v5.1.0` 标签（`72b538e8c`）**。
两者之间只有 4 个提交、改动只有 `README.zh-CN.md` 与 `latest.json`：

```
$ git diff --stat 72b538e8c..e4215f733
 README.zh-CN.md | …
 latest.json     | 28 ++++++++++++++--------------
```

**一行代码都没变**，所以基于 `main` 编出来的二进制与「官方 5.1.0 + 我们的补丁」
在行为上等价，pins.lock 的版本对应关系仍然成立；同时 PR 也不必让上游去 rebase 一个
落后于 main 的分支。

PR 里要说清楚的一句话：**垂直领域发行版需要的是让编码工作流规则「消失」，
而不是在它后面再追加一段。** 项目指令文件（`AGENTS.md` / `.atomcode.md`）只能追加，
所以现有机制解决不了这个问题；而 `system_prompt` 这个配置字段上游已经有了，
只是从来没有被读到过。

PR 链接：见「附：实测表」。fork 只有在决策为 fork 时才会创建。

## 8. 来源与许可

- 上游：`atomgit_atomcode/atomcode`，MIT。基线提交 `72b538e8c7da030a5597536bf3a882ca9de539ea`（tag `v5.1.0`）。
- 补丁以 `docs/fork-patch/apply.py` 的形式保存在本仓库（幂等，可重复对同一棵源码树执行），
  本仓库不复制上游源码。

---

## 附：实测表

全部在测试机（Ubuntu 24.04 · 2 核 8G）上跑，重负载任务走 `flock ~/.hca-heavy.lock`
串行化。源码树 `~/hca/atomcode-fork`，基线 `72b538e8c7da030a5597536bf3a882ca9de539ea`
（tag `v5.1.0`）+ `docs/fork-patch/apply.py`（18 个文件，+311 −24）。

### A. 上游 `check.yml` 四道门

| 门 | 命令 | 上游是否阻塞 | rc | 实测结果 |
|---|---|---|---|---|
| lint-1 | `cargo fmt --all -- --check` | **阻塞** | **0** | 零输出 |
| gate-1 | `cargo check --workspace --all-targets` | **阻塞** | **0** | 111 行日志，全是存量 `dead_code` 警告 |
| lint-2 | `cargo clippy --workspace --all-targets` | report-only | **0** | 5 301 行日志，**补丁新增的 311 行上 0 条警告** |
| gate-2 | `cargo test --workspace` | report-only（`continue-on-error`） | 101 | 46 个测试二进制、**3 314 passed / 2 failed / 9 ignored** |

### B. 那 2 条失败与本补丁无关

```
---- webui::tests::serves_embedded_index stdout ----
panicked at crates/atomcode-daemon/src/webui.rs:75:9: index.html should be embedded
---- webui::tests::unknown_path_falls_back_to_index stdout ----
panicked at crates/atomcode-daemon/src/webui.rs:84:9: SPA route should fall back to index
```

三条互相独立的证据：

1. **补丁没碰这些文件。** `git diff --stat` 的 18 个文件里没有
   `crates/atomcode-daemon/src/webui.rs`，也没有 `webui/` 下的任何东西。
2. **断言考的是前端产物在不在。** `webui.rs:15-18` 用
   `#[derive(RustEmbed)] #[folder = "../../webui/dist/"] #[allow_missing = true]`
   把前端产物编进二进制；`#[allow_missing]` 让 `webui/dist/` 缺席时照样编过，
   代价就是这两条测试失败。实测 `ls webui/dist` → `No such file or directory`
   —— 我们从没跑过 `cd webui && npm install && npm run build`（源码里
   `NOT_BUILT_HELP` 那段文案写的就是这个修复步骤）。
3. **上游 CI 自己也不构建前端。** `check.yml` 的 `gate` job 从 checkout 直接到
   `cargo check`，中间没有任何 npm 步骤 —— 所以这两条在上游 runner 上同样红，
   而那一步的注释写着「The suite is green locally on macOS … but it has not yet
   run on a Linux runner」，与之吻合。这也正是它被标 `continue-on-error` 的原因。

结论：**两道阻塞门全绿，两道报告门里唯一的失败是环境缺前端产物，不是补丁引入的回归。**

### C. fork 二进制

| 项 | 值 |
|---|---|
| 构建方式 | `rust:1-bookworm` 容器，`cargo build --release -p atomcode --bin atomcode`（`--memory 5g --cpus 2`） |
| 产物 | `~/hca/target-bookworm/release/atomcode` |
| 大小 | 38 986 472 字节（官方二进制 40 214 480 字节） |
| sha256 | `7688e749359261ae2171fed808d04f58dcac8b7c77650987b02a04abf5ad4465` |
| 自述版本 | `atomcode 5.1.0 (unknown)` |
| 最高 GLIBC 需求 | **2.34** |

**已知差距：官方二进制最高只要 GLIBC 2.17，我们编的要 2.34。**（两个二进制都用
`objdump -T | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -3` 实测。）运行镜像是
bookworm（glibc 2.36），所以本发行版不受影响；但这意味着 fork 二进制**跑不了
CentOS 7 / Ubuntu 18.04 这类老底座**，而官方的可以。要对齐得用更老的构建底座
（manylinux / 老 glibc 的交叉工具链），本轮不做 —— 发行版只分发 Docker 镜像。
官方是怎么编出 2.17 的没有公开说明，已作为问题 B10 记入 `docs/questions-for-atomgit.md`。

### D. 还没做的

| 项 | 状态 |
|---|---|
| 在 GitCode fork 到 `agentpit-io` | **未做** —— 只有 A/B 决策为 fork 才做 |
| 分支 `feat/domain-persona`（基点 `main` = `e4215f733`，见 §7） | **未做**。注意当前源码树基点是 tag `72b538e8c`，建分支时要把补丁挪到 `main` 上 |
| 上游 PR | **未提**。正文草稿在 `docs/fork-patch/PR.md` |
| `pins.lock` 写入 fork 二进制 | **未写** —— 要等 fork 提交号定下来才能写「fork 提交 + 对应上游版本 + sha256」三元组 |
| daemon 镜像改用 fork 二进制 | **未切**。镜像层 `deploy/Dockerfile.daemon-fork` 已就绪（`FROM hca-daemon:dev`，只换二进制，自带一道 sha256 校验） |
