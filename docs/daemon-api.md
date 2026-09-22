# AtomCode daemon HTTP/SSE API 实测手册（v5.1.0）

| 项 | 值 |
|---|---|
| 底座版本 | AtomCode **5.1.0**（二进制 sha256 `40d86fa3…8c8763`，见 `pins.lock`） |
| 实测环境 | hunter-test-01（34.133.8.3）· Ubuntu 24.04 · `~/hca/bin/atomcode daemon --port 13456 --idle-timeout 0 --no-telemetry` |
| 实测时间 | 2026-09-22（上海时间） |
| 依据 | 路由表来自上游源码 `crates/atomcode-daemon/src/lib.rs:6509-6650` 与 `live_api.rs`；**每个响应样例都是真实抓包**，原始输出在 `docs/evidence/M0/` |
| 与上游 README 的关系 | `crates/atomcode-daemon/README.md` 大体准确但有 3 处过时，见本文 §7 |

---

## 1. 启动与鉴权

### 1.1 启动参数（`atomcode daemon --help` 实测）

```
      --port <PORT>                  监听端口 [默认 13456]
      --client <CLIENT>              遥测用客户端标识（如 "vscode"）
      --idle-timeout <IDLE_TIMEOUT>  空闲自动退出秒数；0 关闭。环境变量
                                     ATOMCODE_DAEMON_IDLE_TIMEOUT 覆盖。默认 1800
      --no-auth                      关闭 API token 鉴权
      --no-telemetry                 本次启动关闭遥测
```

> ⚠️ **上游 README 写的 `--host <ip>` 在 5.1.0 的 `daemon` 子命令里不存在**（`--help` 无此项）。
> daemon 恒定绑 `127.0.0.1`。对我们是好事：compose 内网访问要走容器内回环或 sidecar，
> 不能直接 `--host 0.0.0.0` 暴露。已记入 `docs/questions-for-atomgit.md`。

### 1.2 token 文件

- 路径：`~/.atomcode/daemon-<port>.json`，权限 **0600**，daemon 是唯一写入者，退出时删除。
- 内容（实测，token 已打码）：

```json
{"pid":1682373,"port":13456,"token":"****"}
```

- 客户端用 `Authorization: Bearer <token>`。
- **实测：独立 daemon 也强制鉴权**（源码注释说独立模式 `enforce_token=false` 直接放行，实际不是）。
  无 token 访问 `/config` 返回 **HTTP 401**，空响应体；`/health` 不需要 token。

```
$ curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:13456/config
401
```

- 只有 `/health`、`/`（webui index）与静态资源是公开路由，其余全部在 token 中间件之后。
- CORS 只允许 loopback Origin（`http://localhost:*` / `http://127.0.0.1:*` / `http://[::1]:*` / `https://localhost:*`）。

### 1.3 环境变量

| 变量 | 实测结论 |
|---|---|
| `ATOMCODE_DAEMON_IDLE_TIMEOUT` | 生效，等价 `--idle-timeout` |
| `ATOMCODE_DAEMON_ENABLE_DANGEROUS_TOOLS` | **形同虚设**。源码里 `dangerous_tools_enabled()` 只在启动横幅打印一行警告（`lib.rs:1268` 定义、`lib.rs:6700` 唯一使用），没有任何地方拿它做工具门控。实测：**不设这个变量，`/chat` 也能跑 bash 和 write_file**（见 `docs/evidence/M0/t2-bash.sse`）。上游 README 的说法已过时 |
| `ATOMCODE_HOME` | 改 `~/.atomcode` 的位置（token 文件、config、sessions、mcp_trust 都跟着走） |

---

## 2. 完整端点清单

源码 `lib.rs:6509-6650` 的 axum 路由表，**共 65 条路径 / 69 个「方法 + 路径」组合**（另有 WebUI 静态资源的 SPA fallback）。标 ✅ 的本轮实测过。

### 2.1 公开（无需 token）

| 方法 | 路径 | 说明 | 实测 |
|---|---|---|---|
| GET | `/health` | 健康检查 | ✅ |
| GET | `/` | WebUI 首页（`?token=` → HttpOnly Cookie 交接） | — |
| — | `fallback` | WebUI 静态资源 / SPA 路由 | — |

### 2.2 会话与项目

| 方法 | 路径 | 说明 | 实测 |
|---|---|---|---|
| GET/POST | `/sessions` | 跨项目列会话（倒序，最多 50）/ 新建会话 | ✅ GET |
| GET | `/sessions/by-working-dir` | 按工作目录列会话 | — |
| GET | `/sessions/search?q=` | 按名字搜会话 | — |
| GET | `/sessions/resolve/:id` | 解析会话 id → 所属项目 | — |
| POST | `/sessions/:id/messages` | 追加消息 | — |
| GET | `/project` | 当前工作目录 | ✅ |
| POST | `/cd` | 切换工作目录 | ✅ |
| GET | `/projects` | 历史项目列表 | — |
| GET | `/projects/:hash/sessions` | 项目下会话 | — |
| GET/DELETE | `/projects/:hash/sessions/:id` | 会话详情 / 删除 | — |
| GET | `/projects/:hash/sessions/:id/transcript` | 会话逐字记录 | — |
| PATCH | `/projects/:hash/sessions/:id/rename` | 重命名 | — |
| POST | `/projects/:hash/sessions/:id/repair` | 检查/修复会话 | — |

### 2.3 对话

| 方法 | 路径 | 说明 | 实测 |
|---|---|---|---|
| GET | `/models` | 列出所有已配置 provider 提供的模型 | ✅ |
| POST | `/chat` | **SSE 流式对话**（body 限长，见源码 `CHAT_REQUEST_BODY_LIMIT_BYTES`） | ✅ |
| POST | `/chat/stop` | 停止指定会话 | — |
| GET | `/chat/active` | 正在跑的会话 | ✅ |
| POST | `/chat/permission` | 回复权限请求 | ✅ |
| POST | `/chat/user-input` | 回复模型的结构化提问 | — |
| GET/POST | `/approval_mode` | 读 / 设全局权限档 | ✅ GET |

### 2.4 Live 会话（`/live/*`，**MCP 只有这条路能用**，见 §5）

| 方法 | 路径 | 说明 | 实测 |
|---|---|---|---|
| GET | `/live` | 长连 SSE 事件流（`?session_id=` 可选） | ✅ |
| POST | `/live/message` | 发消息（无 `working_dir` 字段） | ✅ |
| POST | `/live/stop` · `/live/cancel` | 停止 / 取消当前回合 | — |
| POST | `/live/permission` | 回复权限（`decision`: allow / deny / always_allow / allow_persist） | ✅ |
| POST | `/live/user-input` | 回复结构化提问 | — |
| POST | `/live/policy-intervention` | 回复安全策略介入 | — |
| POST | `/live/provider` | 切 provider | ✅ |
| POST | `/live/mode` | 切权限档 | — |
| POST | `/live/goal/start` · `/arm` · `/stop` | 目标控制器 | — |
| POST | `/live/compact` | 压缩上下文 | — |
| POST | `/live/command` | 转发斜杠命令（**仅 TUI 内嵌 runtime 有效**，headless 恒回 `{"accepted":false}`，已实测） | ✅ |
| POST | `/live/mcp/trust` | 信任当前项目的 `.mcp.json` | ✅ |
| POST | `/live/switch_session` | 切会话 | — |
| POST | `/live/reasoning_effort` | 调推理强度 | — |

### 2.5 命令、技能、文件系统、MCP

| 方法 | 路径 | 说明 | 实测 |
|---|---|---|---|
| POST | `/command` | 内置命令，**固定 12 个**：`undo` `remember` `forget` `memory` `context` `compact` `whoami` `config` `diff` `status` `cost` `todo`。**不含技能斜杠命令** | ✅ whoami / context |
| GET | `/skills` | 列 user-invocable 技能，只回 `{name, description}` | ✅ |
| GET | `/fs/list` · `/fs/search` | 列目录 / 搜文件 | — |
| POST | `/fs/mkdir` · `/fs/open` | 建目录 / 打开 | — |
| GET | `/mcp/status` | MCP 连接状态 | ✅ |
| POST | `/mcp/reload` | 重载 MCP 配置并重连 | ✅ |

### 2.6 配置、Provider、认证、CodingPlan、其他

| 方法 | 路径 | 说明 | 实测 |
|---|---|---|---|
| GET | `/config` · POST `/config/reload` | 脱敏配置 / 重载 | ✅ |
| GET/POST | `/providers` | 列 / 建 provider | ✅ GET |
| POST | `/providers/discover-models` | 探测可用模型 | — |
| POST | `/provider-accounts/:account/models` | 批量建模型 | — |
| PATCH/DELETE | `/providers/:name` | 改 / 删 | — |
| POST | `/providers/:name/default` · PATCH `/providers/:name/thinking` | 设默认 / 改思考模式 | — |
| GET | `/auth/status` | AtomGit 登录态 | ✅ |
| POST | `/auth/login/start` · `/auth/login/:id/poll` · DELETE `/auth/login/:id` · POST `/auth/logout` | OAuth 流程 | — |
| POST | `/codingplan/setup` · GET `/codingplan/usage/summary` · `/usage/daily` | CodingPlan | — |
| GET | `/tunnel/status` | 远程访问探测 + 二维码 | ✅ |
| POST | `/shutdown` | 优雅退出 | ✅ |

---

## 3. 关键端点的请求 / 响应结构（真实抓取）

### 3.1 `GET /health`

```json
{"status":"ok","version":"5.1.0","service":"atomcode-daemon",
 "binary_hash":"40d86fa3e31980dbcdc8a6e5707c822f3953284ff76b31523789ca19988c8763",
 "instance_id":"b834dd4c-f14e-4ea1-b4f4-de015b556d8c",
 "capabilities":["goal","session_scoped_live"]}
```

> `binary_hash` 就是二进制自身的 sha256，与 `latest.json` 一致——**可以拿它做运行时版本校验**，不必再算一遍文件。

### 3.2 `POST /chat` 请求体（源码 `lib.rs:3357`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `message` | string | 是 | 用户消息 |
| `working_dir` | string | 否 | 本次回合的工作目录，**按请求生效**（与 `/live` 不同） |
| `provider` | string | 否 | provider 名，默认取配置里的默认 provider |
| `session_id` | string | 否 | 续接已有会话；不传则新建 |
| `request_id` | string | 否 | 浏览器侧取消用的相关 id（首轮就有，区别于 `session_id`） |
| `images` | `[{media_type, data}]` | 否 | base64 图片 |
| `approval_mode` | string | 否 | `build` / `accept_edits` / `plan` / `bypass`，**按请求覆盖**全局档 |

### 3.3 `/chat` SSE 事件类型（源码 `ChatEvent` 枚举，共 **22** 种）

| `type` | 字段 | 说明 | 本轮抓到 |
|---|---|---|---|
| `runtime_info` | `provider` `model` | 本次解析出的 provider/模型 | ✅ |
| `session_assigned` | `session_id` | 会话 id 落定（**在 provider 工作之前就发**，客户端后续一律用它） | ✅ |
| `tool_batch` | `calls[]` | 本轮助手消息里的全部工具调用 | ✗ |
| `text` | `content` | 文本增量 | ✅ |
| `reasoning` | `content` | 推理/思考增量 | ✗ **接 hunter 网关时恒为空**：开足 thinking（`hunter-deep` + `enabled:true`/`budget:10000`/`effort:high`）也一个事件没有，因为网关不透传 `delta.reasoning_content`。详见 M0 报告 §11.6 |
| `tool_start` | `id` `name` `arguments` | 工具调用开始 | ✅ |
| `tool_output` | `chunk` | 工具实时输出片段 | ✗ |
| `tool_progress` | `id` `progress` | 长任务临时进度（原位替换，不持久化） | ✗ |
| `tool_result` | `id` `name` `output` `success` `duration_ms` | 工具完成 | ✅ |
| `tokens` | `prompt` `completion` `total` | token 用量 | ✅ |
| `artifact_start` | `id` `artifact_type` `language` `title` | 制品开始（code / html / markdown） | ✅ |
| `artifact_content` | `id` `content` | 制品片段 | ✅ |
| `artifact_end` | `id` | 制品结束 | ✅ |
| `done` | `tokens` `tool_calls` `session_id` `stats` `stop_reason` `message` | 终态 | ✅ |
| `permission_request` | `session_id` `tool_name` `reason` `call_id` `arguments` | 需要审批，**阻塞直到回复或回合取消** | ✅ |
| `user_input_request` | `session_id` `request_id` + 扁平化 payload | 模型结构化提问 | ✗ |
| `policy_intervention` | `intervention_id` `code` `actions[]` | 安全策略中断，只给安全的恢复动作 | ✗ |
| `stopped` | — | 用户停止 | ✗ |
| `error` | `message` | 错误 | ✅ |
| `warning` | `message` | 非致命提示（如"已压缩上下文"、"模型空响应重试"） | ✅ |
| `persistence_warning` | `message` | 会话落盘失败，需在消息流之外展示 | ✗ |
| `rate_limited` | `reset_at_display` `reset_label` `secs_until_reset` `auto_resuming` `server_message` | 被限流 | ✗ |

心跳：服务端周期性发注释行 `: ping`，流结束前发 `: bye`。

#### 真实样例 A · 纯文本（`docs/evidence/M0/t1-text.sse`）

```
data: {"type":"runtime_info","provider":"hunter","model":"hunter-chat"}

data: {"type":"session_assigned","session_id":"353a8b9e-bcf0-4851-97a0-6173ad8eeed8"}

data: {"type":"text","content":"我是 AtomCode，一个由 AtomGit 开发并运行 hunter-"}

data: {"type":"text","content":"chat 模型的 AI 编程助手。"}

data: {"type":"tokens","prompt":16324,"completion":21,"total":16345}

data: {"type":"done","tokens":16345,"tool_calls":0,"session_id":"353a8b9e-…","stats":{"duration_ms":7416,"rounds":1,"tool_calls":0,"prompt_tokens":16324,"completion_tokens":21,"cached_tokens":0},"stop_reason":"stopped"}

: bye
```

> **注意 `prompt` = 16324**：这是系统提示 + 36 个工具 schema 的固定开销，每轮都要付。

#### 真实样例 B · 工具调用 + artifact（`docs/evidence/M0/t2-bash.sse`）

```
data: {"type":"tool_start","id":"call_1943267","name":"bash","arguments":"{\"command\":\"python3 -c \\\"print(1+1)\\\"\"}"}

data: {"type":"tool_result","id":"call_1943267","name":"bash","output":"2\n","success":true,"duration_ms":41}

data: {"type":"text","content":"输出"}
data: {"type":"text","content":"为：\n\n"}
data: {"type":"artifact_start","id":"artifact_1","artifact_type":"code","language":"","title":null}
data: {"type":"artifact_content","id":"artifact_1","content":"2\n"}
data: {"type":"artifact_end","id":"artifact_1"}

data: {"type":"done","tokens":0,"tool_calls":1,"session_id":"9f83cbd3-…","stats":{"duration_ms":18369,"rounds":2,"tool_calls":1,"prompt_tokens":16334,"completion_tokens":24,"cached_tokens":12154},"stop_reason":"stopped"}
```

#### 真实样例 C · 权限请求（`docs/evidence/M0/perm3-build.sse`）

```
data: {"type":"permission_request","session_id":"…","tool_name":"write_file","reason":"Requires approval","call_id":"call_stub_…","arguments":"{\"file_path\": \"/home/support/hca/outside/escape.txt\", \"content\": \"越界写入\\n\"}"}
```

回复：

```bash
curl -X POST http://127.0.0.1:13456/chat/permission \
  -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"session_id":"…","call_id":"call_stub_…","decision":"deny"}'
```

`decision` 取值：`allow`（AllowOnce）/ `always_allow`（本会话记住）/ 其他一律按 `deny` 处理。
**不回复就一直挂着**，直到回合被取消或 curl 超时。

#### 真实样例 D · provider 侧错误（`docs/evidence/M0/t3-mcp.sse`）

```
data: {"type":"warning","message":"模型返回空响应，1 秒后重试(1/5)..."}
…
data: {"type":"warning","message":"模型连续 5 次返回空响应（上游偶发，与上下文长度无关）。可直接重试，或稍后再试。"}

data: {"type":"done","tokens":0,"tool_calls":4,"session_id":"…","stop_reason":"provider_error","message":"模型连续 5 次返回空响应…"}
```

额度耗尽（HTTP 402）在 `/live` 上表现为：

```
data: {"type":"error","message":"账户余额不足（HTTP 402）","session_id":"…"}
data: {"type":"state","running":false,"stop_reason":"provider_error","stats":{…}}
```

### 3.4 `/live` 事件流

`/live` 的事件用的是另一套 wire 格式（`LiveWireEvent`），**每条都额外带 `session_id`**。
心跳同样是 `: ping`。

**实际抓到的 11 种**（M0/M2 的真实 SSE 抓包，`docs/evidence/`）：
`snapshot`（首帧，含完整系统提示与历史消息）、`user`、`state`
（`running` / `stop_reason` / `stats`）、`mode`、`reasoning_effort`、`text`、`tokens`、
`tool_start`、`tool_result`、`permission_request`、`error`。

**枚举里一共 28 种**（v5.1.0 源码，由 `tools/upstream_diff.sh` 抽取，
完整清单与每个变体的载荷字段见 `docs/upstream-diff/5.0.9__5.1.0.1-daemon-routes-sse.diff`
的左半边）。没抓到的那 17 种不是不存在，是本发行版的配置下发不出来或没触发到 ——
其中 `policy_intervention` / `user_input_request` 这两族的应答体我们**对着源码核过并实现了**
（待办池 P1-19，M5 查出 M3 那版写错会 422）。**别把"没抓到"当成"没有"**：
接第三方前端时要按 28 种做兜底（不认识的 type 忽略即可，不要抛错）。

`POST /live/message` 请求体：

| 字段 | 必填 | 说明 |
|---|---|---|
| `message` | 是 | 消息文本 |
| `client_input_id` | 否 | 浏览器生成的关联 id，只回显不进模型上下文 |
| `images` | 否 | 同 `/chat` |
| `provider` | 否 | 切 provider |
| `session_id` | 否 | 调用方当前会话 |

返回：`{"accepted":true,"disposition":"started"|"steered","generation":N,"turn_id":N,"provider":"…","provider_change_applied":bool}`

> **`/live` 没有 `working_dir` 字段**：它用的是 daemon 全局当前目录（`state.project.working_dir`，由 `POST /cd` 改）。
> 也就是说**一个 daemon 进程的 `/live` 只能服务一个工作目录**。多工作目录要么起多个 daemon，要么走 `/chat`。

---

## 4. 权限档（`approval_mode`）实测语义

四档 wire 值：`build`（默认）/ `accept_edits` / `plan` / `bypass`。
`GET /approval_mode` → `{"ok":true,"mode":"build"}`。
`/chat` 可按请求带 `approval_mode` 覆盖。

实测矩阵见 `docs/开发文档/M0-预研结论.md` §4，结论摘要：

| 目标 | build | accept_edits | plan | bypass |
|---|---|---|---|---|
| 读文件 | 放行 | 放行 | 放行 | 放行 |
| 工作区内 bash | 放行 | 放行 | **放行** | 放行 |
| 工作区外 bash | 弹权限 | 弹权限 | 弹权限 | 放行 |
| 工作区内写 | 放行（不弹） | 放行 | **拦截** | 放行 |
| 工作区外写 | 弹权限 | **放行（不弹）** | **拦截** | 放行 |
| 敏感路径写（如 `.env`） | 弹权限 | 弹权限 | **拦截** | 放行 |

拦截时 `tool_result.success=false`，`output` 形如
`blocked: plan mode is active — \`write_file\` would modify the workspace and is blocked. Only read-only tools are allowed: …`

---

## 5. MCP

### 5.1 配置

- 项目级 `.mcp.json`（工作目录根）+ 用户级 `~/.atomcode/mcp.json`，顶层键 `mcpServers`（兼容旧键 `servers`），支持 `//` 与 `/* */` 注释、不支持尾逗号。
- stdio：`command` / `args` / `env`；HTTP：`url` / `headers` / `auth`。两者都支持 `timeout_ms`（默认 30000）、`disabled`、`trust`、`autoApprove`（别名 `auto_approve`）。

### 5.2 项目信任门（必踩）

项目级 `.mcp.json` 在**未信任的项目里根本不连**：

```
$ curl -s -H "$A" http://127.0.0.1:13456/mcp/status
{"servers":[],"trusted":false,"blocked":["akshare"]}

$ curl -s -X POST -H "$A" http://127.0.0.1:13456/live/mcp/trust
{"ok":true,"trusted":true}

$ curl -s -H "$A" http://127.0.0.1:13456/mcp/status
{"servers":[{"name":"akshare","status":"connecting"}],"trusted":true,"blocked":[]}
```

信任记录落 `~/.atomcode/mcp_trust.json`（键是 project_hash），用户级配置不受此限。

### 5.3 ⚠️ `/chat` 不挂载 MCP 工具，只有 `/live` 挂载

**这是 M0 最重要的发现。** 在 `/mcp/status` 显示 `connected, tool_count=3` 的同一时刻：

| 通道 | 模型看到的工具数 | 含 `mcp__akshare__*` |
|---|---|---|
| `/chat` | 36 | **否** |
| `/live` | 39 | **是** |
| CLI headless（`atomcode -p`） | 39 | 是 |

`/chat` 上调用 MCP 工具直接返回
`unknown or unmounted tool: mcp__akshare__akshare_call`。

源码根因：MCP 连接是异步补挂的（`atomcode-coding/src/parts.rs:805`，"never await them on the
session candidate path"），只有调用 `wait_mcp_ready()` 的路径才会等它就绪 ——
`crates/atomcode-clix/src/code.rs:258`、`crates/atomcode-daemon/src/native_live.rs:458`、
`crates/atomcode-cli/src/main.rs:3018` 三处，**`/chat` 的 `process_chat_request` 不在其中**
（该函数的 `_mcp_cache` 参数带下划线前缀，注释明写"CodingRuntime builds its own MCP；
this per-project cache is warmed by the /context, /compact and /live paths, not the chat turn"）。

### 5.4 冷启动与审批

- 冷启动：`POST /mcp/reload` → `/mcp/status` 出现 `connected` 实测 **3.40 秒**（akshare-mcp，pandas/akshare 首次 import）。
  直连 stdio 的 `initialize` 往返实测 2.65–5.88 秒。
- MCP 工具默认**每次调用都弹权限**（`/live` 上是 `permission_request`）。
  在 `.mcp.json` 里配 `"autoApprove": ["akshare_call", …]` 或 `"trust": true` 之后不再弹（已实测）。
- `/live/permission` 的 `decision` 多一个 `allow_persist`：会把该工具写进项目 `.mcp.json` 的 `autoApprove`。

### 5.5 `/mcp/status` 跟随的是 daemon 全局目录

`/mcp/status` 读 `state.project.working_dir`（`lib.rs:5209`），不是按请求。`POST /cd` 之后才换：

```
cd ws2（未信任）→ {"servers":[{"name":"akshare","status":"connected","tool_count":3}],"trusted":false,"blocked":["akshare-ws2"]}
                    ↑ servers 里还是 ws1 的注册表（缓存回退），blocked 才是 ws2 的 —— 这一帧是自相矛盾的，前端要当心
cd ws2 + trust    → {"servers":[{"name":"akshare-ws2","status":"connecting"}],"trusted":true,"blocked":[]}
cd 回 ws1         → {"servers":[{"name":"akshare","status":"connected","tool_count":3}],"trusted":true,"blocked":[]}
```

MCP 注册表本身**按工作目录隔离**（`state.mcp_cache: HashMap<PathBuf, CachedMcpRegistry>`），隔离是真的；
只是 `/mcp/status` 这个只读视图的取值口径是全局当前目录。

---

## 6. 技能（`GET /skills`）

- 目录式：`<技能名>/SKILL.md`，YAML 头 `name` / `description` / `allowed-tools` / `user-invocable`。
- 搜索路径：项目与用户各若干个（含 `.atomcode/skills`、`.claude/skills`、`.agents/skills`）+ 已安装插件的技能目录。
- **实测：hunter-community 的 `hunter:` 扩展字段原样放进去不会导致解析失败**，技能正常被识别：

```
$ curl -s -H "$A" http://127.0.0.1:13456/skills
[{"name":"skills:risk_profile","description":"读/改风险偏好 + 现金 + 单票/HK 上限 · 供组合建议自动应用"}]
```

两个要点：
1. 名字带 `skills:` 前缀（来源目录的命名空间）。
2. **`/skills` 只回 `{name, description}` 两个字段**，`hunter:` 里的 `display_name` / `icon` /
   `category` / `prompt_tpl` / `needs_tools` **拿不到**。HunterCode 前端的技能卡片要这些字段，
   M1 必须自己读 `SKILL.md` 或由 api 侧提供。
3. 技能斜杠命令**不能**通过 `/command` 调（那里只有 12 个内置命令），`/live/command` 在 headless 下恒回
   `{"accepted":false}`。M1 的做法是把 `/技能名 参数` 当普通消息发给 `/live/message` 或 `/chat`。

---

## 7. Hook

- 配置：项目 `.hooks.json` + 全局 `~/.atomcode/hooks.json`。
  形状 `{"hooks": {"<名字>": {event, matcher?, command, timeout_ms?, disabled?}}}`。
- 事件**只有 8 个**（`cc_hooks.rs:55`）：`PreToolUse` `PostToolUse` `PostToolUseFailure`
  `SessionStart` `SessionEnd` `UserPromptSubmit` `Stop` `StopFailure`。
  PascalCase 与 snake_case 两种拼法都认。**只支持 shell，没有 webhook**。
- `matcher`：`None`/`"*"` = 全部；`"foo_*"` = 前缀；否则精确匹配。
- 默认超时 10000 ms。

### 7.1 真实的 hook stdin（本轮抓取，`docs/evidence/M0/hook-input.jsonl`）

PreToolUse：

```json
{
  "session_id": "c2422eb6-cd78-4481-a56a-a533982e67d4",
  "hook_event_name": "PreToolUse",
  "tool_name": "write_file",
  "tool_input": {"file_path": "hook-blocked.txt", "content": "不该写进来\n"},
  "cwd": "/home/support/hca/probe/ws1"
}
```

PostToolUse：

```json
{
  "session_id": "c2422eb6-cd78-4481-a56a-a533982e67d4",
  "hook_event_name": "PostToolUse",
  "tool_name": "bash",
  "tool_response": "hook-test\n",
  "cwd": "/home/support/hca/probe/ws1"
}
```

> `tool_input` 是**解析好的对象**（不是字符串），解析失败时才退化成原始字符串。

### 7.2 决策输出

hook 的 **stdout 最后一行 JSON** 是决策：

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"原因"}}
```

`permissionDecision` ∈ `allow` / `deny` / `ask`。也认旧写法 `{"decision":"block"}` / `{"action":"block"|"allow"|"modify"}`。
改参数用 `hookSpecificOutput.updatedInput` 或 `action:"modify"` + `args`。
多个 hook 折叠时**最严格的赢**（Deny > Ask > Allow > Proceed），遇到 Deny 立即短路。
另有退出码契约：**只有 exit 2 才拦**，且必须给出明确理由（空输出的 exit 2 视为脚本坏了，放行）。

### 7.3 `atomcode hooks test` 的坑

`atomcode hooks test <NAME>` 的 `<NAME>` **不是 `.hooks.json` 里的键名**，而是事件名或命令子串：

```
$ atomcode hooks test deny-write-file
❌ No hook matching 'deny-write-file' found.
Available hooks (test by event name or a command substring):
  🔹 PostToolUse      …/.hooks/audit.sh
  🔹 PreToolUse       …/.hooks/deny-write.sh

$ atomcode hooks test PreToolUse
🔧 Testing Hook (PreToolUse)
  Command: …/.hooks/deny-write.sh   Timeout: 5000 ms   Matcher: write_file
  Duration: 59.6ms   Status: ✅ SUCCESS (exit code 0)
```

而且它喂的是**固定样例 payload**（`session_id: "test-session-0000"`，`tool_name: "bash"`，
`tool_input: {"command":"echo hello"}`），**不按 matcher 生成**——所以上面那条 matcher 是
`write_file` 的 hook 在测试里收到的是 `bash`，回了 allow。**`hooks test` 只能验"脚本能跑、
输出合法"，验不了拦截逻辑，拦截必须走真实对话。**

### 7.4 hook 优先于权限档（实测）

在 `bypass` 档、工作区内写文件（本该完全放行）的情况下，PreToolUse hook 的 deny 依然生效：

```
发起 bash        → 结果 bash ok=True 'hook-test\n'
（write_file）   → 结果 ok=False 'blocked: HCA 研究模式禁止 write_file（M0 探针钩子）'
hook-blocked.txt: 未写
```

**hook 是唯一能压住 bypass 的硬约束**，`permissionDecisionReason` 原样透传到 `tool_result.output`。

---

## 8. 多会话与并发

- `/chat` 的 `working_dir` 按请求生效。两个会话分别指向 `ws1` / `ws2` 并发对话，**无串话**：

```
ms-ws1: session 46271578-…  read_file → '…ws1 专属标记 WS1MARK'  bash → '/home/support/hca/probe/ws1'
ms-ws2: session 350cd034-…  read_file → '…ws2 专属标记 WS2MARK'  bash → '/home/support/hca/probe/ws2'
```

- `/live` **只有一个工作目录**（见 §3.4）。
- MCP 注册表按工作目录缓存与隔离（`mcp_cache: HashMap<PathBuf, …>`），信任也是按 project_hash。

---

## 9. 与上游 README 的差异（已记入 `questions-for-atomgit.md`）

| # | README 说法 | 实测 |
|---|---|---|
| 1 | `atomcode daemon --host <ip>` 可绑任意 IP | 5.1.0 的 `daemon` 子命令**没有 `--host`**，恒定 127.0.0.1 |
| 2 | `ATOMCODE_DAEMON_ENABLE_DANGEROUS_TOOLS=1` 才启用 bash / 写文件 | 该变量**只打印一行警告**，不门控任何东西；不设也能用 |
| 3 | 可用工具表列了 12 个 | 实际模型侧可见 36 个（`/chat`）/ 39 个（`/live`，含 3 个 MCP）；README 没提 `task` `team` `todowrite` `memory` `recall` `ast_grep` `blast_radius` `code_review` `trace_*` `atomgit_*` `schedule_wakeup` 等 |
| 4 | 源码结构写 `main.rs # 主入口、路由定义` | 路由实际在 `lib.rs`（9445 行），另有 `live_api.rs` `live_hub.rs` `commands.rs` 等 |
| 5 | 独立 daemon 不强制 token | 实测强制，无 token 一律 401 |
