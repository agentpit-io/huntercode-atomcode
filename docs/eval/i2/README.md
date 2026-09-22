# I2 评测批次说明

四个批次，**先基线后优化**。反过来的话万一中途出事，手里留下的是
「优化后的数据但没有基线」—— 那等于什么都没测到。

| 目录 | 阶段 | HCA 侧挂了什么 | 开关 |
|---|---|---|---|
| `baseline-b/` | I2 之前 | 9 个 MCP（无 `hcapack`） | `LLM_TOOL_DENY=""`、`ATOMCODE_AI_SESSION_NAMING=1`、`HCA_HOOKD=0` |
| `baseline-a/` | I2 之前 | **与社区版相同的 6 个**（去掉 `akshare` / `kronos` / `truesource`） | 同上 |
| `opt-b/` | I2 之后 | 10 个 MCP（含 `hcapack`） | 白名单开、会话起名关、常驻 hook 开 |
| `opt-a/` | I2 之后 | 与社区版相同的 6 个（另去掉 `hcapack`） | 同上 |

**两个阶段共用同一个镜像**，差异全在挂进去的工作区模板与上面几个开关上
（`deploy/eval/docker-compose.i2.yml` 的 `HCA_I2_TEMPLATE`）——
这样「优化前 vs 优化后」比的是改动本身，不是两次构建。

> ⚠️ A 线是「与社区版相同的 **6** 个 MCP」，不是 M2 §4.3 写的 4 个。
> 社区版除了 `.opencode/opencode.jsonc` 里那 4 个，工作区根下还有一份
> `opencode.json` 又注册了 `screener` 与 `hunter_cap`；权威口径是 opencode
> 合并后的 `GET /config`。取证 `docs/evidence/I2/社区版MCP清单-实测更正.md`。

## 每个批次怎么跑的

```bash
bash ~/hca/i2-batch.sh <baseline|opt> <full|lite> <输出目录名> 3
```

内部是 `tools/eval/run_ab.py --repeat 3`：5 题 × 2 边 × 3 轮 = 30 次，
**A/B 交错且每轮换先手**。D 维度是「两边中位数相比」的相对打分，
所以交错跑能把机器负载漂移对两边的影响抵掉 —— 这也是评分表当初
用相对打分而不是绝对阈值的原因。

每一次运行留三份记录：

* `<case>.json` —— 结构化全文 + `waterfall`（SSE 段落切分）
* `<case>.sse` / `<case>.raw.json` —— 原始流
* `<case>.shim.jsonl` —— **只有 HCA 侧有**：llm-shim 这一跳看到的每次上游请求
  （尺寸构成、首字与总耗时、网关回的 usage）。只记长度与工具名，不记正文。

## 怎么出分

```bash
python3 tools/eval/score.py prepare --raw docs/eval/i2/<批次> --out docs/eval/i2/<批次>/scores.json
python3 tools/eval/fill_manual.py --manual docs/eval/i2/<批次>/manual-scores.json \
                                  --scores docs/eval/i2/<批次>/scores.json
python3 tools/eval/make_report.py --scores ... --raw ... --out ...
python3 tools/eval/waterfall_report.py docs/eval/i2/<批次>   # 瀑布表
```

**D 维度（D1 步数 / D2 墙钟）是纯自动项**，从原始记录直接算，可复现。
A / C 里的人工项按 M2 同一套评分表打，人工项没填的那一次**整次不出分**
（不补默认值，`score.py` 会 rc=3）。

## 另外两份不走批次的实测

* `hook-bench/` —— hook 开销（一次性容器、每个 hook 预热 2 次后取 9 次中位数、4 次独立跑）
* `latency/` —— llm-shim 这一跳的净开销、以及输入 token 数对首字延迟的影响
