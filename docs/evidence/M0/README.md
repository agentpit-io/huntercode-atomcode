# M0 实测原始输出

所有文件都是 2026-09-22（上海时间）在测试机 hunter-test-01（34.133.8.3）上抓到的原始输出，
未经编辑。复现脚本在仓库 `tools/probe/`。

## 真实模型（hunter-chat / Gemini 3.8 Flash）驱动

| 文件 | 内容 |
|---|---|
| `t1-text.sse` | `/chat` 纯文本对话，7.51 秒 |
| `t2-bash.sse` | `/chat` 工具调用（bash 跑 `python3 -c "print(1+1)"`）+ artifact 事件，18.45 秒 |
| `t3-mcp.sse` | `/chat` 上让模型查 600519 —— **MCP 工具没挂上**，模型退而用 bash 找 akshare，最后撞上上游连续空响应 |
| `t3b-tools.sse` | `/chat` 上模型自述可用工具，**36 个，无 `mcp__*`** |
| `t3c-1.sse` / `t3c-2.sse` | 同一会话连续两轮再问一次工具列表，**两轮都没有 MCP 工具** |
| `t4-live.sse` | `/live` 上模型自述可用工具，**39 个，含 3 个 `mcp__akshare__*`** |
| `t5-mcp600519.sse` | `/live` 上查 600519 —— 撞上额度耗尽 `账户余额不足（HTTP 402）` |
| `p-build.sse` | `/chat` build 档的第一版探针（工具参数名写错成 `path`，因此触发了权限弹窗——正好说明"参数解析不出路径就弹"） |

## stub provider 驱动（见 M0 报告 §10）

工具调用序列由本地 stub 确定性发出；**被测的是 AtomCode 运行时的行为，结果数字都是真的**。

| 文件 | 内容 |
|---|---|
| `perm-build.sse` / `perm-accept_edits.sse` / `perm-plan.sse` / `perm-bypass.sse` | 四档权限 × 「读文件 → bash → 写工作区内文件 → MCP 调用」 |
| `perm3-build.sse` / `perm3-accept_edits.sse` / `perm3-plan.sse` / `perm3-bypass.sse` | 四档权限 × 「写工作区外 → 写敏感路径 `.env` → 越界 bash」，弹权限一律回 deny |
| `hook-bypass.sse` | `bypass` 档下 PreToolUse hook 的 deny 依然生效 |
| `hook-input.jsonl` / `hook-post.jsonl` | hook 真实收到的 stdin JSON（PreToolUse / PostToolUse） |
| `ms-ws1.sse` / `ms-ws2.sse` | 两个工作目录并发对话，无串话 |
| `live-mcp2.sse` | `/live` 上 MCP 调用弹权限 → 放行 → 东方财富源连接被拒 |
| `live-mcp4-1.sse` | 配 `autoApprove` 后不再弹权限 |
| `live-mcp-ok.sse` | **端到端成功**：`/live` → `mcp__akshare__akshare_call` → 新浪源 → 600519 真实 5 日行情，1453 ms |
| `stub.jsonl` | stub 收到的每次请求：轮次、消息数、工具数、工具名（`n_tools` 36 vs 39 就出自这里） |

## 不经 LLM 的直连探针

| 文件 | 内容 |
|---|---|
| `mcp-stdio-probe-重测.txt` | 直连 akshare-mcp stdio JSON-RPC：initialize / tools/list / akshare_search / akshare_signature / akshare_call |
| `mcp-多上游可达性.txt` | 同一台测试机上，东方财富 / 新浪 / 腾讯三个上游的可达性对比 —— 新浪通，东方财富被拒 |
| `daemon2.log` | daemon 启动日志（含它自己打印的端点清单） |
