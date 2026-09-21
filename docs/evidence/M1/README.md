# M1 原始证据

> 全部来自测试机 hunter-test-01（34.133.8.3）上 compose 项目 `hca` 的真实运行，
> 2026-09-22（上海时间）。报告在 [`../../开发文档/M1-成果与测试报告.md`](../../开发文档/M1-成果与测试报告.md)。

| 文件 | 是什么 |
|---|---|
| `e2e/s*-*.sse` | 6 个技能验收用例的**完整 SSE 原始流**（`/live` 通道，含 snapshot / tool_start / tool_result / state） |
| `e2e/s*-*.json` | 对应的汇总（技能是否命中、调了哪些 MCP 工具与每次的 `duration_ms`、耗时、网关配额差值、权限请求） |
| `e2e/s*-*.err` | 探针 stderr（会话切换失败之类的提示） |
| `e2e-run4.log` | **最终一轮**跑批的控制台输出（报告 §7.2 那张表的来源） |
| `e2e-run3.log` | 上一轮跑批 —— 保留是因为它是 §7.3「切会话丢 MCP」的现场：模型改用 bash 自己爬数据、s6 连发 26 次 bash |
| `up-4.log` | 第 4 次 `deploy/up.sh` 的完整输出（构建 + 启动 + 自检） |
| `audit.jsonl` | hook 审计日志，106 条，每次工具调用一行（报告 §5.3） |

## 怎么自己复现

```bash
# 测试机上
cd ~/hca/repo
bash deploy/up.sh                       # 拉起（可重复执行）
bash deploy/up.sh --status              # 看 /health、/skills、/mcp/status

docker exec hca-daemon python3 /opt/hca/tools/dump_mcp_tools.py    # 9 个 MCP 直连自检
bash tools/probe/skill_e2e_all.sh ~/hca/e2e                        # 6 个技能跑批
```

单个用例：

```bash
docker exec hca-daemon python3 /opt/hca/tools/skill_e2e.py \
    --fresh --id 我的用例 --message "分析 002594 比亚迪的龙虎榜" --timeout 300
```

⚠️ 跑批会真实消耗网关配额（本轮 6 个用例合计 **1,183,261 token**）。
