# HCA 的 hook 设计 · opencode 插件 → AtomCode hook 映射

> 面向要改这套 hook 的人。读之前先知道两件事：
> ① AtomCode 的 hook **只有 8 个事件**、配置文件是 `.hooks.json`、只支持 shell 命令
>    （上游 `docs/hooks.md` 与 `docs/webhook-guide.md` 已过时，以 `cc_hooks.rs` 源码为准）；
> ② 本发行版**不改 AtomCode 内核**，hook 是四个外部面之一，也是唯一能在无人值守下
>    压住模型行为的那一个（M0 §4.5 实测：`bypass` 权限档下 PreToolUse 的 deny 依然生效）。

## 1. 现在有哪些 hook

`distro/workspace-template/.hooks/` 下五组实现，`.hooks.json` 里 **8 条注册**：

| 注册名 | 事件 | 实现 | 默认 | 干什么 |
|---|---|---|---|---|
| `hca-guard` | PreToolUse（matcher `*`） | `guard.sh` → `guard.py` | 开 | 唯一的硬约束：写类工具只放行 `reports/` `theses/`；`bash`/`bash_start` 只放行只读与纯计算；任何工具都不许碰工作区外；给 hunter 系 MCP 注入 `_hermes_user_id`；为 audit 落「开始时刻 + 参数摘要」 |
| `hca-context` | UserPromptSubmit | `context.sh` → `context.py` | 开 | 注入真实日期 / 交易日与时段（AKShare 真实日历）/ 工作区资产清单 |
| `hca-lang` | UserPromptSubmit | `lang.sh` → `lang.py` | 开（`HCA_LANG_HOOK=0` 关） | 语言硬约束（承接 opencode 的 `hunter-lang` 插件的**提示词侧**那一道） |
| `hca-budget` | UserPromptSubmit | `budget.sh` → `budget.py` | **关** | 超预算就把这一轮退回 |
| `hca-audit` | PostToolUse（matcher `*`） | `audit.sh` → `audit.py` | 开 | 落 `audit.jsonl` |
| `hca-audit-fail` | PostToolUseFailure（matcher `*`） | 同上 | 开 | 失败的调用走这个事件 |
| `hca-budget-stop` | Stop | `budget.sh` → `budget.py` | **关** | 记账 |
| `hca-budget-stopfail` | StopFailure | 同上 | **关** | 记账（provider 出错那一类终止） |

为什么每组都是 `.sh` + `.py` 两个文件：在 `.sh` 里用 heredoc 喂 python 会**占掉 stdin**，
python 读到的是脚本自己而不是 hook 的输入（M1 实测踩过，`session_id` / `tool` 全是 null）。
`.sh` 只负责挑一个 python 解释器然后 `exec`。

## 2. opencode 插件 → AtomCode hook 映射表

opencode 那套有两个插件（`hunter-community/scripts/opencode-mcp/plugins/`，公开仓里就这两个；
全仓 grep `budget` 没有第三个）。加上 opencode 自己承担的两件事，逐条对照：

| opencode 侧 | 挂在哪 | 它做什么 | HCA 的去处 | 状态 |
|---|---|---|---|---|
| `hunter-mcp-context.ts` | `tool.execute.before` | 给 hunter 系 MCP 的参数注入 `_hermes_user_id`（从会话归属查） | **`hca-guard`**（PreToolUse 的 `hookSpecificOutput.updatedInput`） | ✅ 等价（M2 关闭 P0-5；M3 改成按 `session_id` 查 `/api/internal/session/{sid}/user`，多用户不串户） |
| `hunter-lang.ts` | `experimental.text.complete`（**出口**） | 整段回答写完后送 `/api/internal/lang/guard` 净化/翻译 | **拆成两半**：提示词侧 → `hca-lang`；出口侧 → **AtomCode 没有对应事件**，见 §5 | ⚠️ 只搬了一半，另一半是待办（P1-21） |
| opencode 的登录态 / 用户鉴权 | 服务端 | 谁是谁、能不能用 | **上移到 Web 层**，见 §6 | ✅ 有意上移 |
| （没有）预算控制 | — | opencode 版线上**没有**按会话/按天掐 token 的东西 | **`hca-budget`**，默认关 | ✅ 新增但默认关，行为与现状一致 |
| （没有）工具审计 | — | opencode 版没有落地的工具级审计流水 | **`hca-audit`** | ✅ 新增 |

## 3. 上游契约里必须知道的坑（全部读自 `cc_hooks.rs`，行号是 AtomCode 5.1.0）

| 事实 | 出处 | 影响 |
|---|---|---|
| `.hooks.json` 必须是**严格 JSON**，连 `//` 注释都不许；解析失败**静默返回空** | `cc_hooks.rs:170-176` | 表现是 `hooks list` 说文件 ✓ 找到了、却 `(No hooks loaded)`。说明文字只能塞在被 serde 忽略的 `_说明` 键里 |
| hook 的 `command` **不做环境变量展开** | `cc_hooks.rs:276` 直接 `sh -c <command>` | `${HCA_WORKSPACE}` 由 `deploy/daemon/hca-init.py` 在铺工作区时替换（`.mcp.json` 相反，交给 AtomCode 自己展开，密钥不落盘） |
| 加载顺序 = **hook 名字的字典序** | `HooksFile.hooks` 是 `BTreeMap`（`cc_hooks.rs:154`） | PreToolUse 是顺序折叠且遇 deny 就 `break`，所以名字决定谁先跑。目前 PreToolUse 只有 guard 一条 |
| UserPromptSubmit 的多个 hook **并发**跑，各自 stdout join 后追加到用户消息；任一条 block 即整轮退回 | `cc_hooks.rs:622-684` | context / lang / budget 三条互不影响；budget 要拦就直接拦得住 |
| 判定看 stdout **最后一行 JSON** | `last_json_line`，`cc_hooks.rs:422` | 纯文本注入的 hook（context / lang）**最后一行不能长得像 JSON** |
| `exit 2` 才是 block，且**必须有理由**；裸 exit 2 或解释器启动失败被当成"脚本坏了"而放行 | `cc_hooks.rs:376-420` | 所以本发行版一律 `exit 0` + stdout 表达语义，不用退出码 |
| **PostToolUse 的 payload 里没有 `tool_input`、没有耗时，两个事件都不带 call_id** | `cc_hooks.rs:763-903` | 审计要记参数与耗时，只能由 PreToolUse 侧落一份起始记录再配对（见 §4.2） |
| Stop / StopFailure 的 payload 只有 `session_id` / `transcript_path` / `stop_hook_active` / `stop_reason` / `cwd`，**没有 token** | `cc_hooks.rs:708-742` | 预算 hook 的 token 只能另找源（见 §4.3） |
| `transcript_path` 指向会话的 `.jsonl`，同目录同名的 `.meta` 是 `SessionMeta` | `parts.rs:1027-1034`、`session/manager.rs:1062` | 这是唯一**按会话精确**的用量源 |
| 超时会 `kill_on_drop` 连子进程一起杀 | `cc_hooks.rs:307` | 想异步上报必须 `start_new_session=True` 脱离进程组 |
| `atomcode hooks test <名>` 按**事件名或 command 子串**匹配，取第一个命中的；payload 是**固定的样例**，不能自定义 | `cli/main.rs:3752-3812` | 同一个脚本注册在多个事件上时，用事件名来点名；要喂自定义 payload 得用自己的用例脚本（`tools/hooks/run-hook-cases.sh`） |

## 4. 四个 hook 的实现要点

### 4.1 guard —— M4 补的四个真实绕过

都是先读上游源码找出来的，再写成单测；**逐条对 M3 那版 guard 复验过，六个用例旧版全部漏过**
（复验脚本与输出见 `docs/evidence/M4/guard-old-vs-new.txt`）：

1. **`bash_start`**：上游 `tools/mod.rs:210` 无条件注册 `bash_start` / `bash_poll` / `bash_kill`
   三个后台 shell 工具，`bash_start` 的参数名和 `bash` 一样是 `command`（`bash.rs:453`）。
   M2/M3 的 guard 只判 `tool == "bash"` —— 同一条 `rm -rf holdings/` 用 `bash` 被拒、
   丢到 `bash_start` 直接放行。现在 `BASH_TOOLS = {"bash", "bash_start"}` 同判。
2. **直接调取数库（待办池 P1-20）**：`INLINE_NET_RE` 认的是"谁在发 HTTP"，而
   `import akshare as ak; ak.stock_zh_a_daily(...)` 是 akshare 自己在内部发，脚本里
   一个 `requests` 字样都没有。改成按库名拦（`DATA_LIB_RE`，覆盖 akshare/tushare/
   yfinance/baostock/efinance/adata/pywencai/jqdatasdk/rqdatac/mootdx/qstock）。
3. **包装命令与 `sh -c`**：`env X=1 rm -rf /` 的首词是 `env`、`timeout 5 rm` 的首词是
   `timeout`，旧版判不到真正的命令；`sh -c "rm -rf x"` 的子命令整条在引号里，压根不是
   一个 token。现在 `real_head()` 剥包装（env/nohup/timeout/stdbuf/nice/ionice/setsid/
   command/exec/time/watch/xargs/parallel），`-c` 的子命令**递归判**（限 3 层）。
   顺带补了 `python -m pip|venv|http.server|…`（首词是 python，旧版判不到模块）。
4. **中间带 `..` 的路径**：判据从「以 `/` `~` `../` 开头」放宽到「任何含 `/` 的 token
   折成绝对路径再判」。旧版漏掉 `cat holdings/../../etc/passwd`。
   放宽不会误伤：折完还在工作区内的一律放行，`print(1/2)`、`2026/09/22`、`s/a/b/`
   折出来都在工作区里面（单测 `test_pure_calculation_still_allowed` 守着）。

guard 仍然**不拦**的（有意）：`web_fetch` / `web_search`（它们带 URL，来源可追溯）、
只读的 `read_file` / `grep` / `glob`（限制在工作区内）、`mcp__*`（那正是我们希望它走的路）。

### 4.2 audit —— 参数与耗时为什么要绕一道 guard

PostToolUse 的 payload 只有 `session_id` / `hook_event_name` / `tool_name` /
`tool_response` / `cwd`。要记「参数摘要」和「耗时」，就得有人在 PreToolUse 侧记下来；
而两个事件**都不带 call_id**，没有任何字段能把 pre 和 post 配起来。

两种做法：再挂一个 PreToolUse 的 audit hook（每次工具调用多起一个 python 进程），
或者让本来就每次都跑的 guard 顺手落一份。取后者：

* guard 在**放行的**调用上写 `.atomcode/tool-start/<sha1(session+tool)[:16]>.json`
  （`os.replace` 原子换名，避开多进程读改写竞争；M2 实测一轮里出现过 5 组并行工具调用）；
* audit 在 PostToolUse 里取走并删除，算出 `duration_ms`；
* **被 guard 拒的调用不留记录** —— 它不会有 PostToolUse，留下就成了孤儿，
  会被下一次同名调用配走、算出一个假的耗时；
* 同一 (会话, 工具) 的**并行**调用会互相覆盖，这时耗时按最后一次开始算，
  记录里标 `duration_src="guard-start(may-overlap)"`，不假装精确；
* 配不上就写 `null` + `"—"`，**不猜**（总控红线 1）。

其余字段：`user_id` 从 guard 的身份反查缓存 `.atomcode/session-user.json` 里读
（**只读缓存、不联网** —— 审计不该给每次工具调用加一跳 HTTP），拿不到回落
`HUNTER_USER_ID`，再拿不到写 `null`。`response_len` / `response_head`（默认 200 字符）
来自 `tool_response`。

写入：本地一次 `open(..., "a")` 追加（不 fsync、不读旧内容），超过 `HCA_AUDIT_MAX_MB`
（默认 64）轮转成 `.1`。可选异步上报 `HCA_AUDIT_WEBHOOK`：把记录写进临时文件、
`start_new_session=True` 派一个脱离进程组的子进程去 POST，父进程立刻退 ——
上游超时会 `kill_on_drop` 连子进程一起杀，不脱离进程组就白搭。

### 4.3 budget —— token 到底从哪来

Stop 的 payload 里没有 token。两个真实的源，都可能拿不到：

1. **会话 `.meta` 的 `turn_stats`**（首选，按会话精确）。字段形状是**真读出来的**
   （测试机主部署的真实会话，2026-09-22）：

   ```json
   {"turn_id": 1, "round_count": 4, "tool_call_count": 3, "duration_ms": 30256,
    "total_tokens": 0, "used_tokens": 12783,
    "model_usage": [{"provider_id": "hunter", "model_id": "hunter-chat",
                     "tokens": {"input": 17038, "output": 40, "cached_input": 0}}]}
   ```

   实测结论：`tool_call_count` 与 `duration_ms` 可靠；**`total_tokens` 恒 0**
   （与待办池 P1-12 同一个根因）；**`model_usage` 只有部分 turn 有** —— 同一会话里
   turn 1 有、turn 2 整个字段都不在。所以 budget 会同时记「有 usage 的 turn 数 / 总 turn 数」，
   **只有覆盖率 100% 才拿它当 token 源**。
2. **网关配额差值**（兜底，按 key 全局）：`GET $HCA_QUOTA_URL` 的 `used_today`，
   两次 Stop 之间的差。这是 M2 评测算成本用的同一个口子，是真实计量，
   但它统计的是**这把 key 的全部消耗** —— 单工作区私有化部署（已拍板决策 5）下
   就等于这套栈的用量；key 被别处共用时会偏大。记录里写明 `tokens_src`。

两个都拿不到 → token 写 `null` 且**不按 token 拦**（"不知道" ≠ "超了"）。
工具调用次数一路都拿得到，所以那条闸是稳的。

阈值全是 0（不限）、总开关 `HCA_BUDGET_ENABLED=0`（**默认关**）。
为什么默认关：计划 v0.2 TP-05 写的是「默认关，与现状一致」——
公开仓里本来就没有 budget 插件，把默认改成"会拒绝用户"属于偷偷改产品语义。

### 4.4 lang —— 见 §5

## 5. 出口强校验的去处（这一条是**缺口**，不是已完成项）

原 `hunter-lang` 插件挂在 opencode 的 `experimental.text.complete` 上，是**出口**：
整段回答写完之后送 `/api/internal/lang/guard`，拿净化/翻译后的中文回来覆盖最终 part。
插件头注释里引的铁律 A10 说得很明确：**「prompt 里的中文约束单独用无效，
必须 prompt + 出口强校验两道一起上」**。

AtomCode 的 8 个事件里**没有任何一个发生在"助手正文写完"这个点上**：
PostToolUse 能改的是 `updatedToolOutput`（工具返回），不是模型的回答正文；
Stop 拿到的是 `session_id` / `transcript_path` / `stop_reason`，那时回答早已流给用户。
所以 hook 这一层**只搬得动提示词侧那一道**，`hca-lang` 做的就是这一道。

出口那一道的正确落点是 **Web 转发层**（它本来就坐在 SSE 流上，M3 已经有 `TurnProjector`）：
在文本 part 终态时调一次 `/api/internal/lang/guard`。这个端点在 1.2.0 的 api 镜像里
**已经存在**（`apps/api/app/routers/internal_tools.py:458`），api 侧零改动。
**M4 没有做这件事**（任务书第 1 条只要求 hook 侧），记成 **P1-21** 进待办池。

现状的风险有多大：M2 的 A/B 评测里 C1「全中文」这一项**两边 23 次全部满分**
（`docs/开发文档/M2-AB对比报告.md`），说明人设 + 提示词约束在当前模型上够用；
原事故那种「英文 SKILL 带偏整段回答」的场景，本发行版的 6 个技能正文**都是中文**，
触发条件也不成立。所以这是一个"已知缺一道保险"，不是"现在就在出英文"。

## 6. auth 为什么上移到 Web 层

opencode 那套里，"谁在用"这件事由 opencode 服务端的登录态承担，插件只是顺手把
`_hermes_user_id` 塞进 MCP 参数。AtomCode daemon 不是这个形态：

* daemon 的鉴权只有一个 **Bearer token**，是它自己启动时生成的（`/run/hca/daemon-token`），
  **没有用户概念**；`/live` 也不支持按请求指定工作区（待办池 P0-2）。
* 本发行版是 **P0 单工作区**（已拍板决策 5）：一个工作区一个 daemon。
* 所以"谁是谁"必须在前面挡住：**web（Next.js）负责登录与会话归属，api 负责授权**，
  daemon 只在 compose 内网上听、不发布到宿主（总控端口表），
  任何请求到 daemon 之前都已经过了 web/api 那一关。

hook 这一层因此**不做 auth**，只做一件相关的事：把 web 记下来的会话归属
（`chat_session_owner`，服务端权威、浏览器改不了）翻成 MCP 要的 `_hermes_user_id`。
查不到就**不注入**，让下游 MCP 自己报「缺用户身份」—— 比默默用别人的账本强得多。

## 7. 怎么测

### 7.1 `atomcode hooks test`（跑的是**真实运行时**的那个执行器）

```bash
# 在 daemon 容器里、工作区目录下跑
docker exec -w /workspace hca-daemon atomcode hooks list
docker exec -w /workspace hca-daemon atomcode hooks test guard.sh      # 按 command 子串
docker exec -w /workspace hca-daemon atomcode hooks test Stop          # 按事件名
```

`run_hook_for_test` 复用的就是 live 中间件那个 `run_command_hook`
（`cc_hooks.rs:360`），所以它观察到的与真实一轮一致。**但它的 payload 是固定样例**：
`PreToolUse` 恒是 `bash` + `echo hello`、`Stop` 的 `transcript_path` 恒为 `null`。
一键跑全部 8 条并留档：

```bash
bash tools/hooks/hooks-test.sh            # 输出存 docs/evidence/M4/hooks-test/
```

### 7.2 自定义 payload 的可复现用例

固定样例覆盖不到的（deny 的理由、身份注入、耗时配对、预算拦截），用：

```bash
bash tools/hooks/run-hook-cases.sh        # 21 条用例，逐条 期望/实得 对照
python3 -m pytest tools/tests/test_hooks_m4.py -q     # 33 条单测（开发机上就能跑）
```

## 8. 加一个新 hook 要做什么

1. `distro/workspace-template/.hooks/` 下放 `你的.sh` + `你的.py`（照抄 `lang.sh` 的三行）；
2. `.hooks.json` 里加一条注册（**严格 JSON**；名字决定 PreToolUse 的排序）；
3. 需要开关就加一个 `HCA_*` 环境变量，并同时写进 `deploy/env.example`
   与 `deploy/docker-compose.yml` 的 daemon `environment`（否则容器里读不到）；
4. 写单测（`tools/tests/`）+ 往 `tools/hooks/cases/` 加可复现用例；
5. `deploy/up.sh --rebuild` 或 `docker compose up -d --build daemon`，
   再 `atomcode hooks list` 确认条数对得上 —— **条数不对就是 JSON 写坏了**。
