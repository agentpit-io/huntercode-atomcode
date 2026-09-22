# M3 证据

| 文件 | 来源 | 用途 |
|---|---|---|
| `session-detail-e06d2d59.json` | 测试机 `hca-daemon` 容器内 `GET /projects/385b9fe84c7077ea/sessions/e06d2d59-2ea4-4ce1-abc8-a89e040841aa`，2026-09-22 08:27 上海 | 历史消息投影的单测夹具（`apps/web/tests/atomcode.test.ts`）。这是 M2 的 q3 评测跑出来的一条真实会话：3 条 system + 1 条 user + assistant(tool_calls) + tool(tool_result) + assistant，共 7 条 |

事件转换的单测夹具**不在这里**，直接读仓库里已有的真实 SSE 原文：

- `docs/evidence/M0/t2-bash.sse` —— `/chat` 通道，含 `artifact_start/content/end` 三联（`/live` 上从未出现过这组事件）
- `docs/eval/raw/*.sse` —— M2 的 14 份 `/live` 真实流（A/B 正式评测的原始记录）
