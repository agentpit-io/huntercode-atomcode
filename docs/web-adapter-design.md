# HunterCode 网页接入 AtomCode 底座 · 适配层设计（M3 / 计划 v0.2 TP-06 方案 B）

| 项目 | 内容 |
|---|---|
| 对应计划 | 开发计划 v0.2 §6「TP-06 界面接入」**方案 B**（总控已拍板决策 1） |
| 目标 | 一套前端（hunter-community 的 Next.js `apps/web`）两个底座：`AGENT_BACKEND=opencode` 走原来的 opencode，`AGENT_BACKEND=atomcode` 走 AtomCode daemon |
| 硬约束 | **不改 AtomCode 内核**；**前端尽量不改**，必须改的集中、最小；不 mock、数字实测 |
| 依据 | `docs/daemon-api.md`（M0 实测手册）、`docs/开发文档/M0-预研结论.md`、`M1/M2 成果与测试报告`、上游源码 `atomcode-upstream/`（只读核对） |
| 写作时间 | 2026-09-22（上海） |

---

## 0. 一页结论

1. **接得上，但只能接 `/live`，不能接 `/chat`**：`/chat` 不挂载 MCP 工具（M0 §5.3 实测，已于本轮向上游提 issue [#1583](https://gitcode.com/atomgit_atomcode/atomcode/issues/1583)）。投研场景没有 MCP 等于没有行情，所以适配层的对话通道**只有 `/live` 一条**。
2. `/live` 与 opencode 的形态差别有三处是结构性的，适配层必须自己补：
   - **`/live` 是 daemon 全局单例**：一个工作目录、同一时刻只绑定一个会话。→ 适配层做 **LiveHub（单例 + 回合串行锁）**。
   - **`POST /live/message` 立刻返回**（`{"accepted":true,...}`），而前端 `sendMessage` 是 `await` 到整轮结束才解锁输入框。→ 适配层**把 POST 挂住**，等到 `state{running:false}` 才回。
   - **AtomCode 没有「消息 / part」这层模型**，只有事件流与整段快照。→ 适配层自己生成 `messageID` / `partID`，把 `text` / `tool_start` / `tool_result` 投影成前端认识的 `message.updated` / `message.part.updated` / `message.part.delta`。
3. **切会话不要用 `POST /live/switch_session`**。本轮读源码得到一条 M1/M2 没有的结论：`GET /live?session_id=X` 走的是 `ensure_headless_runtime` → `bind_after_mcp_ready(handle.wait_mcp_ready(...))`，**会等 MCP 就绪**；而 `/live/switch_session` 走 `resume_session_with_lease`，**不等**——那正是待办池 P0-10（切完会话 MCP 工具全部消失而 `/mcp/status` 仍显示 connected）的根因。所以适配层**用「带 `session_id` 重连 `/live`」来切会话**，从根上绕开 P0-10；并保留「`/mcp/status` 复核 + 必要时 `POST /mcp/reload` 等到没有 `connecting`」作为第二道保险。
4. 前端一共只用 `/api/opencode` 下的 **14 个端点 + 1 条 SSE**（§1 逐条列出），全部能映射，**没有映射不了的**。有 4 条是语义近似而非等价（`/agent`、`/config/providers`、`session.title`、`abort`），处理方式与代价见 §3.6 与 §7。
5. **前端只改 1 处**（`ToolCallCard` 的工具名归一，加 `mcp__{server}__{tool}` → `{server}_{tool}`），其余零改动。改动落点与理由见 §6。
6. 原 opencode 四个插件的职责去向见 §5：`hunter-auth` → BFF 已有的会话归属登记；**`hunter-mcp-context` → `guard.py`（PreToolUse hook）调 api 已有的 `GET /api/internal/session/{id}/user`**（该端点在 hunter-community 1.2.0 镜像里已经有，**不需要改 api**）；`hunter-guard` → llm-shim 的 schema 清洗；`hunter-lang` → 靠 `.atomcode.md` 人设（M2 实测 C1「全中文」23 次全满分）。
7. `permission_request` / `user_input_request` 的策略见 §8：**默认不弹窗，自动 deny 并把理由作为可见提示插进消息流**；真正的硬约束在 `guard.py` hook（M0 §7.4 实测「hook 压得住 bypass」）。`user_input_request` 在本发行版被 `ATOMCODE_REQUEST_USER_INPUT=0` 关掉，收到即 deny 并记日志。

---

## 1. 前端依赖的 opencode 接口与事件清单（逐条盘点）

盘点方法：`apps/web/app/chat/lib/opencodeClient.ts` 与 `useSSE.ts` 是前端**唯一**访问 `/api/opencode/*` 的两个文件（`grep -rn "api/opencode" apps/web` 只命中这两处 + 两处注释），逐个函数抄下来。

### 1.1 HTTP 端点（14 条）

| # | 前端调用 | 方法 · 路径 | 前端拿它做什么 |
|---|---|---|---|
| 1 | `listSessions()` | `GET /session` | 左侧会话列表 |
| 2 | `createSession()` | `POST /session` `{title}` | 新建对话 |
| 3 | `getSession(id)` | `GET /session/{id}` | 会话标题 / 元数据 |
| 4 | `deleteSession(id)` | `DELETE /session/{id}` | 删除会话 |
| 5 | `renameSession(id,t)` | `PATCH /session/{id}` `{title}` | 自动/手动改标题 |
| 6 | `listMessages(id)` | `GET /session/{id}/message` | 刷新页面后恢复历史 |
| 7 | `sendMessage(...)` | `POST /session/{id}/message` | 发消息（**await 到整轮结束**） |
| 8 | `abortSession(id)` | `POST /session/{id}/abort` | 停止生成 |
| 9 | `currentModelKey()` | `GET /config` | 取 `model` 字段 `"provider/model"` |
| 10 | `listProviders()` / `defaultModelKey()` / `resolveModelKey()` | `GET /config/providers` | 模型选择器；校验 localStorage 里存的模型还有没有效 |
| 11 | `listAgents()` | `GET /agent` | agent 选择器 |
| 12 | `switchSessionAgent()` | `POST /api/session/{id}/agent` `{agent}` | 切 agent |
| 13 | `switchSessionModel()` | `POST /api/session/{id}/model` `{model:{providerID,modelID}}` | 切模型 |
| 14 | `useOpencodeSSE()` | `GET /event?token=…` | **全局** SSE 事件流 |

> 注：`GET /skills`（能力库）、`/api/chat/kpred/*`（Kronos 预测）、`/api/chat/debate/*`（多专家辩论）、`/api/chat/artifact/*`（发布）走的是 **`/api/*` → hermes-api**，不经过 `/api/opencode`，**与 agent 底座无关，换底座后原样可用**（见 §4.3）。

### 1.2 前端消费的 SSE 事件（3 种 + 1 种被忽略）

`reduceEvents()`（`ChatWorkspace.tsx:63`）是唯一的事件归约点，它只认三种：

| 事件 | 结构（前端实际读的字段） | 归约行为 |
|---|---|---|
| `message.updated` | `properties.info` = `{id, sessionID, role, time:{created}, parts?, error?}` | 按 `info.id` 覆盖/创建消息 |
| `message.part.updated` | `properties.part` = `{id, type, messageID, sessionID, …}` | 按 `part.id`（tool 按 `callID`）替换或追加 part |
| `message.part.delta` | `properties` = `{messageID, partID, field, delta}` | 往已存在的 part 的某个字段上**追加**字符串；**part 不存在就丢弃** |

另外 `useSSE.ts` 会按 `properties.sessionID || properties.part.sessionID` 过滤非当前会话的事件，取不到 sessionID 的事件（`server.connected` / 心跳）放行。BFF 侧 `sseOwnershipFilter` 还会按 `properties.info.sessionID` 再过滤一次归属。

**结论：适配层只需要产出这三种事件**，形状对齐即可，不必仿造 opencode 的其余 40 多种事件。

### 1.3 前端消费的 part 形状

```ts
// text
{ id, type:'text', messageID, sessionID, text }
// tool
{ id, type:'tool', messageID, sessionID, tool, callID,
  state:{ status:'pending'|'running'|'completed'|'error', input, output, time:{start,end} } }
```

- 富卡片：`ToolCallCard` 用 `part.tool` 去掉 `watchlist_`/`portfolio_`/`uzi_` 前缀后匹配 7 个富卡片；`state.output` 必须是**能 `JSON.parse` 的字符串**。
- `getAssistantText()`（报告预览、发布、记忆浓缩都用它）取的是**最后一个 tool part 之后**的 text part。所以适配层必须在每次工具调用之后**新开一个 text part**，不能把整轮文本拼在一个 part 里——否则工具前的「正在调用…」会被算进最终报告。

---

## 2. AtomCode daemon 侧的对应实现（逐条映射）

daemon 的事实以 `docs/daemon-api.md` 为准，本轮又补测了 4 条（§2.3 标「本轮实测」）。

### 2.1 逐条映射表

| # | opencode 端点 | AtomCode 实现 | 等价性 |
|---|---|---|---|
| 1 | `GET /session` | **不直接用 `GET /sessions`**：以 hermes-api 的 `GET /api/chat/sessions`（归属表）为准，再用 daemon `GET /sessions` 补 `updated_at`/`message_count` | 近似 → §2.2① |
| 2 | `POST /session` | `POST /sessions` → `{id,name,working_dir,project_hash,created_at}`；随后 `PATCH /projects/{hash}/sessions/{id}/rename` 写标题 | 等价 |
| 3 | `GET /session/{id}` | `GET /projects/{hash}/sessions/{id}`（`hash` 由 `GET /project` 缓存） | 等价 |
| 4 | `DELETE /session/{id}` | `DELETE /projects/{hash}/sessions/{id}` | 等价 |
| 5 | `PATCH /session/{id}` | `PATCH /projects/{hash}/sessions/{id}/rename` `{name}` | 等价 |
| 6 | `GET /session/{id}/message` | `GET /projects/{hash}/sessions/{id}` 的 `messages[]` → 投影成 opencode Message[] | 等价 → §2.4 |
| 7 | `POST /session/{id}/message` | LiveHub：绑定会话 → `POST /live/message` → 等 `state{running:false}` | 需要适配层补齐 → §3 |
| 8 | `POST /session/{id}/abort` | `POST /live/stop`（无 body，返回 `{"accepted":bool}`） | 等价 |
| 9 | `GET /config` | `GET /config` 的 `default_provider` + 该 provider 的 `model` → `{"model":"hunter/hunter-chat"}` | 近似（字段名不同，值语义一致） |
| 10 | `GET /config/providers` | `GET /models` → `{providers:[{id,name,models:{…}}], default:{provider:model}}` | 近似 → §2.2② |
| 11 | `GET /agent` | AtomCode **没有 agent 概念**，有「权限档」。映射成两个伪 agent：`build` / `plan` | 语义近似 → §7 U-10 |
| 12 | `POST /api/session/{id}/agent` | `POST /live/mode` `{mode}`（`build`/`accept_edits`/`plan`/`bypass`） | 语义近似（**全局**而非按会话） |
| 13 | `POST /api/session/{id}/model` | `POST /live/provider` `{provider}` | 语义近似（AtomCode 一个 provider 一个模型） |
| 14 | `GET /event` | LiveHub 扇出：上游 `GET /live` 一条连接 → 每个浏览器一条转换后的 SSE | 需要适配层补齐 → §3 |

**没有映射不了的条目。** 4 条「近似」的具体差异与代价见 §7。

### 2.2 两处「近似」的展开

**① 会话列表为什么不直接用 `GET /sessions`**

本轮实测（测试机 `hca-daemon`，2026-09-22 08:27 上海）：

```
POST /sessions                    → {"id":"9f5a17d8-…","name":"session-9f5a17d8-…",…}
GET  /sessions                    → 新建的这条**不在列表里**
PATCH /projects/{hash}/sessions/{id}/rename {"name":"M3 改名测试"} → 200 "Session … renamed to 'M3 改名测试'"
GET  /projects/{hash}/sessions/{id} → {"name":"M3 改名测试",…,"message_count":0}
```

即 **`GET /sessions` 只列有消息的会话**（`message_count>0`）。而前端「新建对话」之后立刻要在左栏看到它。同时 `POST /sessions` **忽略请求体里的 `name`**，必须再调一次 rename。

所以列表以 **hermes-api 的归属表为准**（BFF 本来就在建会话时往那里登记，标题也同步在那儿），daemon 只提供 `updated_at` / `message_count` 作补充。这同时顺手解决了 opencode 版靠「daemon 全量列表 + 按归属过滤」带来的跨用户泄露面——AtomCode 版**根本不把 daemon 的全量列表发给浏览器**。

**② `/config/providers` 的形状**

daemon `GET /models` 实测返回：

```json
[{"provider":"hunter","model":"hunter-chat","provider_type":"openai","is_default":true,
  "effort_applicable":false,"reasoning_effort":null,
  "effort_levels":["low","medium","high","xhigh","max"]}]
```

按 provider 聚合成前端要的 `{providers:[{id,name,models:{…}}], default:{hunter:"hunter-chat"}}`。
前端的 `PLACEHOLDER_MODEL`（`hunter-unconfigured`）过滤逻辑对 AtomCode 侧无害（那个名字不会出现）。

### 2.3 本轮补测的 4 条 daemon 事实

| # | 事实 | 怎么测的 |
|---|---|---|
| 1 | `POST /sessions` 忽略 `name`，自动命名 `session-<uuid>`；`message_count=0` 的会话**不出现在 `GET /sessions`** | 容器内 `python3` 直连 daemon，建 → 列 → 改名 → 查 → 删，见 §2.2① |
| 2 | `PATCH /projects/{hash}/sessions/{id}/rename` body 字段是 `name`，200 返回一句话字符串 | 同上（上游 `lib.rs::RenameRequest{name}` 交叉核对） |
| 3 | `GET /projects/{hash}/sessions/{id}` 的 `messages[]` **带工具调用**：`assistant` 消息上有 `tool_calls:[{id,name,arguments,display}]`，紧随其后的 `tool` 消息上有 `tool_result:{call_id,success,summary,line_count}`，**完整输出在该消息的 `content` 里**（`summary` 是截断的） | 取测试机上一条真实的 7 条消息会话逐字段打印 |
| 4 | **`GET /live?session_id=X` 会等 MCP 就绪，`POST /live/switch_session` 不会** | 读上游 `native_live.rs`：`ensure_headless_runtime` 在 `requested_session_id` 与当前绑定不同时**拆掉旧 runtime 重建**，重建路径上有 `bind_after_mcp_ready(handle.wait_mcp_ready(mcp::CONNECT_TIMEOUT))`（30 秒上限）；而 `live_switch_session` → `resume_session` → `hub().resume_session_with_lease()` 这条路上没有 `wait_mcp_ready` |

> 第 4 条是本轮最有价值的一条：它把待办池 **P0-10 从「每次切会话都要 reload + 轮询」降级成「换个切法就没有」**。
> 代价：换会话时 runtime 会被重建，9 个 MCP 要重连（M1 实测低负载下一轮 reload 即 9/9，冷启动单个 2.65–5.88 秒）。上限由上游写死的 `CONNECT_TIMEOUT=30s` 决定，**2 核机器高负载下有超时风险**（待办池 B-9 同源），所以 §3.3 仍保留第二道保险。

### 2.4 历史消息的投影规则

daemon 侧一条真实会话（测试机 `e06d2d59-…`，7 条消息）的结构：

| idx | role | 带的字段 | 投影去向 |
|---|---|---|---|
| 0–2 | `system` | `content` | **丢弃**（系统提示不进用户界面） |
| 3 | `user` | `content`, `created_at` | `Message{role:'user', parts:[text]}`，**text 要剥掉 `<hca-context>…</hca-context>`**（那是 `context.sh` hook 注入的日期/交易日上下文，不是用户打的字） |
| 4 | `assistant` | `content`, `tool_calls[]`, `created_at` | `Message{role:'assistant'}`，`content` → text part，`tool_calls[]` → 每个一个 tool part（`status` 先记 `running`） |
| 5 | `tool` | `content`, `tool_result{call_id,success,…}` | **不产出新消息**，按 `call_id` 回填上一条 assistant 的 tool part：`output=content`、`status=success?'completed':'error'` |
| 6 | `assistant` | `content`, `created_at` | 追加到同一条 assistant 消息的**新** text part（保证 `getAssistantText` 只取最后一次工具之后的正文） |

一个「用户问 → 助手调工具 → 助手作答」的回合因此投影成 **2 条消息**（1 user + 1 assistant），与 opencode 一致。

---

## 3. 适配层架构

### 3.1 位置与开关

```
浏览器 ── /api/opencode/*  ──►  Next.js BFF（apps/web/app/api/opencode/[...path]/route.ts）
                                  │
                                  ├── AGENT_BACKEND=opencode（默认）→ 现有转发，一行不改
                                  └── AGENT_BACKEND=atomcode       → lib/atomcode 适配层
                                          ├── rest.ts      会话 CRUD / 历史 / 配置
                                          ├── live-hub.ts  /live 单连接 + 回合串行锁 + 扇出
                                          ├── project.ts   ok
                                          └── events.ts    /live 事件 → opencode 事件（纯函数，可单测）
```

- 开关默认值 **`opencode`**：这样这份仓库里那套 hunter-community 的 compose（`docker-compose.yml`）拿去跑行为完全不变。
- 会话归属、用户身份、OCR、system prompt 注入、`skillKey` 注入这些**与底座无关的逻辑全部复用**，两条分支共用同一段代码（见 §4.1）。

### 3.2 LiveHub：为什么必须有

`/live` 有三个性质与「多标签页 + 多会话」的网页是冲突的：

1. **一条 daemon 只有一个 `/live` 运行时**，绑定一个会话。两个浏览器标签页各开一条 `/live` 连上去，拿到的是同一个 runtime 的同一条事件流。
2. **`POST /live/message` 立刻返回**，不等回合结束。
3. **换会话要重建 runtime**，而重建时如果正在回合中，上游直接返回 `cannot replace an active live runtime`。

所以适配层在 web 容器进程里放一个单例（挂 `globalThis`，避免 Next 的模块重载造出第二个）：

```
LiveHub
├── upstream: EventSource-like（GET /live?session_id=<bound>）  —— 永远最多一条
├── bound: string | null                                        —— 当前绑定的会话
├── subscribers: Set<{sessionId, push(event)}>                  —— 每个浏览器 SSE 连接一个
├── turnQueue: Promise 链                                       —— 回合串行锁
└── state: 每个会话的 turn 投影状态（messageId / partSeq / 累积文本）
```

### 3.3 切会话的完整步骤（绕开 P0-10）

```
ensureBound(sid):
  if bound === sid: return
  await turnLock                       # 回合中不许切（上游会拒）
  close(upstream)
  upstream = GET /live?session_id=sid  # ← 这条路 wait_mcp_ready，是 P0-10 的解
  await first 'snapshot' frame         # 上游此时已经等过 MCP
  # 第二道保险：上游 wait_mcp_ready 有 30 秒上限，2 核机器高负载时可能没等满
  st = GET /mcp/status
  if st 里有 status != 'connected':
      POST /mcp/reload; 轮询到没有 'connecting'（上限 150s）
      再查一次，仍不齐就**照常服务但记一条 warning 事件**（不谎报健康）
  bound = sid
```

**为什么不直接 `POST /live/switch_session`**：见 §2.3 第 4 条。第二道保险保留的是 M1 已验证过的绕法，只是从「每次必做」降成「兜底才做」。

### 3.4 一个回合的完整时序

```
POST /api/opencode/session/{sid}/message
  │
  ├─ BFF 公共段：验归属 → image OCR → 注入 system / skillKey（与 opencode 分支同一段代码）
  │
  └─ LiveHub.runTurn(sid, text):
       await turnLock                       # 同一时刻只跑一个回合
       await ensureBound(sid)
       msgId = `asst_${sid}_${Date.now()}`
       emit message.updated(user 消息)       # 让乐观气泡换成真消息
       emit message.updated(assistant 空壳)
       POST /live/message {message, session_id: sid}
       ── 上游事件流边到边转换（§3.5），推给所有订阅了 sid 的浏览器 ──
       等 state{running:false}               # 或超时 / 连接断
       resolve POST                          # 前端这时才 setBusy(false)
```

**超时**：与现有 BFF 对齐，`maxDuration=600`、undici `headersTimeout=600s`。回合超过 600 秒时返回 `upstream_timeout`，并向事件流补一条 `message.updated`（带 `error`），不让前端卡在生成态。

### 3.5 事件转换表（`events.ts`，纯函数、可单测）

| `/live` 事件 | 产出的 opencode 事件 | 说明 |
|---|---|---|
| `snapshot` | —— | 只用于确认绑定成功；历史走 §2.4 的 HTTP 路径，不从这里灌 |
| `user` | `message.updated`（role=user） | 用它把乐观气泡 `tmp_user_*` 换成真消息；text 需剥 `<hca-context>` |
| `state{running:true}` | `message.updated`（assistant 空壳） | 建气泡 |
| `text{content}` | 首次 → `message.part.updated`（空 text part）<br>其后 → `message.part.delta{field:'text',delta}` | **必须先建 part 再发 delta**：前端 `reduceEvents` 对找不到 `partID` 的 delta 直接丢弃 |
| `tool_start{id,name,arguments}` | `message.part.updated`（tool part，`status:'running'`，`input` 为 `JSON.parse(arguments)`，`time.start`） | `name` 要过工具名归一（§3.7）；**同时关掉当前 text part**，下一段 `text` 另起新 part |
| `tool_result{id,name,output,success,duration_ms}` | `message.part.updated`（同一个 `callID`，`status:'completed'\|'error'`，`output`，`time.end`） | `output` 原样传字符串，富卡片自己 `JSON.parse` |
| `tool_output{chunk}` / `tool_progress` | `message.part.delta`（field=`output`） | `/live` 上没抓到过，按 `/chat` 的字段实现并用 `/chat` 样例做夹具 |
| `tokens{prompt,completion,total}` | `session.updated`（`info.tokens`） | 前端 Session 类型有这个字段；没有 UI 消费时无副作用 |
| `state{running:false, stop_reason, stats}` | `message.updated`（assistant 终态；`stop_reason` 非 `stopped` 时带 `error`） | 同时解开回合锁 |
| `error{message}` | `message.updated`（assistant 带 `error:{name:'ProviderError',data:{message}}`） | 前端 `modelError.ts` 会渲染成错误条 |
| `warning{message}` | `message.part.updated`（一个 text part，前缀 `> ⚠️ `） | 「模型返回空响应重试」这类必须让用户看见 |
| `permission_request` | 见 §8 | |
| `user_input_request` | 见 §8 | |
| `policy_intervention` | 同 `permission_request`，一律拒绝并显示理由 | |
| `steered` | —— | 丢弃（`/live` 的 steer 语义前端没有对应 UI） |
| `mode` / `reasoning_effort` / `SessionSwitched` / `SessionRenamed` | —— | 丢弃 |
| `artifact_start` / `artifact_content` / `artifact_end` | 见 §4.2 | `/live` 上从未出现过（本轮统计 14 次真实运行、0 次），按 `/chat` 语义实现并用 `/chat` 样例做夹具 |
| `: ping` / `: bye` 注释行 | —— | 心跳，不产出事件 |

### 3.6 id 生成规则

AtomCode 没有 message / part id，适配层自己造，规则必须**稳定且同一回合内可复现**：

| 对象 | id |
|---|---|
| assistant 消息 | `asst_{sid}_{turnStartMs}` |
| user 消息 | `user_{sid}_{turnStartMs}` |
| text part | `{msgId}:t{n}`（`n` 从 0 递增，每次工具调用后 +1） |
| tool part | `{msgId}:call:{callId}`（`callId` 来自 `tool_start.id`） |

历史投影（§2.4）用 `hist_{sid}_{created_at}` 作前缀，保证与实时回合不撞。

### 3.7 工具名归一

| 侧 | 形态 | 例 |
|---|---|---|
| AtomCode | `mcp__{server}__{tool}` | `mcp__watchlist__stock_quickview` |
| opencode（前端富卡片认的） | `{server}_{tool}` | `watchlist_stock_quickview` |

适配层把 `mcp__X__Y` 改写成 `X_Y`，七个富卡片全部对得上：

```
mcp__watchlist__stock_quickview   → watchlist_stock_quickview   → StockQuickviewCard
mcp__watchlist__stock_news        → watchlist_stock_news        → StockNewsCard
mcp__watchlist__watchlist_digest  → watchlist_watchlist_digest  → WatchlistDigestCard
mcp__portfolio__portfolio_rebalance → portfolio_portfolio_rebalance → PortfolioRebalanceCard
mcp__portfolio__portfolio_stress  → portfolio_portfolio_stress  → PortfolioStressCard
mcp__portfolio__update_risk_profile → portfolio_update_risk_profile → RiskProfileCard
mcp__uzi__stock_deep_analysis     → uzi_stock_deep_analysis      → UziDeepAnalysisCard
```

内置工具（`bash` / `read_file` / `use_skill` / …）原样透传，走 `ToolCallCard` 的通用卡片。

> **前端为什么还要改一行**：归一在 BFF 做了之后前端本可以零改动，但 `ToolCallCard.normalizeToolName` 的前缀白名单只有三个（`watchlist_`/`portfolio_`/`uzi_`），一旦将来有人把 BFF 的归一关掉（或直连 daemon 调试），卡片就会静默退化成通用卡。所以**在前端也认一次 `mcp__X__Y`**，两边都能认，属于防御性的一行。详见 §6。

---

## 4. Artifact / 图表 / 报告怎么接到现有 ArtifactPanel

### 4.1 三条通路，各走各的

| 产物 | 通路 | 与 agent 底座的关系 |
|---|---|---|
| **Kronos 预测图** | 前端 `kpredClient.ts` → `POST /api/chat/kpred/start` + SSE → hermes-api 直接产出完整 HTML → `ChatWorkspace.htmlArtifacts` → `ArtifactPanel` iframe | **完全不经过 agent**。换底座后原样可用，**但刷新恢复需要补一张表**，见下方 ⚠ |
| **回测结果 / HTML 报告** | 模型在正文里写 ` ```html ` 围栏 → `reportDetect.extractHtmlBlock()` → `ArtifactPanel` 的 `artifactType:'html'` | 只要正文流对了就通 |
| **MCP 结构化结果**（行情 / 深度分析 / 组合） | `tool_result.output`（JSON 字符串）→ 富卡片；**通用卡片**（非富卡片、且 output > 200 字符）上有「在右侧查看」按钮 → `onOpenArtifact(part)` → `ArtifactPanel` 的 tool 模式看 INPUT/OUTPUT 原文 | 靠 §3.7 的工具名归一。<br>⚠ M3 实测修正：**7 张富卡片没有展开态、也没有「在右侧查看」入口**，进 ArtifactPanel 的只有通用卡片这一条路 |

所以**「Kronos 图在 ArtifactPanel 渲染」这件事在 AtomCode 版是原样成立的**，不需要 artifact_* 事件。这是本设计特意选择的：M2 的 23 次真实运行里 `/live` **一次 `artifact_*` 都没有发过**，把图表押在一个实测不发的事件上是不负责任的。

> ⚠ **M3 实测补充：当场渲染成立，刷新恢复原先不成立。**
> `apps/api/app/routers/chat_kpred.py` 把报告写进 `chat_kpred.reports`、刷新时从那里读，
> 但 `db/migrations/` 的 0001–0022 里**没有建这张表的迁移**。写与读都包在 `try/except` 里，
> 于是不报错、只降级：接口返回 `{"items":[],"count":0}`，用户看到的是「图刷新就没了」。
> 修法（不改 api 源码）：新增 `db/migrations/0023_chat_kpred_reports.sql`，
> compose 给 api 设 `HUNTER_MIGRATIONS_DIR` 指到仓库的 `db/migrations`
> （0001–0022 与镜像里那份 `sha256` 逐文件一致，挂载不改变已有行为）。见 M3 报告 §2.1。

### 4.2 `artifact_*` 事件仍然实现，但如实标注为「未在 `/live` 上观察到」

`/chat` 上确实会发（M0 `docs/evidence/M0/t2-bash.sse` 有完整三联）。适配层按下列规则转换，并用那份**真实 `/chat` 样例**做单测夹具：

| 事件 | 处理 |
|---|---|
| `artifact_start{id,artifact_type,language,title}` | 关掉当前 text part，新开一个 text part，写入围栏起始（`html`/`markdown`/`code` 分别对应 ` ```html ` / 无围栏 / ` ```{language} `） |
| `artifact_content{id,content}` | `message.part.delta` 往该 part 追加 |
| `artifact_end{id}` | 补上围栏结束，关掉该 part |

这样 html 类 artifact 自动落进 `extractHtmlBlock()` 的匹配范围，接到 ArtifactPanel 的 iframe；markdown 类落进 `isReportWorthy()` 的报告预览。**代价**：如果上游哪天让 `/live` 也发 artifact_*，而 artifact 内容又与 `text` 重复（`/chat` 样例里 artifact 就是 tool 输出的副本），正文会出现重复段落。所以适配层加一条保险：**同一回合内，若 artifact 内容已经逐字出现在已累积的文本里，就跳过**。

### 4.3 与 agent 底座无关、原样复用的前端能力

`/api/*`（非 `/api/opencode`）全部直连 hermes-api，换底座零影响：能力库 `/api/chat/skills`、用户画像 `/api/chat/system-prompt` 与记忆浓缩、多专家辩论 `/api/chat/debate/*`、Kronos `/api/chat/kpred/*`、发布 `/api/chat/artifact/*`、OCR `/api/internal/ocr/extract`、自选/持仓/筛选各页。

---

## 5. 用户身份与会话归属：原 opencode 插件的职责去哪了

opencode 版靠 4 个插件 + 2 个（M0 §2.2 修正过的真实清单是 4+2）。逐条交代：

| 插件 | 原职责 | AtomCode 版的承担者 | 状态 |
|---|---|---|---|
| `hunter-auth` | 从 `message.parts[0].metadata.hermes_token` 取 JWT，建立 session→user 映射 | **BFF**：建会话时 `POST /api/chat/sessions` 登记归属；每次发消息 `PATCH` 刷新 `last_used_at`。**这段逻辑两条分支共用，一行没改** | ✅ 已有 |
| `hunter-mcp-context` | 给 hunter 系 MCP 的工具参数注入 `_hermes_user_id` | **`guard.py`（PreToolUse hook）**：拿 hook 事件里的 `session_id`，调 **`GET /api/internal/session/{sid}/user`**（带 `X-Hunter-Internal-Key`）换 `user_id`，再用 `hookSpecificOutput.updatedInput` 注入。**该端点在 hunter-community 1.2.0 的 api 镜像里已经存在**（`apps/api/app/routers/internal_tools.py`，当初就是给这个插件写的），**所以 api 侧零改动** | 🔧 M3 实现 |
| `hunter-guard` | 给 Gemini 清洗 tool schema | `llm-shim` 的 `schema_clean.py`（M0 起就在 compose 里） | ✅ 已有 |
| —（新增） | **堵住「绕过 MCP 层」** | `guard.py` 对整条 bash 命令原文匹配 `/opt/hca`，命中即 deny。M3 实拍到模型 `read_file /opt/hca/mcp/watchlist_mcp.py` + `/opt/hca/venv-hunter/bin/python -c "sys.path.insert(…)"` 把数据取了出来 —— 数字是真的，但拿不到 `_hermes_user_id`、不进审计、前端收到的工具名是 `bash`，富卡片直接退化。见 M3 报告 §3.2 ⑤ | 🔧 M3 实现 |
| `hunter-lang` | 出口处的中文守卫 | `.atomcode.md` 人设（M2 §2.2）。**M2 实测 C1「全中文」两边 23 次全部满分**，所以 P0 不再加一层 HTTP 守卫；如果将来真出现英文泄漏，api 的 `POST /api/internal/lang/guard` 现成可用 | ✅ 靠人设 |
| `hunter-audit` | 写 `AUDIT.jsonl` | `audit.py`（PostToolUse hook，M2 已上） | ✅ 已有 |
| `hunter-budget` | Redis 预算 | 社区版默认就关（`HUNTER_BUDGET_ENABLED=false`），本发行版不启用 | ⬜ 不做 |

### 5.1 为什么身份必须按会话查，不能用容器环境变量

M2 的 `guard.py` 用的是容器级 `HUNTER_USER_ID`——那是**单用户评测环境**的简化。网页上线后每个登录用户是不同的 hermes user_id，用一个容器常量等于所有人共用一份持仓与自选，**这是数据串户，不是体验问题**。

改法（M3）：

```
guard.py 收到 tool_name.startswith("mcp__") 且 server ∈ {uzi,watchlist,portfolio,hunter_cap,hunter_user}
  uid = lookup(session_id)            # GET {HERMES_API_URL}/api/internal/session/{sid}/user
                                      #   带 X-Hunter-Internal-Key，超时 2s，按 session_id 缓存 60s
  uid = uid or HUNTER_USER_ID         # 单机 / 离线部署的回退（发行版单用户形态仍然走这条）
  if uid: updatedInput["_hermes_user_id"] = uid
```

安全性：`chat_session_owner` 表是**服务端权威**，浏览器改不了；hook 的 `session_id` 由 daemon 自己填，模型也伪造不了。查不到归属（比如运维在容器里手工发起的会话）就回退到环境变量，回退不到就**不注入**——让下游 MCP 自己报「缺用户身份」，而不是默默用别人的账本。

### 5.2 会话归属在 AtomCode 版的三道门（与 opencode 版等价）

1. **列表**：不再「取全量再过滤」，直接以归属表为准（§2.2①）。
2. **新建**：`POST /sessions` 成功后立刻 `POST /api/chat/sessions` 登记；登记失败返回 500 并提示重试（沿用现有逻辑，避免孤儿会话）。
3. **单会话操作**：`GET /api/chat/sessions/{id}/owned` 验一次，非本人 403。
4. **事件流**：`/api/opencode/event` 只推**该浏览器当前会话**且**归属本人**的事件；LiveHub 扇出时按 `sessionId` 分流，BFF 再用现成的 `sseOwnershipFilter` 兜一层。

> AtomCode 的会话 id 是 UUID，不带 `ses_` 前缀，所以现有 BFF 里 `sessionIdOf()` 的 `id.startsWith('ses_')` 判定在 atomcode 分支要换成 UUID 判定（§6 改动 2）。

---

## 6. 改动清单（前端 / BFF / 部署）

| # | 文件 | 改什么 | 为什么必须改 |
|---|---|---|---|
| 1 | `apps/web/app/api/opencode/[...path]/route.ts` | 加 `AGENT_BACKEND` 分叉；把「归属 / OCR / system 注入 / skillKey」抽成两条分支共用的函数 | 适配层的落点 |
| 2 | 同上 · `sessionIdOf()` | atomcode 分支认 UUID（现在只认 `ses_` 前缀） | 否则所有单会话操作都会被 `isUnrecognizedSessionPath` 的 deny-by-default 拒掉 |
| 3 | `apps/web/app/lib/atomcode/*.ts`（新增 4 个文件） | rest / live-hub / events / project | 适配层本体 |
| 4 | `apps/web/app/chat/components/ToolCallCard.tsx` | `normalizeToolName` 多认一种 `mcp__X__Y` | 防御性（§3.7 末尾）；**这是前端唯一的改动** |
| 5 | `distro/workspace-template/.hooks/guard.py` | `_hermes_user_id` 改为按 `session_id` 查 api | §5.1 数据串户 |
| 6 | `deploy/docker-compose.yml` | 加 web / api / postgres / redis | M3 任务 4 |
| 7 | `deploy/Dockerfile.web` + `deploy/up.sh` | 在测试机构建 web 镜像（flock + 内存检查） | 总控「开发机严禁跑 Next.js 构建」 |

前端 **1182 行的 `ChatWorkspace.tsx`、732 行的 `MessageList.tsx`、552 行的 `ArtifactPanel.tsx`、355 行的 `opencodeClient.ts`、92 行的 `useSSE.ts` 全部零改动**。

---

## 7. 4 条「语义近似」的代价与决定

| # | 差异 | 决定 | 记到哪 |
|---|---|---|---|
| 1 | **agent ≠ 权限档**：opencode 的 `build`/`plan` 是两套提示词与工具集；AtomCode 的 `build`/`plan` 是审批档。前端的 agent 选择器换到 AtomCode 上，用户以为在换「助手人格」，实际在换「能不能写文件」 | P0 **只暴露 `build` 一个**，选择器里隐藏其余档（AtomCode 版的人格由 `.atomcode.md` 决定，本来就只有一个）。M2 已实测 `plan` 档每轮注入「给个方案然后停下来等批准」，对投研是负作用 | 需用户决定 **U-10** |
| 2 | **切档 / 切 provider 是全局的**，不是按会话 | 单工作区形态下（已拍板决策 5）只有一个 daemon，全局即会话。多用户同时用网页时，A 切档会影响 B——所以 P0 直接不暴露（同上） | 同 U-10 |
| 3 | **`abort` 停的是「当前那一轮」**，`/live/stop` 没有 session 参数 | 因为回合是串行的（§3.2），当前那一轮就是发起 abort 的那个人的那一轮。适配层在 abort 前核对 `bound === sid`，不符就拒绝 | 已处理 |
| 4 | **`stop_reason=stopped` 分不出「做完了」和「做了一半」**（待办池 P0-7 / 阻塞 B-5） | 适配层不假装能分辨：`stop_reason` 非 `stopped` 时（`provider_error` / `max_rounds` 等）在气泡上挂 `error`；是 `stopped` 就正常收尾。**不自动续跑**——自动发「继续」会在用户看不见的地方烧 token | 保持 P0-7 开着 |

---

## 8. permission_request / user_input_request / policy_intervention 的处理策略

### 8.1 现状：什么时候会弹

| 场景 | `build` 档下的行为 | 本发行版实际会不会遇到 |
|---|---|---|
| MCP 工具调用 | 默认每次都弹 | **不会**：`.mcp.json` 给 9 个 server 全配了 `autoApprove`（M1/M2 实测 25 次 MCP 调用 0 次弹窗） |
| 工作区内写文件 / bash | 放行，不弹（M0 §4 实测矩阵） | 不会 |
| **工作区外**写文件 / bash | 弹 | **弹不到**：`guard.py` 先 deny（hook 优先于权限档，M0 §7.4） |
| 敏感路径（`.env` 之类） | 弹 | 同上 |

也就是说，在「`.mcp.json` 的 autoApprove + `guard.py` 的硬约束」这套组合下，**`permission_request` 是残余情形而不是常态**。

### 8.2 决定：自动 deny + 可见提示，不做弹窗

理由三条：

1. **前端没有权限 UI，加一个就违反「前端尽量不改」**，而它要处理的是一个已经被两道机制压掉的残余情形。
2. **自动 allow 是危险的**：能走到 permission 这一步，说明 `guard.py` 没拦住（比如一条 guard 判不出来的 bash），这时候替用户点「允许」等于把唯一一道人工关卡也拆了。
3. **挂着不回是最坏的**：M1 实测一次没人应答的审批把整轮挂了 300 秒，后续用例还被 `switch_session 被拒(active_turn)` 连带拖垮（待办池 P0-9）。

实现：

```
permission_request{tool_name, reason, call_id, arguments}
  → POST /live/permission {decision:'deny', tool_name}
  → 同时产出一个 tool part：status='error'
       output = "已自动拒绝：{tool_name}。原因：{reason}。
                 研究会话默认不执行需要人工批准的动作；
                 如果这是你要的操作，请改用数据工具，或在本机部署里放开该工具。"
```

用户在工具卡片上看得见「哪个工具被拒了、为什么」，而不是对着一个卡住的界面。

### 8.3 `user_input_request`

本发行版用 `ATOMCODE_REQUEST_USER_INPUT=0` 把这个工具关掉了（M2 §2.4 的 6 个开关之一，实测每轮省 3936 prompt token）。因此**不应该出现**。万一出现（上游换实现），适配层 `POST /live/user-input` 回一个空对象并在消息流里写一条 warning，不挂住回合。

### 8.4 `policy_intervention`

同 permission：回 `/live/policy-intervention` 拒绝，把 `code` 与可选动作作为 warning 文本显示。`/live` 上本轮从未观察到。

### 8.4b M3 实测：这三类事件一次都没触发

10 步端到端跑完，`permission_request` / `user_input_request` / `policy_intervention`
**一次都没出现** —— 与 §8.1 的判断一致（autoApprove + guard.py 把它压成了残余情形）。

「没触发」不等于「对」。所以把决策逻辑从 `live-hub.ts` 抽成纯函数
`permissionDenyPlan()`，用 4 条单测钉住：一律回 `deny`、带上 `tool_name`、
用户看得见「哪个工具被拒了、为什么」、**回 daemon 失败要如实写进提示**
（不许假装拒绝成功了）。另两类如实记为**未在真实链路上验证**（待办池 P1-19）。

### 8.5 P1 的做法（写进待办池，不在 M3 做）

把 `permission_request` 做成消息流里的一张**内联审批卡**（允许一次 / 始终允许 / 拒绝 → `POST /live/permission`），比弹窗更契合现有 UI；同时给「无人值守」保留 30 秒自动拒绝的兜底。

---

## 9. 已知限制（P0 形态下如实列出）

| # | 限制 | 根因 | 影响面 |
|---|---|---|---|
| 1 | **同一时刻只能有一个人在生成**（回合串行） | `/live` 是 daemon 全局单例运行时 | 单机私有化 / 个人桌面无影响；多人 Demo 站不适用（本来也不是 P0 目标） |
| 2 | **换会话有一次 runtime 重建 + MCP 重连**的延迟 | `ensure_headless_runtime` 在换 session 时重建（§2.3 第 4 条） | 秒级；2 核机器高负载时可能触到上游写死的 30 秒上限，此时走第二道保险 |
| 3 | **只有一个工作目录** | `/live` 没有 `working_dir`（待办池 P0-2） | 已拍板决策 5：P0 只做单工作区 |
| 4 | **MCP 返回超 16 KB 被截断**（待办池 P0-11） | 上游 `output_artifact.rs` 两个 `const` | 与网页无关，但网页上更显眼（用户看得到工具卡片里的 `output truncated`）。M3 在 `.atomcode.md` 里加硬规则 + akshare 侧压返回 |
| 5 | `reasoning` 事件恒为空 | hunter 网关不透传 `reasoning_content`（M0 §11.6） | 前端本来也没有思考过程 UI |

---

## 10. 测试计划（与 M3 任务书 §5 对应）· **已全部执行，结果见 M3 报告 §4**

| 层 | 用例 | 怎么算通过 |
|---|---|---|
| 单测 | `events.ts` 的转换：用 **M0/M2 抓到的真实 SSE 原文**做夹具（`docs/evidence/M0/t2-bash.sse` 的 `/chat` 三联 artifact、`docs/eval/raw/*.sse` 的 14 份 `/live` 流） | 逐事件断言产出的 opencode 事件形状；富卡片工具名归一；`getAssistantText` 的分段语义 |
| 单测 | 历史投影：拿测试机真实会话 JSON 做夹具 | 7 条 daemon 消息 → 2 条前端消息，tool part 的 output 完整 |
| 端到端 | Playwright（测试机真浏览器）：登录 → 新建对话 → 问会调 MCP 的问题 → 工具卡片 → 触发技能 → Kronos 图进 ArtifactPanel → 刷新恢复 → 中止 → SSE 断线重连 | 每步截图存 `docs/screenshots/M3/` |
| 部署 | `http://34.133.8.3:3200` 可用，单用户模式关闭，管理员账号可登录 | 实测 |

---

## 11. 需要用户决定

| # | 事项 | 建议 |
|---|---|---|
| **U-10** | agent / 模型选择器在 AtomCode 版是否隐藏（§7 差异 1、2） | 建议 P0 隐藏：AtomCode 侧只有一个 provider、一个模型、一种人格，选择器给用户的是「可选的错觉」；切权限档还会影响别人 |
| **U-11** | 多人同时使用网页（回合串行，§9 限制 1）是否接受 | 建议接受：P0 定位是单机私有化 / 个人桌面。多用户多 daemon 是 P1 待办 |
